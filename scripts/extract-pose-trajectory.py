# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "rosbags>=0.11.1",
# ]
# ///
"""Extract a PoseStamped trajectory CSV from a ROS bag."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract PoseStamped trajectory samples from a ROS bag."
    )
    parser.add_argument("bag", type=Path)
    parser.add_argument("-o", "--output", required=True, type=Path)
    parser.add_argument("--metadata", type=Path, default=None)
    parser.add_argument("--pose-topic", required=True)
    parser.add_argument(
        "--reference-topic",
        default="",
        help="Topic whose first stamp defines t=0. Defaults to --pose-topic.",
    )
    parser.add_argument("--start-offset-sec", type=float, default=0.0)
    parser.add_argument("--duration-sec", type=float, default=0.0, help="0 means until bag end.")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace existing CSV and metadata outputs.",
    )
    return parser.parse_args()


def load_reader():
    try:
        from rosbags.highlevel import AnyReader
    except ImportError as exc:
        raise SystemExit(
            "Missing dependency 'rosbags'. Run with: uv run scripts/extract-pose-trajectory.py ..."
        ) from exc
    return AnyReader


def stamp_to_ns(stamp) -> int:
    return int(stamp.sec) * 1_000_000_000 + int(stamp.nanosec)


def msg_stamp_info(msg, fallback_ns: int) -> tuple[int, str]:
    header = getattr(msg, "header", None)
    stamp = getattr(header, "stamp", None)
    if stamp is None:
        return fallback_ns, "bag_timestamp"
    ns = stamp_to_ns(stamp)
    if ns > 0:
        return ns, "header_stamp"
    return fallback_ns, "bag_timestamp"


def msg_stamp_ns(msg, fallback_ns: int) -> int:
    return msg_stamp_info(msg, fallback_ns)[0]


def first_topic_stamp_ns(AnyReader, bag: Path, topic: str) -> int | None:
    if not topic:
        return None
    with AnyReader([bag]) as reader:
        connections = [conn for conn in reader.connections if conn.topic == topic]
        for conn, timestamp, rawdata in reader.messages(connections=connections):
            msg = reader.deserialize(rawdata, conn.msgtype)
            return msg_stamp_ns(msg, timestamp)
    return None


def validate_output_paths(bag: Path, output: Path, metadata: Path) -> None:
    bag_path = bag.expanduser().resolve()
    output_path = output.expanduser().resolve()
    metadata_path = metadata.expanduser().resolve()
    if output_path == metadata_path:
        raise ValueError("--output and --metadata must be different files")
    if bag_path in {output_path, metadata_path}:
        raise ValueError("output files must not replace the source bag")


def main() -> int:
    args = parse_args()
    if not args.bag.exists():
        raise SystemExit(f"Bag not found: {args.bag}")

    metadata_path = args.metadata or args.output.with_suffix(".metadata.json")
    try:
        validate_output_paths(args.bag, args.output, metadata_path)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    existing = [path for path in (args.output, metadata_path) if path.exists()]
    if existing and not args.overwrite:
        names = ", ".join(path.name for path in existing)
        raise SystemExit(f"Output exists, pass --overwrite: {names}")

    AnyReader = load_reader()
    reference_topic = args.reference_topic or args.pose_topic
    reference_stamp = first_topic_stamp_ns(AnyReader, args.bag, reference_topic)
    if reference_stamp is None:
        raise SystemExit(f"Reference topic not found: {reference_topic}")
    start_ns = reference_stamp + int(round(args.start_offset_sec * 1_000_000_000))
    end_ns = (
        None if args.duration_sec <= 0 else start_ns + int(round(args.duration_sec * 1_000_000_000))
    )

    rows: list[dict[str, object]] = []
    with AnyReader([args.bag]) as reader:
        connections = [conn for conn in reader.connections if conn.topic == args.pose_topic]
        if not connections:
            raise SystemExit(f"Pose topic not found: {args.pose_topic}")
        for conn, timestamp, rawdata in reader.messages(connections=connections):
            msg = reader.deserialize(rawdata, conn.msgtype)
            stamp_ns, timestamp_source = msg_stamp_info(msg, timestamp)
            if stamp_ns < start_ns:
                continue
            if end_ns is not None and stamp_ns >= end_ns:
                break
            position = msg.pose.position
            orientation = msg.pose.orientation
            frame_id = str(getattr(msg.header, "frame_id", ""))
            rows.append(
                {
                    "stamp_ns": stamp_ns,
                    "timestamp_source": timestamp_source,
                    "frame_id": frame_id,
                    "t_sec": f"{(stamp_ns - start_ns) / 1_000_000_000.0:.9f}",
                    "x": f"{float(position.x):.9f}",
                    "y": f"{float(position.y):.9f}",
                    "z": f"{float(position.z):.9f}",
                    "qx": f"{float(orientation.x):.12g}",
                    "qy": f"{float(orientation.y):.12g}",
                    "qz": f"{float(orientation.z):.12g}",
                    "qw": f"{float(orientation.w):.12g}",
                }
            )

    if not rows:
        raise SystemExit("No pose samples in requested window.")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    csv_staging = args.output.with_name(f".{args.output.name}.tmp")
    with csv_staging.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "stamp_ns",
                "timestamp_source",
                "frame_id",
                "t_sec",
                "x",
                "y",
                "z",
                "qx",
                "qy",
                "qz",
                "qw",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)
    csv_staging.replace(args.output)

    metadata = {
        "bag": args.bag.name,
        "bag_size_bytes": args.bag.stat().st_size,
        "pose_topic": args.pose_topic,
        "reference_topic": reference_topic,
        "reference_stamp_ns": reference_stamp,
        "start_offset_sec": args.start_offset_sec,
        "duration_sec": args.duration_sec,
        "start_stamp_ns": start_ns,
        "end_stamp_ns": end_ns,
        "samples": len(rows),
        "first_stamp_ns": rows[0]["stamp_ns"],
        "last_stamp_ns": rows[-1]["stamp_ns"],
        "frame_ids": sorted({str(row["frame_id"]) for row in rows}),
        "timestamp_sources": sorted({str(row["timestamp_source"]) for row in rows}),
        "output": args.output.name,
    }
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_staging = metadata_path.with_name(f".{metadata_path.name}.tmp")
    metadata_staging.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    metadata_staging.replace(metadata_path)
    print(f"SAMPLES={len(rows)}")
    print(f"OUTPUT={args.output.name}")
    print(f"METADATA={metadata_path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
