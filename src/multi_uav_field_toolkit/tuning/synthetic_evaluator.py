from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
from pathlib import Path
from typing import Any

from .config import SYNTHETIC_PARAMETER_CONTRACTS, SYNTHETIC_PARAMETERS, ConfigError, load_scenarios
from .search import candidate_id

MAX_INPUT_BYTES = 128 * 1024


class EvaluationError(ValueError):
    pass


def _reject_constant(value: str) -> None:
    raise ValueError(value)


def _load_json(path: Path) -> Any:
    if path.stat().st_size > MAX_INPUT_BYTES:
        raise EvaluationError("input-too-large")
    try:
        return json.loads(path.read_text(encoding="utf-8"), parse_constant=_reject_constant)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise EvaluationError("invalid-json") from exc


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(64 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise EvaluationError("scenario-read-failed") from exc
    return digest.hexdigest()


def _number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EvaluationError(f"invalid-parameter:{name}")
    result = float(value)
    if not math.isfinite(result):
        raise EvaluationError(f"invalid-parameter:{name}")
    return result


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def evaluate(
    parameters: dict[str, int | float], scenarios: list[dict[str, object]]
) -> dict[str, float | int]:
    """Evaluate an illustrative, non-physical planner surrogate deterministically."""
    missing = SYNTHETIC_PARAMETERS - set(parameters)
    if missing:
        raise EvaluationError(f"missing-parameter:{sorted(missing)[0]}")
    if set(parameters) != SYNTHETIC_PARAMETERS:
        raise EvaluationError("unexpected-parameter")
    if not scenarios:
        raise EvaluationError("no-scenarios")

    lookahead = _number(parameters["lookahead_m"], "lookahead_m")
    replan_distance = _number(parameters["replan_distance_m"], "replan_distance_m")
    resolution = _number(parameters["grid_resolution_m"], "grid_resolution_m")
    safety_margin = _number(parameters["safety_margin_m"], "safety_margin_m")
    speed = _number(parameters["speed_limit_mps"], "speed_limit_mps")
    stride = _number(parameters["sample_stride"], "sample_stride")
    for name, value in {
        "lookahead_m": lookahead,
        "replan_distance_m": replan_distance,
        "grid_resolution_m": resolution,
        "safety_margin_m": safety_margin,
        "speed_limit_mps": speed,
        "sample_stride": stride,
    }.items():
        expected_kind, minimum, maximum = SYNTHETIC_PARAMETER_CONTRACTS[name]
        if not minimum <= value <= maximum:
            raise EvaluationError(f"parameter-out-of-domain:{name}")
        if expected_kind == "integer" and (
            isinstance(parameters[name], bool) or not isinstance(parameters[name], int)
        ):
            raise EvaluationError(f"invalid-parameter-type:{name}")

    success_values: list[float] = []
    replan_values: list[float] = []
    clearance_values: list[float] = []
    command_gaps: list[float] = []
    completion_values: list[float] = []
    path_ratios: list[float] = []
    collisions = 0

    for scenario in scenarios:
        if not isinstance(scenario, dict):
            raise EvaluationError("invalid-scenario")
        complexity = _number(scenario.get("complexity"), "scenario-complexity")
        narrowness = _number(scenario.get("narrowness"), "scenario-narrowness")
        length = _number(scenario.get("length_m"), "scenario-length")
        if not 0 <= narrowness <= 1 or not 0 <= complexity <= 10 or length <= 0:
            raise EvaluationError("scenario-out-of-domain")

        replan_ms = (
            16.0
            + 3.8 * lookahead
            + 7.0 * (0.15 / resolution)
            + 3.4 * speed
            + 2.1 * complexity
            - 1.8 * (stride - 1.0)
            - 0.8 * replan_distance
        )
        clearance = (
            safety_margin
            + 0.08
            - 0.045 * speed
            - 0.018 * complexity
            - 0.035 * narrowness
        )
        corridor_penalty = max(0.0, safety_margin - (0.55 - 0.25 * narrowness)) * 0.7
        collision = clearance < 0.08
        success = (
            1.02
            - 0.025 * complexity
            - 0.03 * (stride - 1.0)
            - 0.025 * speed
            - 0.06 * max(0.0, replan_distance - 0.6 * lookahead)
            - corridor_penalty
            - (0.25 if collision else 0.0)
        )
        success = min(1.0, max(0.0, success))
        path_ratio = (
            1.02
            + 0.025 * complexity
            + 0.08 * max(0.0, replan_distance / lookahead - 0.5)
            + 0.12 * max(0.0, (resolution - 0.15) / 0.15)
            + 0.05 * corridor_penalty
        )
        completion = length * path_ratio / (0.7 * speed)
        command_gap = 1.25 * replan_ms + 8.0 * speed + 2.0 * stride

        success_values.append(success)
        replan_values.append(replan_ms)
        clearance_values.append(clearance)
        command_gaps.append(command_gap)
        completion_values.append(completion)
        path_ratios.append(path_ratio)
        collisions += int(collision)

    return {
        "success_rate": round(sum(success_values) / len(success_values), 6),
        "collision_count": collisions,
        "p95_replan_ms": round(_percentile(replan_values, 0.95), 6),
        "minimum_clearance_m": round(min(clearance_values), 6),
        "max_command_gap_ms": round(max(command_gaps), 6),
        "completion_time_s": round(sum(completion_values), 6),
        "path_length_ratio": round(sum(path_ratios) / len(path_ratios), 6),
    }


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the bundled synthetic tuning evaluator")
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--scenarios", required=True, type=Path)
    parser.add_argument("--result", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        candidate = _load_json(args.candidate)
        scenarios = load_scenarios(args.scenarios)
        if not isinstance(candidate, dict) or not isinstance(candidate.get("parameters"), dict):
            raise EvaluationError("invalid-candidate")
        identifier = candidate.get("candidate_id")
        if identifier != candidate_id(candidate["parameters"]):
            raise EvaluationError("candidate-id-mismatch")
        expected_scenario_hash = candidate.get("scenario_sha256")
        if (
            not isinstance(expected_scenario_hash, str)
            or len(expected_scenario_hash) != 64
            or any(character not in "0123456789abcdef" for character in expected_scenario_hash)
            or _file_sha256(args.scenarios) != expected_scenario_hash
        ):
            raise EvaluationError("scenario-digest-mismatch")
        metrics = evaluate(candidate["parameters"], scenarios)
        _atomic_json(
            args.result,
            {
                "schema_version": 1,
                "evaluator_id": "synthetic-planner-v1",
                "candidate_id": identifier,
                "simulated": True,
                "status": "ok",
                "scenario_count": len(scenarios),
                "metrics": metrics,
                "diagnostics": {
                    "notes": [
                        "Illustrative deterministic surrogate; not a physical simulator or safety proof."
                    ]
                },
            },
        )
    except (ConfigError, EvaluationError, OSError) as exc:
        code = str(exc) if isinstance(exc, EvaluationError) else "io-error"
        print(f"evaluation failed: {code}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
