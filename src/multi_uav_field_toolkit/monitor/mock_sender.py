"""Mock UAV UDP sender for host/WebUI development."""

from __future__ import annotations

import argparse
import base64
import math
import socket
import time

from multi_uav_field_toolkit.monitor.common.protocol import (
    DEFAULT_IMAGE_PORT,
    DEFAULT_STATUS_PORT,
    encode_status_packet,
    monotonic_ms,
    split_image_frame,
)

JPEG_1X1 = base64.b64decode(
    b"/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAP//////////////////////////////////////////////////////////////////////////////////////"
    b"2wBDAf//////////////////////////////////////////////////////////////////////////////////////wAARCAABAAEDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAX/"
    b"xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oADAMBAAIQAxAAAAH/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oACAEBAAEFAqf/xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oACAEDAQE/Aaf/"
    b"xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oACAECAQE/Aaf/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oACAEBAAY/Aqf/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oACAEBAAE/IV//"
    b"2gAMAwEAAgADAAAAEP/EABQRAQAAAAAAAAAAAAAAAAAAABD/2gAIAQMBAT8QH//EABQRAQAAAAAAAAAAAAAAAAAAABD/2gAIAQIBAT8QH//EABQQAQAAAAAAAAAAAAAAAAAAABD/"
    b"2gAIAQEAAT8QH//Z"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Send fake UAV monitor UDP packets.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--status-port", type=int, default=DEFAULT_STATUS_PORT)
    parser.add_argument("--image-port", type=int, default=DEFAULT_IMAGE_PORT)
    parser.add_argument("--uav", action="append", default=None)
    parser.add_argument("--status-hz", type=float, default=1.0)
    parser.add_argument("--image-hz", type=float, default=2.0)
    parser.add_argument("--chunk-payload-bytes", type=int, default=1200)
    parser.add_argument(
        "--duration-seconds",
        type=float,
        default=0.0,
        help="Stop after this many seconds. 0 runs until interrupted.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    uav_ids = args.uav or ["uav1", "uav2", "uav3"]
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    status_target = (args.host, args.status_port)
    image_target = (args.host, args.image_port)
    seq = 0
    frame_id = 0
    last_image = 0.0
    status_interval = 1.0 / max(0.1, args.status_hz)
    image_interval = 1.0 / max(0.1, args.image_hz)
    deadline = None if args.duration_seconds <= 0 else time.monotonic() + args.duration_seconds
    while deadline is None or time.monotonic() < deadline:
        now = time.monotonic()
        for index, uav_id in enumerate(uav_ids):
            packet = encode_status_packet(fake_status(uav_id, index, seq, now))
            sock.sendto(packet, status_target)
        if now - last_image >= image_interval:
            frame_id += 1
            for uav_id in uav_ids:
                for packet in split_image_frame(
                    uav_id=uav_id,
                    frame_id=frame_id,
                    timestamp_monotonic_ms=monotonic_ms(),
                    frame=JPEG_1X1,
                    max_payload_bytes=args.chunk_payload_bytes,
                ):
                    sock.sendto(packet, image_target)
            last_image = now
        seq += 1
        time.sleep(status_interval)


def fake_status(uav_id: str, index: int, seq: int, now: float) -> dict:
    freshness = 80 + int(40 * math.sin(now + index))
    return {
        "uav_id": uav_id,
        "agent_time_monotonic_ms": monotonic_ms(),
        "seq": seq,
        "battery": {
            "percent": max(0.0, 0.87 - index * 0.1),
            "voltage": 16.4 - index * 0.2,
            "current": -0.04,
        },
        "flight": {
            "connected": True,
            "armed": False,
            "manual_input": True,
            "mode": "POSCTL",
            "system_status": 3,
            "landed_state": 1,
            "killed": False,
        },
        "rc": {
            "rssi": 255 - index * 30,
            "last_seen_ms": 40,
        },
        "odom": {
            "input_topic": "/localization/pose",
            "freshness_ms": freshness,
            "hz": 10.0,
            "stale": False,
            "ekf_ev_delay": None,
            "ekf_ev_delay_note": "not directly measured",
        },
        "nodes": {
            "telemetry": True,
            "localization": True,
            "perception": True,
            "tracker": True,
            "planner": True,
        },
        "link": {
            "interface": "simulated",
            "network_rx_bps": 4000 + index * 100,
            "network_tx_bps": 12000 + index * 100,
        },
        "agent": {
            "status_tx_bps": 800,
            "image_tx_bps": 36000,
        },
    }


if __name__ == "__main__":
    main()
