from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass

from .config import ParameterSpec


@dataclass(frozen=True)
class Candidate:
    candidate_id: str
    round_index: int
    source: str
    parameters: dict[str, int | float]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "candidate_id": self.candidate_id,
            "round": self.round_index,
            "source": self.source,
            "parameters": self.parameters,
        }


def _normalize(value: int | float) -> int | float:
    if isinstance(value, int):
        return value
    normalized = round(float(value), 12)
    return 0.0 if normalized == 0 else normalized


def candidate_id(parameters: Mapping[str, int | float]) -> str:
    normalized = {name: _normalize(value) for name, value in sorted(parameters.items())}
    payload = json.dumps(normalized, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def generate_round(
    parameters: Mapping[str, ParameterSpec],
    round_index: int,
    center: Mapping[str, int | float] | None,
    shrink_factor: float,
    seen: set[str] | frozenset[str] | None = None,
    max_candidates: int | None = None,
) -> list[Candidate]:
    seen_ids = seen or set()
    if round_index == 0:
        values = {name: spec.baseline for name, spec in sorted(parameters.items())}
        identifier = candidate_id(values)
        return [] if identifier in seen_ids else [Candidate(identifier, 0, "baseline", values)]
    if center is None:
        raise ValueError("a center is required after the baseline round")

    candidates: list[Candidate] = []
    for name, spec in sorted(parameters.items()):
        if name not in center:
            raise ValueError(f"center is missing parameter {name}")
        if spec.kind == "integer":
            delta: int | float = max(1, round(spec.step * shrink_factor ** (round_index - 1)))
        else:
            delta = _normalize(spec.step * shrink_factor ** (round_index - 1))
        for direction in (-1, 1):
            proposed = center[name] + direction * delta
            if proposed < spec.minimum or proposed > spec.maximum:
                continue
            if spec.kind == "integer":
                proposed = int(round(proposed))
            else:
                proposed = _normalize(proposed)
            values = {key: _normalize(value) for key, value in sorted(center.items())}
            values[name] = proposed
            identifier = candidate_id(values)
            if identifier in seen_ids or any(item.candidate_id == identifier for item in candidates):
                continue
            candidates.append(Candidate(identifier, round_index, "coordinate", values))
            if max_candidates is not None and len(candidates) >= max_candidates:
                return candidates
    return candidates
