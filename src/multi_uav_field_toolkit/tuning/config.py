from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MAX_CONFIG_BYTES = 128 * 1024
REGISTERED_EVALUATORS = frozenset({"synthetic-planner-v1"})
SYNTHETIC_PARAMETERS = frozenset(
    {
        "lookahead_m",
        "replan_distance_m",
        "grid_resolution_m",
        "safety_margin_m",
        "speed_limit_mps",
        "sample_stride",
    }
)
SYNTHETIC_PARAMETER_CONTRACTS = {
    "lookahead_m": ("float", 2.0, 15.0),
    "replan_distance_m": ("float", 0.5, 8.0),
    "grid_resolution_m": ("float", 0.05, 0.5),
    "safety_margin_m": ("float", 0.05, 1.0),
    "speed_limit_mps": ("float", 0.25, 5.0),
    "sample_stride": ("integer", 1, 8),
}
SYNTHETIC_METRICS = frozenset(
    {
        "success_rate",
        "collision_count",
        "p95_replan_ms",
        "minimum_clearance_m",
        "max_command_gap_ms",
        "completion_time_s",
        "path_length_ratio",
    }
)
SYNTHETIC_METRIC_DOMAINS = {
    "success_rate": ("float", 0.0, 1.0),
    "collision_count": ("integer", 0, 64),
    "p95_replan_ms": ("float", 0.0, 1_000.0),
    "minimum_clearance_m": ("float", -10.0, 10.0),
    "max_command_gap_ms": ("float", 0.0, 2_000.0),
    "completion_time_s": ("float", 0.0, 10_000_000.0),
    "path_length_ratio": ("float", 1.0, 10.0),
}
_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


class ConfigError(ValueError):
    """Raised when a tuning configuration crosses a schema or safety boundary."""


@dataclass(frozen=True)
class ParameterSpec:
    name: str
    kind: str
    baseline: int | float
    minimum: int | float
    maximum: int | float
    step: int | float


@dataclass(frozen=True)
class ConstraintSpec:
    metric: str
    operator: str
    threshold: float


@dataclass(frozen=True)
class ObjectiveSpec:
    metric: str
    direction: str
    weight: float
    range_min: float
    range_max: float


@dataclass(frozen=True)
class BudgetSpec:
    rounds: int
    max_candidates_per_round: int


@dataclass(frozen=True)
class EvaluatorSpec:
    evaluator_id: str
    timeout_seconds: int


@dataclass(frozen=True)
class TuningConfig:
    schema_version: int
    experiment_id: str
    budget: BudgetSpec
    evaluator: EvaluatorSpec
    scenarios_path: Path
    scenario_count: int
    scenarios: tuple[dict[str, object], ...]
    parameters: dict[str, ParameterSpec]
    constraints: tuple[ConstraintSpec, ...]
    objectives: tuple[ObjectiveSpec, ...]
    shrink_factor: float
    source_name: str


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant: {value}")


def _load_object(path: Path) -> dict[str, Any]:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise ConfigError("configuration file is not readable") from exc
    if size > MAX_CONFIG_BYTES:
        raise ConfigError("configuration file is too large")
    try:
        value = json.loads(path.read_text(encoding="utf-8"), parse_constant=_reject_constant)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        message = "configuration contains non-finite JSON" if "non-finite" in str(exc) else "invalid JSON"
        raise ConfigError(message) from exc
    if not isinstance(value, dict):
        raise ConfigError("configuration root must be an object")
    return value


def _check_fields(value: dict[str, Any], allowed: set[str], context: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ConfigError(f"{context} has unknown field: {unknown[0]}")


def _number(value: Any, context: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{context} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise ConfigError(f"{context} must be a finite number")
    return result


def _integer(value: Any, context: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{context} must be an integer")
    if not minimum <= value <= maximum:
        raise ConfigError(f"{context} must be between {minimum} and {maximum}")
    return value


def _parameters(raw: Any) -> dict[str, ParameterSpec]:
    if not isinstance(raw, dict) or not raw:
        raise ConfigError("parameters must be a non-empty object")
    if len(raw) > 32:
        raise ConfigError("parameters cannot contain more than 32 entries")
    result: dict[str, ParameterSpec] = {}
    for name, value in raw.items():
        if not isinstance(name, str) or not _IDENTIFIER.fullmatch(name):
            raise ConfigError("parameter names must be lowercase public identifiers")
        if not isinstance(value, dict):
            raise ConfigError(f"parameter {name} must be an object")
        _check_fields(value, {"type", "baseline", "min", "max", "step"}, f"parameter {name}")
        kind = value.get("type")
        if kind not in {"float", "integer"}:
            raise ConfigError(f"parameter {name} type must be float or integer")
        if kind == "integer":
            baseline = _integer(value.get("baseline"), f"parameter {name} baseline", -10**9, 10**9)
            minimum = _integer(value.get("min"), f"parameter {name} min", -10**9, 10**9)
            maximum = _integer(value.get("max"), f"parameter {name} max", -10**9, 10**9)
            step = _integer(value.get("step"), f"parameter {name} step", 1, 10**9)
        else:
            baseline = _number(value.get("baseline"), f"parameter {name} baseline")
            minimum = _number(value.get("min"), f"parameter {name} min")
            maximum = _number(value.get("max"), f"parameter {name} max")
            step = _number(value.get("step"), f"parameter {name} step")
        if minimum >= maximum:
            raise ConfigError(f"parameter {name} min must be less than max")
        if step <= 0:
            raise ConfigError(f"parameter {name} step must be greater than zero")
        if kind == "float" and round(step, 12) == 0:
            raise ConfigError(f"parameter {name} step is too small for stable candidate fingerprints")
        if not minimum <= baseline <= maximum:
            raise ConfigError(f"parameter {name} baseline must be within min and max")
        if step > maximum - minimum:
            raise ConfigError(f"parameter {name} step cannot exceed its bounded range")
        result[name] = ParameterSpec(name, kind, baseline, minimum, maximum, step)
    return result


def _constraints(raw: Any) -> tuple[ConstraintSpec, ...]:
    if not isinstance(raw, list) or not raw:
        raise ConfigError("constraints must be a non-empty array")
    result = []
    seen = set()
    for index, value in enumerate(raw):
        if not isinstance(value, dict):
            raise ConfigError(f"constraint {index} must be an object")
        _check_fields(value, {"metric", "operator", "threshold"}, f"constraint {index}")
        metric = value.get("metric")
        if metric not in SYNTHETIC_METRICS:
            raise ConfigError(f"constraint {index} references an unknown metric")
        if metric in seen:
            raise ConfigError(f"constraint metric {metric} is duplicated")
        seen.add(metric)
        operator = value.get("operator")
        if operator not in {"<=", ">="}:
            raise ConfigError(f"constraint {index} operator must be <= or >=")
        threshold = _number(value.get("threshold"), f"constraint {index} threshold")
        _, domain_min, domain_max = SYNTHETIC_METRIC_DOMAINS[metric]
        if not domain_min <= threshold <= domain_max:
            raise ConfigError(f"constraint {index} threshold exceeds the metric domain")
        result.append(ConstraintSpec(metric, operator, threshold))
    return tuple(result)


def _objectives(raw: Any) -> tuple[ObjectiveSpec, ...]:
    if not isinstance(raw, list) or not raw:
        raise ConfigError("objectives must be a non-empty array")
    result = []
    seen = set()
    for index, value in enumerate(raw):
        if not isinstance(value, dict):
            raise ConfigError(f"objective {index} must be an object")
        _check_fields(value, {"metric", "direction", "weight", "range"}, f"objective {index}")
        metric = value.get("metric")
        if metric not in SYNTHETIC_METRICS:
            raise ConfigError(f"objective {index} references an unknown metric")
        if metric in seen:
            raise ConfigError(f"objective metric {metric} is duplicated")
        seen.add(metric)
        direction = value.get("direction")
        if direction not in {"maximize", "minimize"}:
            raise ConfigError(f"objective {index} direction must be maximize or minimize")
        weight = _number(value.get("weight"), f"objective {index} weight")
        if not 0 < weight <= 1_000:
            raise ConfigError(f"objective {index} weight must be greater than zero and at most 1000")
        metric_range = value.get("range")
        if not isinstance(metric_range, list) or len(metric_range) != 2:
            raise ConfigError(f"objective {index} range must contain two numbers")
        range_min = _number(metric_range[0], f"objective {index} range minimum")
        range_max = _number(metric_range[1], f"objective {index} range maximum")
        if range_min >= range_max:
            raise ConfigError(f"objective {index} range minimum must be less than maximum")
        _, domain_min, domain_max = SYNTHETIC_METRIC_DOMAINS[metric]
        if range_min < domain_min or range_max > domain_max:
            raise ConfigError(f"objective {index} range exceeds the metric domain")
        result.append(ObjectiveSpec(metric, direction, weight, range_min, range_max))
    return tuple(result)


def load_config(path: str | Path) -> TuningConfig:
    source = Path(path).resolve()
    raw = _load_object(source)
    _check_fields(
        raw,
        {
            "$schema",
            "schema_version",
            "experiment_id",
            "budget",
            "evaluator",
            "scenarios",
            "parameters",
            "constraints",
            "objectives",
            "search",
        },
        "configuration",
    )
    if raw.get("schema_version") != 1:
        raise ConfigError("schema_version must be 1")
    experiment_id = raw.get("experiment_id")
    if not isinstance(experiment_id, str) or not _IDENTIFIER.fullmatch(experiment_id):
        raise ConfigError("experiment_id must be a lowercase public identifier")

    parameters = _parameters(raw.get("parameters"))
    if set(parameters) != SYNTHETIC_PARAMETERS:
        raise ConfigError("parameters must match the registered evaluator contract")
    for name, spec in parameters.items():
        expected_kind, safe_minimum, safe_maximum = SYNTHETIC_PARAMETER_CONTRACTS[name]
        if spec.kind != expected_kind:
            raise ConfigError(f"parameter {name} type must match the registered evaluator contract")
        if spec.minimum < safe_minimum or spec.maximum > safe_maximum:
            raise ConfigError(f"parameter {name} bounds exceed the registered evaluator domain")

    budget = raw.get("budget")
    if not isinstance(budget, dict):
        raise ConfigError("budget must be an object")
    _check_fields(budget, {"rounds", "max_candidates_per_round"}, "budget")
    rounds = _integer(budget.get("rounds"), "budget rounds", 1, 20)
    max_candidates = _integer(
        budget.get("max_candidates_per_round"), "budget max_candidates_per_round", 1, 64
    )
    if rounds > 1 and max_candidates < 2 * len(parameters):
        raise ConfigError("max_candidates_per_round must allow both directions for each parameter")

    evaluator = raw.get("evaluator")
    if not isinstance(evaluator, dict):
        raise ConfigError("evaluator must be an object")
    _check_fields(evaluator, {"id", "timeout_seconds"}, "evaluator")
    evaluator_id = evaluator.get("id")
    if evaluator_id not in REGISTERED_EVALUATORS:
        raise ConfigError("evaluator id must name a registered evaluator")
    timeout = _integer(evaluator.get("timeout_seconds"), "evaluator timeout_seconds", 1, 300)

    scenarios_value = raw.get("scenarios")
    if not isinstance(scenarios_value, str) or not scenarios_value or Path(scenarios_value).is_absolute():
        raise ConfigError("scenarios must be a relative JSON path")
    scenarios_path = (source.parent / scenarios_value).resolve()
    if not scenarios_path.is_relative_to(source.parent.resolve()) or scenarios_path.suffix != ".json":
        raise ConfigError("scenarios must stay under the configuration directory")
    if not scenarios_path.is_file():
        raise ConfigError("scenarios file does not exist")
    scenarios = load_scenarios(scenarios_path)

    search = raw.get("search")
    if not isinstance(search, dict):
        raise ConfigError("search must be an object")
    _check_fields(search, {"shrink_factor"}, "search")
    shrink_factor = _number(search.get("shrink_factor"), "search shrink_factor")
    if not 0 < shrink_factor <= 1:
        raise ConfigError("search shrink_factor must be greater than zero and at most one")

    return TuningConfig(
        schema_version=1,
        experiment_id=experiment_id,
        budget=BudgetSpec(rounds, max_candidates),
        evaluator=EvaluatorSpec(evaluator_id, timeout),
        scenarios_path=scenarios_path,
        scenario_count=len(scenarios),
        scenarios=tuple(scenarios),
        parameters=parameters,
        constraints=_constraints(raw.get("constraints")),
        objectives=_objectives(raw.get("objectives")),
        shrink_factor=shrink_factor,
        source_name=source.name,
    )


def load_scenarios(path: str | Path) -> list[dict[str, object]]:
    raw = _load_object(Path(path))
    _check_fields(raw, {"$schema", "schema_version", "description", "scenarios"}, "scenarios")
    if raw.get("schema_version") != 1:
        raise ConfigError("scenario schema_version must be 1")
    scenarios = raw.get("scenarios")
    if not isinstance(scenarios, list) or not 1 <= len(scenarios) <= 64:
        raise ConfigError("scenarios must contain between 1 and 64 entries")
    result = []
    seen_ids = set()
    for index, scenario in enumerate(scenarios):
        if not isinstance(scenario, dict):
            raise ConfigError(f"scenario {index} must be an object")
        _check_fields(scenario, {"id", "complexity", "narrowness", "length_m"}, f"scenario {index}")
        missing_fields = {"id", "complexity", "narrowness", "length_m"} - set(scenario)
        if missing_fields:
            raise ConfigError(f"scenario {index} is missing field: {sorted(missing_fields)[0]}")
        identifier = scenario.get("id")
        if not isinstance(identifier, str) or not _IDENTIFIER.fullmatch(identifier):
            raise ConfigError(f"scenario {index} id must be a lowercase public identifier")
        if identifier in seen_ids:
            raise ConfigError(f"scenario id {identifier} is duplicated")
        seen_ids.add(identifier)
        complexity = _number(scenario.get("complexity"), f"scenario {index} complexity")
        narrowness = _number(scenario.get("narrowness"), f"scenario {index} narrowness")
        length = _number(scenario.get("length_m"), f"scenario {index} length_m")
        if not 0 <= complexity <= 10:
            raise ConfigError(f"scenario {index} complexity must be between 0 and 10")
        if not 0 <= narrowness <= 1:
            raise ConfigError(f"scenario {index} narrowness must be between 0 and 1")
        if not 0 < length <= 10_000:
            raise ConfigError(f"scenario {index} length_m must be greater than 0 and at most 10000")
        result.append(
            {
                "id": identifier,
                "complexity": complexity,
                "narrowness": narrowness,
                "length_m": length,
            }
        )
    return result
