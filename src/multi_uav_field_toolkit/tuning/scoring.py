from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from .config import ConstraintSpec, ObjectiveSpec


class MetricError(ValueError):
    """Raised when evaluator metrics are incomplete or non-finite."""


@dataclass(frozen=True)
class Score:
    candidate_id: str
    metrics: dict[str, float]
    feasible: bool
    total_violation: float
    objective_score: float
    constraint_results: tuple[dict[str, object], ...]

    @property
    def rank_key(self) -> tuple[bool, float, float, str]:
        return (not self.feasible, self.total_violation, -self.objective_score, self.candidate_id)

    def to_dict(self) -> dict[str, object]:
        return {
            "candidate_id": self.candidate_id,
            "feasible": self.feasible,
            "total_violation": round(self.total_violation, 12),
            "objective_score": round(self.objective_score, 6),
            "constraints": list(self.constraint_results),
        }


def _metric(metrics: Mapping[str, object], name: str) -> float:
    if name not in metrics:
        raise MetricError(f"required metric is missing: {name}")
    value = metrics[name]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MetricError(f"metric must be numeric: {name}")
    result = float(value)
    if not math.isfinite(result):
        raise MetricError(f"metric must be finite: {name}")
    return result


def score_metrics(
    candidate_id: str,
    metrics: Mapping[str, object],
    constraints: Iterable[ConstraintSpec],
    objectives: Iterable[ObjectiveSpec],
) -> Score:
    constraint_specs = tuple(constraints)
    objective_specs = tuple(objectives)
    required = {spec.metric for spec in constraint_specs} | {spec.metric for spec in objective_specs}
    clean_metrics = {name: _metric(metrics, name) for name in sorted(required)}

    total_violation = 0.0
    constraint_results = []
    for spec in constraint_specs:
        value = clean_metrics[spec.metric]
        passed = value <= spec.threshold if spec.operator == "<=" else value >= spec.threshold
        raw_violation = (
            max(0.0, value - spec.threshold)
            if spec.operator == "<="
            else max(0.0, spec.threshold - value)
        )
        normalized = raw_violation / max(abs(spec.threshold), 1.0)
        total_violation += normalized
        constraint_results.append(
            {
                "metric": spec.metric,
                "operator": spec.operator,
                "threshold": spec.threshold,
                "value": value,
                "passed": passed,
                "normalized_violation": round(normalized, 12),
            }
        )

    weighted = 0.0
    total_weight = 0.0
    for spec in objective_specs:
        value = clean_metrics[spec.metric]
        if spec.direction == "maximize":
            normalized = (value - spec.range_min) / (spec.range_max - spec.range_min)
        else:
            normalized = (spec.range_max - value) / (spec.range_max - spec.range_min)
        normalized = min(1.0, max(0.0, normalized))
        weighted += normalized * spec.weight
        total_weight += spec.weight
    objective_score = 100.0 * weighted / total_weight
    if not math.isfinite(objective_score):
        raise MetricError("objective score must be finite")

    return Score(
        candidate_id=candidate_id,
        metrics=clean_metrics,
        feasible=total_violation == 0,
        total_violation=total_violation,
        objective_score=objective_score,
        constraint_results=tuple(constraint_results),
    )


def select_best(scores: Iterable[Score]) -> Score | None:
    values = list(scores)
    return min(values, key=lambda score: score.rank_key) if values else None
