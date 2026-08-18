# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "numpy>=2",
#   "pillow>=10",
#   "rosbags>=0.11.1",
# ]
# ///
"""Convert Livox ROS1 bag point clouds into a top-down occupancy grid.

The script reads Livox CustomMsg point clouds and a PoseStamped trajectory,
transforms local points into the pose frame, and writes a global occupancy map.
It is a data-preparation step; video encoding can consume the generated grid
image or optional frame sequence later.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

DEFAULT_MAX_GRID_CELLS = 25_000_000
DEFAULT_MAX_GRID_SIDE = 20_000


@dataclass(frozen=True)
class PoseSample:
    stamp_ns: int
    xyz: np.ndarray
    quat_xyzw: np.ndarray


@dataclass
class GridResult:
    occupied_cells: set[tuple[int, int]]
    used_lidar_frames: int
    skipped_lidar_frames: int
    used_points: int
    pose_samples: int
    first_lidar_stamp_ns: int | None
    last_lidar_stamp_ns: int | None
    max_pose_delta_ns: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Transform Livox point clouds by PoseStamped odometry and write a "
            "top-down occupancy grid."
        )
    )
    parser.add_argument("bag", type=Path, help="Input ROS1 bag.")
    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        default=Path("outputs") / "occupancy-grids",
        help="Directory for grid outputs.",
    )
    parser.add_argument("--lidar-topic", required=True)
    parser.add_argument("--pose-topic", required=True)
    parser.add_argument(
        "--resolution",
        type=float,
        default=0.3,
        help="Grid resolution in meters per cell.",
    )
    parser.add_argument(
        "--z-min",
        type=float,
        default=-2.0,
        help="Minimum transformed Z to keep, in meters.",
    )
    parser.add_argument(
        "--z-max",
        type=float,
        default=3.0,
        help="Maximum transformed Z to keep, in meters.",
    )
    parser.add_argument(
        "--range-max",
        type=float,
        default=80.0,
        help="Maximum local point range to keep, in meters. 0 disables.",
    )
    parser.add_argument(
        "--every-n-lidar",
        type=int,
        default=1,
        help="Use one lidar frame every N frames.",
    )
    parser.add_argument(
        "--max-lidar-frames",
        type=int,
        default=0,
        help="Stop after this many used lidar frames. 0 means no limit.",
    )
    parser.add_argument(
        "--max-pose-delta-ms",
        type=float,
        default=100.0,
        help="Skip lidar frames farther than this from the nearest pose sample.",
    )
    parser.add_argument(
        "--lidar-xyz",
        nargs=3,
        type=float,
        default=(0.0, 0.0, 0.0),
        metavar=("X", "Y", "Z"),
        help="LiDAR origin in the pose/body frame, meters.",
    )
    parser.add_argument(
        "--lidar-rpy-deg",
        nargs=3,
        type=float,
        default=(0.0, 0.0, 0.0),
        metavar=("ROLL", "PITCH", "YAW"),
        help="LiDAR-to-pose/body rotation, degrees.",
    )
    parser.add_argument(
        "--frame-stride",
        type=int,
        default=0,
        help="Write cumulative PNG frames every N used lidar frames. 0 disables.",
    )
    parser.add_argument(
        "--save-cells-csv",
        action="store_true",
        help="Write occupied cell coordinates as CSV.",
    )
    parser.add_argument(
        "--max-grid-cells",
        type=int,
        default=DEFAULT_MAX_GRID_CELLS,
        help="Refuse dense grids above this many cells.",
    )
    parser.add_argument(
        "--max-grid-side",
        type=int,
        default=DEFAULT_MAX_GRID_SIDE,
        help="Refuse grids wider or taller than this many cells.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace an existing run while preserving it as a backup directory.",
    )
    return parser.parse_args()


def load_reader():
    try:
        from rosbags.highlevel import AnyReader
    except ImportError as exc:
        raise SystemExit(
            "Missing dependency 'rosbags'. Run with: uv run scripts/livox-bag-to-occupancy-grid.py ..."
        ) from exc
    return AnyReader


def stamp_to_ns(stamp) -> int:
    return int(stamp.sec) * 1_000_000_000 + int(stamp.nanosec)


def msg_stamp_ns(msg, fallback_ns: int) -> int:
    header = getattr(msg, "header", None)
    stamp = getattr(header, "stamp", None)
    if stamp is None:
        return fallback_ns
    ns = stamp_to_ns(stamp)
    return ns if ns > 0 else fallback_ns


def quat_normalize(q: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(q))
    if norm <= 0:
        return np.array([0.0, 0.0, 0.0, 1.0], dtype=np.float64)
    return q / norm


def quat_from_rpy_deg(roll: float, pitch: float, yaw: float) -> np.ndarray:
    cr = math.cos(math.radians(roll) * 0.5)
    sr = math.sin(math.radians(roll) * 0.5)
    cp = math.cos(math.radians(pitch) * 0.5)
    sp = math.sin(math.radians(pitch) * 0.5)
    cy = math.cos(math.radians(yaw) * 0.5)
    sy = math.sin(math.radians(yaw) * 0.5)
    return quat_normalize(
        np.array(
            [
                sr * cp * cy - cr * sp * sy,
                cr * sp * cy + sr * cp * sy,
                cr * cp * sy - sr * sp * cy,
                cr * cp * cy + sr * sp * sy,
            ],
            dtype=np.float64,
        )
    )


def quat_slerp(q0: np.ndarray, q1: np.ndarray, ratio: float) -> np.ndarray:
    q0 = quat_normalize(q0)
    q1 = quat_normalize(q1)
    dot = float(np.dot(q0, q1))
    if dot < 0.0:
        q1 = -q1
        dot = -dot
    if dot > 0.9995:
        return quat_normalize(q0 + ratio * (q1 - q0))
    theta_0 = math.acos(max(-1.0, min(1.0, dot)))
    theta = theta_0 * ratio
    sin_theta = math.sin(theta)
    sin_theta_0 = math.sin(theta_0)
    s0 = math.cos(theta) - dot * sin_theta / sin_theta_0
    s1 = sin_theta / sin_theta_0
    return quat_normalize((s0 * q0) + (s1 * q1))


def quat_to_matrix(q: np.ndarray) -> np.ndarray:
    x, y, z, w = quat_normalize(q)
    return np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
        ],
        dtype=np.float64,
    )


def nearest_pose_index(stamps: np.ndarray, stamp_ns_value: int) -> int:
    index = int(np.searchsorted(stamps, stamp_ns_value))
    if index <= 0:
        return 0
    if index >= len(stamps):
        return len(stamps) - 1
    before = stamps[index - 1]
    after = stamps[index]
    return index - 1 if stamp_ns_value - before <= after - stamp_ns_value else index


def interpolate_pose(
    poses: list[PoseSample], stamps: np.ndarray, stamp_ns_value: int
) -> tuple[np.ndarray, np.ndarray, int]:
    if stamp_ns_value <= stamps[0]:
        pose = poses[0]
        return pose.xyz, pose.quat_xyzw, int(abs(stamp_ns_value - pose.stamp_ns))
    if stamp_ns_value >= stamps[-1]:
        pose = poses[-1]
        return pose.xyz, pose.quat_xyzw, int(abs(stamp_ns_value - pose.stamp_ns))
    right = int(np.searchsorted(stamps, stamp_ns_value))
    left = right - 1
    left_pose = poses[left]
    right_pose = poses[right]
    span = right_pose.stamp_ns - left_pose.stamp_ns
    ratio = 0.0 if span <= 0 else (stamp_ns_value - left_pose.stamp_ns) / span
    xyz = left_pose.xyz + ratio * (right_pose.xyz - left_pose.xyz)
    quat = quat_slerp(left_pose.quat_xyzw, right_pose.quat_xyzw, ratio)
    nearest = poses[nearest_pose_index(stamps, stamp_ns_value)]
    return xyz, quat, int(abs(stamp_ns_value - nearest.stamp_ns))


def collect_poses(AnyReader, bag: Path, pose_topic: str) -> list[PoseSample]:
    poses: list[PoseSample] = []
    with AnyReader([bag]) as reader:
        connections = [conn for conn in reader.connections if conn.topic == pose_topic]
        if not connections:
            return poses
        for conn, timestamp, rawdata in reader.messages(connections=connections):
            msg = reader.deserialize(rawdata, conn.msgtype)
            stamp_ns_value = msg_stamp_ns(msg, timestamp)
            position = msg.pose.position
            orientation = msg.pose.orientation
            poses.append(
                PoseSample(
                    stamp_ns=stamp_ns_value,
                    xyz=np.array([position.x, position.y, position.z], dtype=np.float64),
                    quat_xyzw=quat_normalize(
                        np.array(
                            [
                                orientation.x,
                                orientation.y,
                                orientation.z,
                                orientation.w,
                            ],
                            dtype=np.float64,
                        )
                    ),
                )
            )
    poses.sort(key=lambda pose: pose.stamp_ns)
    return poses


def livox_points_to_array(points: Iterable[object]) -> np.ndarray:
    return np.array([(p.x, p.y, p.z) for p in points], dtype=np.float32)


def safe_run_name(bag: Path) -> str:
    parent = bag.parent.name
    stem = bag.stem if parent == bag.stem else f"{parent}__{bag.stem}"
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", stem)


def render_grid(
    occupied_cells: set[tuple[int, int]],
    resolution: float,
    *,
    max_cells: int = DEFAULT_MAX_GRID_CELLS,
    max_side: int = DEFAULT_MAX_GRID_SIDE,
) -> tuple[Image.Image, dict[str, float | int]]:
    if not occupied_cells:
        image = Image.new("L", (1, 1), 255)
        return image, {
            "width_cells": 1,
            "height_cells": 1,
            "origin_x_m": 0.0,
            "origin_y_m": 0.0,
        }
    xs = [cell[0] for cell in occupied_cells]
    ys = [cell[1] for cell in occupied_cells]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    width = max_x - min_x + 1
    height = max_y - min_y + 1
    cell_count = width * height
    if width > max_side or height > max_side or cell_count > max_cells:
        raise ValueError(
            "refusing dense grid "
            f"{width}x{height} ({cell_count} cells); "
            f"limits are side={max_side}, cells={max_cells}"
        )
    grid = np.full((height, width), 255, dtype=np.uint8)
    for ix, iy in occupied_cells:
        row = max_y - iy
        col = ix - min_x
        grid[row, col] = 0
    return Image.fromarray(grid, mode="L"), {
        "width_cells": width,
        "height_cells": height,
        "origin_x_m": min_x * resolution,
        "origin_y_m": min_y * resolution,
        "max_x_m": (max_x + 1) * resolution,
        "max_y_m": (max_y + 1) * resolution,
    }


def write_frame(
    frames_dir: Path,
    frame_index: int,
    occupied_cells: set[tuple[int, int]],
    resolution: float,
    max_cells: int,
    max_side: int,
) -> None:
    frames_dir.mkdir(parents=True, exist_ok=True)
    image, _meta = render_grid(
        occupied_cells,
        resolution,
        max_cells=max_cells,
        max_side=max_side,
    )
    image.save(frames_dir / f"grid_{frame_index:06d}.png")


def process_bag(args: argparse.Namespace, out_dir: Path) -> GridResult:
    AnyReader = load_reader()
    poses = collect_poses(AnyReader, args.bag, args.pose_topic)
    if not poses:
        raise RuntimeError(f"Pose topic not found or empty: {args.pose_topic}")

    stamps = np.array([pose.stamp_ns for pose in poses], dtype=np.int64)
    max_pose_delta_ns = int(args.max_pose_delta_ms * 1_000_000)
    lidar_translation = np.array(args.lidar_xyz, dtype=np.float64)
    lidar_rotation = quat_to_matrix(quat_from_rpy_deg(*args.lidar_rpy_deg))

    occupied_cells: set[tuple[int, int]] = set()
    used_lidar_frames = 0
    skipped_lidar_frames = 0
    used_points = 0
    seen_lidar_frames = 0
    first_lidar_stamp_ns: int | None = None
    last_lidar_stamp_ns: int | None = None
    worst_pose_delta_ns = 0
    frames_dir = out_dir / "frames" if args.frame_stride > 0 else None

    with AnyReader([args.bag]) as reader:
        connections = [conn for conn in reader.connections if conn.topic == args.lidar_topic]
        if not connections:
            raise RuntimeError(f"LiDAR topic not found: {args.lidar_topic}")
        for conn, timestamp, rawdata in reader.messages(connections=connections):
            seen_lidar_frames += 1
            if args.every_n_lidar < 1:
                raise ValueError("--every-n-lidar must be >= 1")
            if (seen_lidar_frames - 1) % args.every_n_lidar != 0:
                continue
            if args.max_lidar_frames and used_lidar_frames >= args.max_lidar_frames:
                break

            msg = reader.deserialize(rawdata, conn.msgtype)
            lidar_stamp = msg_stamp_ns(msg, timestamp)
            pose_xyz, pose_quat, pose_delta_ns = interpolate_pose(poses, stamps, lidar_stamp)
            worst_pose_delta_ns = max(worst_pose_delta_ns, pose_delta_ns)
            if pose_delta_ns > max_pose_delta_ns:
                skipped_lidar_frames += 1
                continue

            local_points = livox_points_to_array(msg.points)
            if local_points.size == 0:
                skipped_lidar_frames += 1
                continue

            finite = np.isfinite(local_points).all(axis=1)
            nonzero = np.linalg.norm(local_points, axis=1) > 1e-6
            mask = finite & nonzero
            if args.range_max > 0:
                mask &= np.linalg.norm(local_points, axis=1) <= args.range_max
            local_points = local_points[mask]
            if local_points.size == 0:
                skipped_lidar_frames += 1
                continue

            body_points = local_points @ lidar_rotation.T + lidar_translation
            pose_rotation = quat_to_matrix(pose_quat)
            global_points = body_points @ pose_rotation.T + pose_xyz
            height_mask = (global_points[:, 2] >= args.z_min) & (global_points[:, 2] <= args.z_max)
            global_points = global_points[height_mask]
            if global_points.size == 0:
                skipped_lidar_frames += 1
                continue

            cells = np.floor(global_points[:, :2] / args.resolution).astype(np.int64)
            for ix, iy in np.unique(cells, axis=0):
                occupied_cells.add((int(ix), int(iy)))

            used_points += int(global_points.shape[0])
            used_lidar_frames += 1
            first_lidar_stamp_ns = (
                lidar_stamp if first_lidar_stamp_ns is None else first_lidar_stamp_ns
            )
            last_lidar_stamp_ns = lidar_stamp
            if frames_dir is not None and used_lidar_frames % args.frame_stride == 0:
                write_frame(
                    frames_dir,
                    used_lidar_frames,
                    occupied_cells,
                    args.resolution,
                    args.max_grid_cells,
                    args.max_grid_side,
                )

    return GridResult(
        occupied_cells=occupied_cells,
        used_lidar_frames=used_lidar_frames,
        skipped_lidar_frames=skipped_lidar_frames,
        used_points=used_points,
        pose_samples=len(poses),
        first_lidar_stamp_ns=first_lidar_stamp_ns,
        last_lidar_stamp_ns=last_lidar_stamp_ns,
        max_pose_delta_ns=worst_pose_delta_ns,
    )


def write_cells_csv(path: Path, occupied_cells: set[tuple[int, int]], resolution: float) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["ix", "iy", "x_m", "y_m"])
        writer.writeheader()
        for ix, iy in sorted(occupied_cells):
            writer.writerow(
                {
                    "ix": ix,
                    "iy": iy,
                    "x_m": f"{ix * resolution:.6f}",
                    "y_m": f"{iy * resolution:.6f}",
                }
            )


def main() -> int:
    args = parse_args()
    if args.resolution <= 0:
        print("--resolution must be > 0", file=sys.stderr)
        return 2
    if args.max_grid_cells <= 0 or args.max_grid_side <= 0:
        print("--max-grid-cells and --max-grid-side must be > 0", file=sys.stderr)
        return 2
    if not args.bag.exists():
        print(f"Bag not found: {args.bag}", file=sys.stderr)
        return 2

    run_name = safe_run_name(args.bag)
    final_dir = args.output_dir / run_name
    if final_dir.exists() and not args.overwrite:
        print(f"Output directory exists, pass --overwrite: {final_dir}", file=sys.stderr)
        return 2
    args.output_dir.mkdir(parents=True, exist_ok=True)
    staging_dir = args.output_dir / f".{run_name}.staging-{uuid.uuid4().hex[:8]}"
    staging_dir.mkdir()

    result = process_bag(args, staging_dir)
    image, grid_meta = render_grid(
        result.occupied_cells,
        args.resolution,
        max_cells=args.max_grid_cells,
        max_side=args.max_grid_side,
    )
    image_path = staging_dir / "occupancy_grid.png"
    image.save(image_path)

    metadata = {
        "bag": args.bag.name,
        "lidar_topic": args.lidar_topic,
        "pose_topic": args.pose_topic,
        "resolution_m": args.resolution,
        "z_min_m": args.z_min,
        "z_max_m": args.z_max,
        "range_max_m": args.range_max,
        "max_grid_cells": args.max_grid_cells,
        "max_grid_side": args.max_grid_side,
        "lidar_xyz_m": list(args.lidar_xyz),
        "lidar_rpy_deg": list(args.lidar_rpy_deg),
        "pose_samples": result.pose_samples,
        "used_lidar_frames": result.used_lidar_frames,
        "skipped_lidar_frames": result.skipped_lidar_frames,
        "used_points": result.used_points,
        "occupied_cells": len(result.occupied_cells),
        "first_lidar_stamp_ns": result.first_lidar_stamp_ns,
        "last_lidar_stamp_ns": result.last_lidar_stamp_ns,
        "max_pose_delta_ms": result.max_pose_delta_ns / 1_000_000.0,
        **grid_meta,
    }
    metadata_path = staging_dir / "metadata.json"
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if args.save_cells_csv:
        write_cells_csv(staging_dir / "occupied_cells.csv", result.occupied_cells, args.resolution)

    backup_dir = None
    if final_dir.exists():
        backup_dir = args.output_dir / f".{run_name}.backup-{uuid.uuid4().hex[:8]}"
        final_dir.rename(backup_dir)
    try:
        staging_dir.rename(final_dir)
    except Exception:
        if backup_dir is not None and not final_dir.exists():
            backup_dir.rename(final_dir)
        raise

    print(f"OUTPUT_DIR={final_dir}")
    print(f"OCCUPANCY_GRID={final_dir / image_path.name}")
    print(f"METADATA={final_dir / metadata_path.name}")
    if backup_dir is not None:
        print(f"PREVIOUS_OUTPUT_BACKUP={backup_dir}")
    print(f"USED_LIDAR_FRAMES={result.used_lidar_frames}")
    print(f"USED_POINTS={result.used_points}")
    print(f"OCCUPIED_CELLS={len(result.occupied_cells)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
