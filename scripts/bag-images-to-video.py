# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "rosbags>=0.11.1",
# ]
# ///
"""Export compressed image messages from ROS bag files to review videos.

The script intentionally does not require a local ROS installation. It reads
ROS1/ROS2 bags with rosbags and sends encoded JPEG/PNG frames to ffmpeg.
"""

from __future__ import annotations

import argparse
import csv
import re
import shutil
import sqlite3
import struct
import subprocess
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class BagJob:
    bag: Path
    output: Path


@dataclass(frozen=True)
class ExportResult:
    bag: Path
    output: Path
    topic: str
    frames: int
    fps: float
    status: str
    message: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Convert sensor_msgs/CompressedImage messages in ROS bag files to MP4 review videos."
        )
    )
    parser.add_argument(
        "inputs",
        nargs="+",
        type=Path,
        help=(
            "ROS1 .bag files, ROS2 bag directories, or directories to search. "
            "Search directories are scanned recursively for *.bag and ROS2 metadata.yaml."
        ),
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        default=Path("outputs") / "bag-video-preview",
        help="Directory for generated videos and manifest.",
    )
    parser.add_argument(
        "--topic",
        required=True,
        help="Compressed image topic to export.",
    )
    parser.add_argument(
        "--fps",
        default="20",
        help="Output FPS, or 'auto' to estimate from bag timestamps.",
    )
    parser.add_argument(
        "--every-n",
        type=int,
        default=1,
        help="Export one frame every N image messages.",
    )
    parser.add_argument(
        "--max-frames",
        type=int,
        default=0,
        help="Stop after this many exported frames per bag. 0 means no limit.",
    )
    parser.add_argument(
        "--start-offset-sec",
        type=float,
        default=0.0,
        help="Start exporting this many seconds after the first image-topic frame.",
    )
    parser.add_argument(
        "--duration-sec",
        type=float,
        default=0.0,
        help="Export this many seconds after --start-offset-sec. 0 means until bag end.",
    )
    parser.add_argument(
        "--ffmpeg",
        type=Path,
        default=None,
        help="Path to ffmpeg executable. Defaults to ffmpeg on PATH.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing output videos.",
    )
    parser.add_argument(
        "--crf",
        type=int,
        default=20,
        help="x264 CRF quality value. Lower is higher quality.",
    )
    return parser.parse_args()


def find_bags(inputs: Iterable[Path]) -> list[Path]:
    bags: list[Path] = []
    for item in inputs:
        item = item.expanduser()
        if item.is_file():
            if item.suffix.lower() == ".bag":
                bags.append(item.resolve())
            continue
        if item.is_dir():
            if (item / "metadata.yaml").is_file():
                bags.append(item.resolve())
            for bag in item.rglob("*.bag"):
                if bag.name.startswith("."):
                    continue
                if ".repair-" in bag.name:
                    continue
                bags.append(bag.resolve())
            for metadata in item.rglob("metadata.yaml"):
                bags.append(metadata.parent.resolve())
    return sorted(set(bags), key=lambda p: str(p).lower())


def safe_output_name(bag: Path) -> str:
    stem = bag.stem
    parent = bag.parent.name
    if parent and parent != stem and parent != "reindexed-bags":
        stem = f"{parent}__{stem}"
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", stem) + ".mp4"


def build_jobs(bags: list[Path], output_dir: Path, overwrite: bool) -> list[BagJob]:
    output_dir.mkdir(parents=True, exist_ok=True)
    jobs: list[BagJob] = []
    planned_outputs: dict[str, Path] = {}
    for bag in bags:
        out = output_dir / safe_output_name(bag)
        output_key = out.name.casefold()
        previous_bag = planned_outputs.get(output_key)
        if previous_bag is not None:
            raise ValueError(f"multiple inputs map to {out.name}: {previous_bag.name}, {bag.name}")
        planned_outputs[output_key] = bag
        if out.exists() and not overwrite:
            print(f"SKIP_EXISTS={out}", file=sys.stderr)
            continue
        jobs.append(BagJob(bag=bag, output=out))
    return jobs


def is_ros2_sqlite_bag(path: Path) -> bool:
    return path.is_dir() and (path / "metadata.yaml").is_file() and any(path.glob("*.db3"))


def parse_ros2_compressed_image(raw: bytes) -> bytes:
    """Extract sensor_msgs/msg/CompressedImage.data from ROS2 CDR bytes."""
    if len(raw) < 28:
        raise ValueError("CompressedImage payload is too short")

    endian = "<"
    offset = 4  # CDR encapsulation.
    offset += 8  # builtin_interfaces/Time stamp.

    frame_id_len = struct.unpack_from(f"{endian}I", raw, offset)[0]
    offset += 4 + frame_id_len
    offset = (offset + 3) & ~3

    format_len = struct.unpack_from(f"{endian}I", raw, offset)[0]
    offset += 4 + format_len
    offset = (offset + 3) & ~3

    data_len = struct.unpack_from(f"{endian}I", raw, offset)[0]
    offset += 4
    end = offset + data_len
    if end > len(raw):
        raise ValueError("CompressedImage data length exceeds payload size")
    return raw[offset:end]


def iter_ros2_sqlite_timestamps(bag: Path, topic: str):
    db_paths = sorted(bag.glob("*.db3"), key=lambda path: path.name)
    for db_path in db_paths:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as db:
            row = db.execute("SELECT id FROM topics WHERE name = ?", (topic,)).fetchone()
            if row is None:
                continue
            topic_id = int(row[0])
            cursor = db.execute(
                "SELECT timestamp FROM messages WHERE topic_id = ? ORDER BY timestamp",
                (topic_id,),
            )
            for (timestamp,) in cursor:
                yield int(timestamp)


def iter_ros2_sqlite_images(
    bag: Path,
    topic: str,
    every_n: int = 1,
    start_ns: int = 0,
    end_ns: int | None = None,
):
    db_paths = sorted(bag.glob("*.db3"), key=lambda path: path.name)
    reference_timestamp: int | None = None
    seen = 0
    for db_path in db_paths:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as db:
            row = db.execute("SELECT id FROM topics WHERE name = ?", (topic,)).fetchone()
            if row is None:
                continue
            topic_id = int(row[0])
            cursor = db.execute(
                "SELECT id, timestamp FROM messages WHERE topic_id = ? ORDER BY timestamp",
                (topic_id,),
            )
            for message_id, timestamp in cursor:
                timestamp = int(timestamp)
                if reference_timestamp is None:
                    reference_timestamp = timestamp
                rel_ns = timestamp - reference_timestamp
                if rel_ns < start_ns:
                    continue
                if end_ns is not None and rel_ns >= end_ns:
                    return
                seen += 1
                if seen % every_n != 0:
                    continue
                data = db.execute(
                    "SELECT data FROM messages WHERE id = ?",
                    (int(message_id),),
                ).fetchone()[0]
                yield timestamp, parse_ros2_compressed_image(bytes(data))


def estimate_fps_from_timestamps(timestamps: list[int]) -> float | None:
    if len(timestamps) < 2:
        return None
    seconds = (timestamps[-1] - timestamps[0]) / 1_000_000_000.0
    if seconds <= 0:
        return None
    fps = (len(timestamps) - 1) / seconds
    return max(1.0, min(60.0, fps))


def find_ffmpeg(explicit: Path | None) -> Path:
    if explicit is not None:
        if explicit.exists():
            return explicit
        raise FileNotFoundError(f"ffmpeg not found: {explicit}")

    found = shutil.which("ffmpeg")
    if found:
        return Path(found)

    raise FileNotFoundError("ffmpeg was not found. Pass --ffmpeg <path>.")


def load_reader():
    try:
        from rosbags.highlevel import AnyReader
        from rosbags.typesys import Stores, get_typestore
    except ImportError as exc:
        raise SystemExit(
            "Missing dependency 'rosbags'. Run with: uv run scripts/bag-images-to-video.py ..."
        ) from exc
    return AnyReader, get_typestore(Stores.ROS2_FOXY)


def estimate_fps(AnyReader, typestore, bag: Path, topic: str, every_n: int) -> float | None:
    if is_ros2_sqlite_bag(bag):
        timestamps: list[int] = []
        for index, timestamp in enumerate(iter_ros2_sqlite_timestamps(bag, topic), start=1):
            if index % every_n == 0:
                timestamps.append(timestamp)
            if len(timestamps) >= 300:
                break
        return estimate_fps_from_timestamps(timestamps)

    timestamps: list[int] = []
    with AnyReader([bag], default_typestore=typestore) as reader:
        connections = [conn for conn in reader.connections if conn.topic == topic]
        if not connections:
            return None
        for index, (_conn, timestamp, _raw) in enumerate(
            reader.messages(connections=connections), start=1
        ):
            if index % every_n == 0:
                timestamps.append(timestamp)
            if len(timestamps) >= 300:
                break
    return estimate_fps_from_timestamps(timestamps)


def frame_codec(frame: bytes) -> str:
    if frame.startswith(b"\xff\xd8"):
        return "mjpeg"
    if frame.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    return "mjpeg"


def first_frame(AnyReader, typestore, bag: Path, topic: str) -> bytes | None:
    if is_ros2_sqlite_bag(bag):
        for _timestamp, frame in iter_ros2_sqlite_images(bag, topic):
            return frame
        return None

    with AnyReader([bag], default_typestore=typestore) as reader:
        connections = [conn for conn in reader.connections if conn.topic == topic]
        if not connections:
            return None
        for conn, _timestamp, rawdata in reader.messages(connections=connections):
            msg = reader.deserialize(rawdata, conn.msgtype)
            return bytes(msg.data)
    return None


def export_bag(
    AnyReader,
    typestore,
    bag: Path,
    output: Path,
    topic: str,
    fps_arg: str,
    every_n: int,
    max_frames: int,
    start_offset_sec: float,
    duration_sec: float,
    ffmpeg: Path,
    crf: int,
) -> ExportResult:
    if every_n < 1:
        raise ValueError("--every-n must be >= 1")
    if start_offset_sec < 0:
        raise ValueError("--start-offset-sec must be >= 0")
    if duration_sec < 0:
        raise ValueError("--duration-sec must be >= 0")

    preview = first_frame(AnyReader, typestore, bag, topic)
    if preview is None:
        return ExportResult(bag, output, topic, 0, 0.0, "no_topic", "topic not found")

    if fps_arg.lower() == "auto":
        fps = estimate_fps(AnyReader, typestore, bag, topic, every_n) or 20.0
    else:
        fps = float(fps_arg)

    output.parent.mkdir(parents=True, exist_ok=True)
    staging_output = output.with_name(f".{output.stem}.partial{output.suffix}")
    staging_output.unlink(missing_ok=True)
    codec = frame_codec(preview)
    cmd = [
        str(ffmpeg),
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "image2pipe",
        "-framerate",
        f"{fps:.6g}",
        "-vcodec",
        codec,
        "-i",
        "pipe:0",
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-pix_fmt",
        "yuv420p",
        "-crf",
        str(crf),
        str(staging_output),
    ]

    frames = 0
    seen = 0
    process = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    assert process.stdin is not None
    reference_timestamp: int | None = None
    start_ns = int(round(start_offset_sec * 1_000_000_000))
    end_ns = start_ns + int(round(duration_sec * 1_000_000_000)) if duration_sec > 0 else None

    processing_error: Exception | None = None
    try:
        if is_ros2_sqlite_bag(bag):
            for _timestamp, frame in iter_ros2_sqlite_images(
                bag,
                topic,
                every_n=every_n,
                start_ns=start_ns,
                end_ns=end_ns,
            ):
                process.stdin.write(frame)
                frames += 1
                if max_frames and frames >= max_frames:
                    break
        else:
            with AnyReader([bag], default_typestore=typestore) as reader:
                connections = [conn for conn in reader.connections if conn.topic == topic]
                for conn, _timestamp, rawdata in reader.messages(connections=connections):
                    timestamp = int(_timestamp)
                    if reference_timestamp is None:
                        reference_timestamp = timestamp
                    rel_ns = timestamp - reference_timestamp
                    if rel_ns < start_ns:
                        continue
                    if end_ns is not None and rel_ns >= end_ns:
                        break
                    seen += 1
                    if seen % every_n != 0:
                        continue
                    msg = reader.deserialize(rawdata, conn.msgtype)
                    process.stdin.write(bytes(msg.data))
                    frames += 1
                    if max_frames and frames >= max_frames:
                        break
    except Exception as exc:  # cleanup and report after ffmpeg exits.
        processing_error = exc
    finally:
        try:
            process.stdin.close()
        except OSError:
            pass

    return_code = process.wait()
    if processing_error is not None:
        staging_output.unlink(missing_ok=True)
        if isinstance(processing_error, BrokenPipeError):
            raise RuntimeError("ffmpeg stopped while reading frames") from processing_error
        raise processing_error
    if return_code != 0:
        staging_output.unlink(missing_ok=True)
        return ExportResult(
            bag, output, topic, frames, fps, "ffmpeg_failed", f"ffmpeg exit {return_code}"
        )
    if frames == 0:
        staging_output.unlink(missing_ok=True)
        return ExportResult(bag, output, topic, 0, fps, "no_frames", "no frames exported")
    staging_output.replace(output)
    return ExportResult(bag, output, topic, frames, fps, "ok", "")


def write_manifest(path: Path, results: list[ExportResult]) -> None:
    staging = path.with_name(f".{path.name}.tmp")
    with staging.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "bag",
                "output",
                "topic",
                "frames",
                "fps",
                "status",
                "message",
            ],
        )
        writer.writeheader()
        for result in results:
            writer.writerow(
                {
                    "bag": result.bag.name,
                    "output": result.output.name,
                    "topic": result.topic,
                    "frames": result.frames,
                    "fps": f"{result.fps:.6g}",
                    "status": result.status,
                    "message": result.message,
                }
            )
    staging.replace(path)


def main() -> int:
    args = parse_args()
    bags = find_bags(args.inputs)
    if not bags:
        print("No .bag files found.", file=sys.stderr)
        return 2

    ffmpeg = find_ffmpeg(args.ffmpeg)
    AnyReader, typestore = load_reader()
    manifest = args.output_dir / "manifest.csv"
    if manifest.exists() and not args.overwrite:
        print(f"Manifest exists, pass --overwrite: {manifest}", file=sys.stderr)
        return 2
    try:
        jobs = build_jobs(bags, args.output_dir, args.overwrite)
    except ValueError as exc:
        print(f"Invalid batch: {exc}", file=sys.stderr)
        return 2
    if not jobs:
        print("No jobs to run.", file=sys.stderr)
        return 0

    results: list[ExportResult] = []
    for index, job in enumerate(jobs, start=1):
        print(f"[{index}/{len(jobs)}] {job.bag.name} -> {job.output.name}", flush=True)
        try:
            result = export_bag(
                AnyReader=AnyReader,
                typestore=typestore,
                bag=job.bag,
                output=job.output,
                topic=args.topic,
                fps_arg=args.fps,
                every_n=args.every_n,
                max_frames=args.max_frames,
                start_offset_sec=args.start_offset_sec,
                duration_sec=args.duration_sec,
                ffmpeg=ffmpeg,
                crf=args.crf,
            )
        except Exception as exc:  # noqa: BLE001 - keep batch conversion moving.
            print(f"ERROR={job.bag.name}: {type(exc).__name__}: {exc}", file=sys.stderr)
            result = ExportResult(
                job.bag,
                job.output,
                args.topic,
                0,
                0.0,
                "error",
                f"{type(exc).__name__}: processing failed",
            )
        print(f"STATUS={result.status} FRAMES={result.frames} FPS={result.fps:.3f}")
        if result.message:
            print(f"MESSAGE={result.message}")
        results.append(result)

    write_manifest(manifest, results)
    print(f"MANIFEST={manifest.name}")
    failed = [result for result in results if result.status != "ok"]
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
