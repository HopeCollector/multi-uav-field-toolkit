# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "rosbags>=0.11.1",
# ]
# ///
"""Inspect ROS1 bag topics for point cloud and odometry data.

This script is intentionally local and read-only. It does not require a ROS
installation; rosbags parses the bag files directly.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

ODOM_TYPE_PATTERNS = (
    "nav_msgs/msg/Odometry",
    "geometry_msgs/msg/PoseStamped",
    "geometry_msgs/msg/PoseWithCovarianceStamped",
)
ODOM_TOPIC_PATTERNS = (
    re.compile(r"odom", re.IGNORECASE),
    re.compile(r"local_position", re.IGNORECASE),
    re.compile(r"vision_pose", re.IGNORECASE),
    re.compile(r"/tf$", re.IGNORECASE),
)
POINT_TYPE_PATTERNS = (
    "sensor_msgs/msg/PointCloud2",
    "livox_ros_driver2/msg/CustomMsg",
)


@dataclass(frozen=True)
class TopicRow:
    bag: Path
    topic: str
    msgtype: str
    count: int
    role: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Inspect bag topics and identify odometry/point-cloud candidates."
    )
    parser.add_argument(
        "inputs",
        nargs="+",
        type=Path,
        help="Bag files or directories. Directories are searched recursively for *.bag.",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("outputs") / "bag-topic-inspection" / "topics.csv",
        help="CSV output path.",
    )
    parser.add_argument(
        "--include-all",
        action="store_true",
        help="Write all topics instead of only likely odometry, point-cloud, and TF topics.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace an existing CSV output.",
    )
    return parser.parse_args()


def load_reader():
    try:
        from rosbags.highlevel import AnyReader
    except ImportError as exc:
        raise SystemExit(
            "Missing dependency 'rosbags'. Run with: uv run scripts/inspect-bag-topics.py ..."
        ) from exc
    return AnyReader


def find_bags(inputs: Iterable[Path]) -> list[Path]:
    bags: list[Path] = []
    for item in inputs:
        item = item.expanduser()
        if item.is_file() and item.suffix.lower() == ".bag":
            bags.append(item.resolve())
        elif item.is_dir():
            for bag in item.rglob("*.bag"):
                if bag.name.startswith(".") or ".repair-" in bag.name:
                    continue
                bags.append(bag.resolve())
    return sorted(set(bags), key=lambda p: str(p).lower())


def classify_topic(topic: str, msgtype: str) -> str:
    roles: list[str] = []
    if msgtype in POINT_TYPE_PATTERNS:
        roles.append("point_cloud")
    if msgtype in ODOM_TYPE_PATTERNS or any(
        pattern.search(topic) for pattern in ODOM_TOPIC_PATTERNS
    ):
        roles.append("odometry_candidate")
    if topic in {"/tf", "/tf_static"}:
        roles.append("tf")
    if msgtype.endswith("/Imu"):
        roles.append("imu")
    if msgtype.endswith("/CompressedImage"):
        roles.append("image")
    return "+".join(roles) if roles else ""


def inspect_bag(AnyReader, bag: Path, include_all: bool) -> list[TopicRow]:
    rows: list[TopicRow] = []
    with AnyReader([bag]) as reader:
        for conn in reader.connections:
            role = classify_topic(conn.topic, conn.msgtype)
            if include_all or role:
                rows.append(
                    TopicRow(
                        bag=bag,
                        topic=conn.topic,
                        msgtype=conn.msgtype,
                        count=conn.msgcount,
                        role=role,
                    )
                )
    return sorted(rows, key=lambda row: (row.topic, row.msgtype))


def write_csv(path: Path, rows: list[TopicRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_name(f".{path.name}.tmp")
    with staging.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["bag", "topic", "msgtype", "count", "role"],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "bag": row.bag.name,
                    "topic": row.topic,
                    "msgtype": row.msgtype,
                    "count": row.count,
                    "role": row.role,
                }
            )
    staging.replace(path)


def validate_output_path(output: Path, bags: list[Path]) -> None:
    output_path = output.expanduser().resolve()
    if output_path in {bag.expanduser().resolve() for bag in bags}:
        raise ValueError("CSV output must not replace a source bag")


def main() -> int:
    args = parse_args()
    if args.output.exists() and not args.overwrite:
        print(f"Output exists, pass --overwrite: {args.output}", file=sys.stderr)
        return 2
    bags = find_bags(args.inputs)
    if not bags:
        print("No .bag files found.", file=sys.stderr)
        return 2
    try:
        validate_output_path(args.output, bags)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    AnyReader = load_reader()
    all_rows: list[TopicRow] = []
    failed = 0
    for index, bag in enumerate(bags, start=1):
        print(f"[{index}/{len(bags)}] {bag.name}", flush=True)
        try:
            rows = inspect_bag(AnyReader, bag, args.include_all)
        except Exception as exc:  # noqa: BLE001 - continue batch inspection.
            failed += 1
            print(f"ERROR={bag.name}: {type(exc).__name__}: {exc}", file=sys.stderr)
            continue
        point_clouds = [row.topic for row in rows if "point_cloud" in row.role]
        odom = [row.topic for row in rows if "odometry_candidate" in row.role]
        print(f"POINT_CLOUD_TOPICS={','.join(point_clouds) if point_clouds else 'none'}")
        print(f"ODOMETRY_CANDIDATES={','.join(odom) if odom else 'none'}")
        all_rows.extend(rows)

    write_csv(args.output, all_rows)
    print(f"TOPIC_CSV={args.output}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
