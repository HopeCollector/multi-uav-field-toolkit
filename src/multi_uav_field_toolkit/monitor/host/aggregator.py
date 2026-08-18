"""In-memory aggregation for multi-UAV monitor packets."""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from multi_uav_field_toolkit.monitor.common.protocol import (
    MAX_IMAGE_FRAME_BYTES,
    ImageChunk,
    ProtocolError,
    decode_status_packet,
    unpack_image_chunk,
    validate_jpeg_frame,
    validate_uav_id,
)

STATUS_WARN_MS = 3000
STATUS_ERROR_MS = 6000
IMAGE_WARN_MS = 2000
IMAGE_ERROR_MS = 5000
ODOM_WARN_MS = 300
ODOM_ERROR_MS = 800
IMAGE_ASSEMBLY_TIMEOUT_MS = 3000
MAX_TRACKED_UAVS = 64


class RateMeter:
    def __init__(self, window_seconds: float = 5.0) -> None:
        self.window_seconds = window_seconds
        self.samples: deque[tuple[float, int]] = deque()

    def add(self, byte_count: int, now: float | None = None) -> None:
        if now is None:
            now = time.monotonic()
        self.samples.append((now, byte_count))
        self._trim(now)

    def rate_bps(self, now: float | None = None) -> float:
        if now is None:
            now = time.monotonic()
        self._trim(now)
        if not self.samples:
            return 0.0
        return sum(size for _, size in self.samples) / self.window_seconds

    def _trim(self, now: float) -> None:
        cutoff = now - self.window_seconds
        while self.samples and self.samples[0][0] < cutoff:
            self.samples.popleft()


@dataclass
class ImageAssembly:
    frame_id: int
    timestamp_monotonic_ms: int
    chunk_count: int
    created_monotonic_ms: int
    chunks: dict[int, bytes] = field(default_factory=dict)
    total_bytes: int = 0

    def add(self, chunk: ImageChunk) -> bool:
        if chunk.frame_id != self.frame_id:
            return False
        if chunk.chunk_count != self.chunk_count:
            return False
        previous = self.chunks.get(chunk.chunk_index)
        next_total = self.total_bytes - (len(previous) if previous is not None else 0)
        next_total += len(chunk.payload)
        if next_total > MAX_IMAGE_FRAME_BYTES:
            raise ProtocolError("assembled image exceeds maximum frame size")
        self.total_bytes = next_total
        self.chunks[chunk.chunk_index] = chunk.payload
        return len(self.chunks) == self.chunk_count

    def frame(self) -> bytes:
        return b"".join(self.chunks[index] for index in range(self.chunk_count))


@dataclass
class UavState:
    uav_id: str
    status: dict[str, Any] = field(default_factory=dict)
    status_received_monotonic_ms: int | None = None
    latest_image: bytes | None = None
    latest_image_frame_id: int | None = None
    latest_image_agent_monotonic_ms: int | None = None
    latest_image_received_monotonic_ms: int | None = None
    image_candidate: ImageAssembly | None = None
    status_rx: RateMeter = field(default_factory=RateMeter)
    image_rx: RateMeter = field(default_factory=RateMeter)
    image_version: int = 0


class MonitorAggregator:
    def __init__(self, expected_uavs: list[str] | None = None) -> None:
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._uavs: dict[str, UavState] = {}
        for uav_id in expected_uavs or []:
            validate_uav_id(uav_id)
            if len(self._uavs) >= MAX_TRACKED_UAVS:
                raise ProtocolError(f"monitor tracks at most {MAX_TRACKED_UAVS} UAV ids")
            self._uavs[uav_id] = UavState(uav_id=uav_id)

    def _state_for_uav(self, uav_id: str) -> UavState:
        state = self._uavs.get(uav_id)
        if state is not None:
            return state
        if len(self._uavs) >= MAX_TRACKED_UAVS:
            raise ProtocolError(f"monitor tracks at most {MAX_TRACKED_UAVS} UAV ids")
        state = UavState(uav_id=uav_id)
        self._uavs[uav_id] = state
        return state

    def ingest_status_packet(self, packet: bytes, source: tuple[str, int]) -> None:
        now = time.monotonic()
        status = decode_status_packet(packet)
        uav_id = str(status["uav_id"])
        with self._lock:
            state = self._state_for_uav(uav_id)
            state.status = status
            state.status_received_monotonic_ms = int(now * 1000)
            state.status["host"] = {
                "source_ip": source[0],
                "source_port": source[1],
            }
            state.status_rx.add(len(packet), now)

    def ingest_image_packet(self, packet: bytes, source: tuple[str, int]) -> None:
        now = time.monotonic()
        now_ms = int(now * 1000)
        chunk = unpack_image_chunk(packet)
        with self._lock:
            state = self._state_for_uav(chunk.uav_id)
            state.image_rx.add(len(packet), now)

            if self._is_agent_restart_frame(state, chunk, now_ms):
                state.latest_image_frame_id = None
                state.latest_image_agent_monotonic_ms = None
                state.image_candidate = None

            if (
                state.latest_image_frame_id is not None
                and chunk.frame_id <= state.latest_image_frame_id
            ):
                return
            candidate_expired = (
                state.image_candidate is not None
                and now_ms - state.image_candidate.created_monotonic_ms >= IMAGE_ASSEMBLY_TIMEOUT_MS
            )
            if (
                state.image_candidate is None
                or candidate_expired
                or chunk.frame_id > state.image_candidate.frame_id
            ):
                state.image_candidate = ImageAssembly(
                    frame_id=chunk.frame_id,
                    timestamp_monotonic_ms=chunk.timestamp_monotonic_ms,
                    chunk_count=chunk.chunk_count,
                    created_monotonic_ms=now_ms,
                )
            if chunk.frame_id < state.image_candidate.frame_id:
                return

            if state.image_candidate.add(chunk):
                frame = state.image_candidate.frame()
                validate_jpeg_frame(frame)
                state.latest_image = frame
                state.latest_image_frame_id = state.image_candidate.frame_id
                state.latest_image_agent_monotonic_ms = state.image_candidate.timestamp_monotonic_ms
                state.latest_image_received_monotonic_ms = now_ms
                state.image_version += 1
                state.image_candidate = None
                self._condition.notify_all()

    def snapshot(self) -> dict[str, Any]:
        now_ms = int(time.monotonic() * 1000)
        now = time.monotonic()
        with self._lock:
            uavs = [self._snapshot_uav(state, now_ms, now) for state in self._uavs.values()]
        return {
            "host_time_monotonic_ms": now_ms,
            "uavs": sorted(uavs, key=lambda item: item["uav_id"]),
        }

    def latest_image(self, uav_id: str) -> bytes | None:
        with self._lock:
            state = self._uavs.get(uav_id)
            if state is None:
                return None
            return state.latest_image

    def wait_for_image(
        self,
        uav_id: str,
        last_version: int,
        timeout_seconds: float = 2.0,
    ) -> tuple[bytes | None, int]:
        deadline = time.monotonic() + timeout_seconds
        with self._condition:
            while True:
                state = self._uavs.get(uav_id)
                if (
                    state is not None
                    and state.latest_image is not None
                    and state.image_version != last_version
                ):
                    return state.latest_image, state.image_version
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    if state is None:
                        return None, last_version
                    return state.latest_image, state.image_version
                self._condition.wait(remaining)

    def _snapshot_uav(self, state: UavState, now_ms: int, now: float) -> dict[str, Any]:
        status_age_ms = None
        if state.status_received_monotonic_ms is not None:
            status_age_ms = now_ms - state.status_received_monotonic_ms
        image_age_ms = None
        if state.latest_image_received_monotonic_ms is not None:
            image_age_ms = now_ms - state.latest_image_received_monotonic_ms

        status = dict(state.status)
        status["host"] = {
            **status.get("host", {}),
            "status_age_ms": status_age_ms,
            "status_rx_bps": state.status_rx.rate_bps(now),
            "image_rx_bps": state.image_rx.rate_bps(now),
            "image_age_ms": image_age_ms,
            "image_frame_id": state.latest_image_frame_id,
            "image_agent_monotonic_ms": state.latest_image_agent_monotonic_ms,
            "status_level": level_for_age(status_age_ms, STATUS_WARN_MS, STATUS_ERROR_MS),
            "image_level": level_for_age(image_age_ms, IMAGE_WARN_MS, IMAGE_ERROR_MS),
            "odom_level": level_for_age(
                nested_number(status, ["odom", "freshness_ms"]),
                ODOM_WARN_MS,
                ODOM_ERROR_MS,
            ),
        }
        return {
            "uav_id": state.uav_id,
            "status": status,
            "latest_image_url": f"/api/image/{state.uav_id}",
            "has_image": state.latest_image is not None,
        }

    def _is_agent_restart_frame(self, state: UavState, chunk: ImageChunk, now_ms: int) -> bool:
        if state.latest_image_agent_monotonic_ms is None:
            return False
        if (
            state.latest_image_frame_id is not None
            and chunk.frame_id <= state.latest_image_frame_id
            and chunk.timestamp_monotonic_ms > state.latest_image_agent_monotonic_ms
        ):
            return True
        if state.latest_image_received_monotonic_ms is None:
            return False
        image_age_ms = now_ms - state.latest_image_received_monotonic_ms
        return image_age_ms >= IMAGE_ERROR_MS and chunk.frame_id <= (
            state.latest_image_frame_id or -1
        )


def nested_number(value: dict[str, Any], keys: list[str]) -> float | None:
    current: Any = value
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    if isinstance(current, (int, float)):
        return float(current)
    return None


def level_for_age(age_ms: float | None, warn_ms: int, error_ms: int) -> str:
    if age_ms is None:
        return "missing"
    if age_ms >= error_ms:
        return "error"
    if age_ms >= warn_ms:
        return "warn"
    return "ok"
