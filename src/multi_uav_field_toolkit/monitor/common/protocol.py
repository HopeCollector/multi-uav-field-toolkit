"""Shared UDP protocol helpers for UAV monitoring.

Status packets are UTF-8 JSON dictionaries. Image packets are binary chunks with
a small fixed header followed by the UAV id bytes and payload bytes.
"""

from __future__ import annotations

import json
import math
import re
import struct
import time
import zlib
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

DEFAULT_STATUS_PORT = 17010
DEFAULT_IMAGE_PORT = 17011
DEFAULT_WEB_PORT = 8088
MAX_STATUS_PACKET_BYTES = 16 * 1024
MAX_STATUS_JSON_NESTING = 64

IMAGE_MAGIC = b"UIMG"
IMAGE_VERSION = 1
MAX_UAV_ID_BYTES = 64
MAX_IMAGE_CHUNKS = 8192
MAX_IMAGE_FRAME_BYTES = 8 * 1024 * 1024
MAX_CHUNK_PAYLOAD_BYTES = 60_000
IMAGE_HEADER = struct.Struct("!4sBBHQQHHII")
UAV_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


class ProtocolError(ValueError):
    """Raised when a UDP packet does not match the monitor protocol."""


@dataclass(frozen=True)
class ImageChunk:
    uav_id: str
    frame_id: int
    timestamp_monotonic_ms: int
    chunk_index: int
    chunk_count: int
    payload: bytes


def monotonic_ms() -> int:
    return int(time.monotonic() * 1000)


def encode_status_packet(status: dict[str, Any]) -> bytes:
    validate_uav_id(status.get("uav_id"))
    packet = json.dumps(status, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(packet) > MAX_STATUS_PACKET_BYTES:
        raise ProtocolError(f"status packet exceeds {MAX_STATUS_PACKET_BYTES} bytes")
    return packet


def decode_status_packet(packet: bytes) -> dict[str, Any]:
    if len(packet) > MAX_STATUS_PACKET_BYTES:
        raise ProtocolError(f"status packet exceeds {MAX_STATUS_PACKET_BYTES} bytes")
    validate_json_nesting(packet)
    try:
        value = json.loads(packet.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise ProtocolError(f"invalid status JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ProtocolError("status packet must decode to an object")
    value["uav_id"] = validate_uav_id(value.get("uav_id"))
    return value


def validate_json_nesting(packet: bytes) -> None:
    """Enforce a parser-independent nesting limit while ignoring JSON strings."""
    depth = 0
    in_string = False
    escaped = False
    for byte in packet:
        if in_string:
            if escaped:
                escaped = False
            elif byte == ord("\\"):
                escaped = True
            elif byte == ord('"'):
                in_string = False
            continue
        if byte == ord('"'):
            in_string = True
        elif byte in {ord("["), ord("{")}:
            depth += 1
            if depth > MAX_STATUS_JSON_NESTING:
                raise ProtocolError(f"status JSON nesting exceeds {MAX_STATUS_JSON_NESTING} levels")
        elif byte in {ord("]"), ord("}")}:
            depth -= 1


def validate_uav_id(value: object) -> str:
    """Return a safe identifier accepted by the protocol and URL routes."""
    if not isinstance(value, str) or not UAV_ID_PATTERN.fullmatch(value):
        raise ProtocolError(
            "uav_id must be 1..64 ASCII letters, digits, dots, underscores, or dashes"
        )
    if len(value.encode("utf-8")) > MAX_UAV_ID_BYTES:
        raise ProtocolError("uav_id must encode to at most 64 bytes")
    return value


def pack_image_chunk(
    *,
    uav_id: str,
    frame_id: int,
    timestamp_monotonic_ms: int,
    chunk_index: int,
    chunk_count: int,
    payload: bytes,
) -> bytes:
    uav_id_bytes = validate_uav_id(uav_id).encode("ascii")
    if chunk_count < 1 or chunk_count > MAX_IMAGE_CHUNKS:
        raise ProtocolError("chunk_count out of range")
    if chunk_index < 0 or chunk_index >= chunk_count:
        raise ProtocolError("chunk_index out of range")
    if len(payload) > MAX_CHUNK_PAYLOAD_BYTES:
        raise ProtocolError("payload too large for one image chunk")
    header_len = IMAGE_HEADER.size + len(uav_id_bytes)
    crc32 = zlib.crc32(payload) & 0xFFFFFFFF
    header = IMAGE_HEADER.pack(
        IMAGE_MAGIC,
        IMAGE_VERSION,
        len(uav_id_bytes),
        header_len,
        int(frame_id),
        int(timestamp_monotonic_ms),
        int(chunk_index),
        int(chunk_count),
        len(payload),
        crc32,
    )
    return header + uav_id_bytes + payload


def unpack_image_chunk(packet: bytes) -> ImageChunk:
    if len(packet) < IMAGE_HEADER.size:
        raise ProtocolError("image packet too short")
    (
        magic,
        version,
        uav_id_len,
        header_len,
        frame_id,
        timestamp_monotonic_ms,
        chunk_index,
        chunk_count,
        payload_len,
        expected_crc32,
    ) = IMAGE_HEADER.unpack_from(packet)
    if magic != IMAGE_MAGIC:
        raise ProtocolError("bad image magic")
    if version != IMAGE_VERSION:
        raise ProtocolError("unsupported image protocol version")
    if uav_id_len < 1 or uav_id_len > MAX_UAV_ID_BYTES:
        raise ProtocolError("invalid uav_id length")
    if header_len != IMAGE_HEADER.size + uav_id_len:
        raise ProtocolError("invalid image header length")
    if len(packet) != header_len + payload_len:
        raise ProtocolError("image payload length mismatch")
    if payload_len > MAX_CHUNK_PAYLOAD_BYTES:
        raise ProtocolError("image chunk payload exceeds maximum size")
    if chunk_count < 1 or chunk_count > MAX_IMAGE_CHUNKS or chunk_index >= chunk_count:
        raise ProtocolError("invalid image chunk indexes")
    try:
        uav_id = packet[IMAGE_HEADER.size : header_len].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ProtocolError("uav_id is not valid UTF-8") from exc
    validate_uav_id(uav_id)
    payload = packet[header_len:]
    actual_crc32 = zlib.crc32(payload) & 0xFFFFFFFF
    if actual_crc32 != expected_crc32:
        raise ProtocolError("image payload crc mismatch")
    return ImageChunk(
        uav_id=uav_id,
        frame_id=frame_id,
        timestamp_monotonic_ms=timestamp_monotonic_ms,
        chunk_index=chunk_index,
        chunk_count=chunk_count,
        payload=payload,
    )


def split_image_frame(
    *,
    uav_id: str,
    frame_id: int,
    timestamp_monotonic_ms: int | None,
    frame: bytes,
    max_payload_bytes: int = 1200,
) -> list[bytes]:
    if max_payload_bytes < 256:
        raise ProtocolError("max_payload_bytes must be at least 256")
    if max_payload_bytes > MAX_CHUNK_PAYLOAD_BYTES:
        raise ProtocolError(f"max_payload_bytes must be at most {MAX_CHUNK_PAYLOAD_BYTES}")
    if not frame:
        raise ProtocolError("image frame must not be empty")
    if len(frame) > MAX_IMAGE_FRAME_BYTES:
        raise ProtocolError(f"image frame exceeds {MAX_IMAGE_FRAME_BYTES} bytes")
    if timestamp_monotonic_ms is None:
        timestamp_monotonic_ms = monotonic_ms()
    chunk_count = max(1, int(math.ceil(len(frame) / float(max_payload_bytes))))
    if chunk_count > MAX_IMAGE_CHUNKS:
        raise ProtocolError(f"image frame requires more than {MAX_IMAGE_CHUNKS} chunks")
    packets: list[bytes] = []
    for index in range(chunk_count):
        start = index * max_payload_bytes
        payload = frame[start : start + max_payload_bytes]
        packets.append(
            pack_image_chunk(
                uav_id=uav_id,
                frame_id=frame_id,
                timestamp_monotonic_ms=timestamp_monotonic_ms,
                chunk_index=index,
                chunk_count=chunk_count,
                payload=payload,
            )
        )
    return packets


def iter_json_lines(values: Iterable[dict[str, Any]]) -> Iterable[bytes]:
    for value in values:
        yield encode_status_packet(value) + b"\n"


def validate_jpeg_frame(frame: bytes) -> None:
    """Reject payloads that cannot be a complete JPEG image."""
    if len(frame) < 4 or not frame.startswith(b"\xff\xd8") or not frame.endswith(b"\xff\xd9"):
        raise ProtocolError("assembled image is not a complete JPEG frame")
