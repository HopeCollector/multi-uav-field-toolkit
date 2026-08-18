import math

import pytest

from multi_uav_field_toolkit.tuning.config import ConstraintSpec, ObjectiveSpec
from multi_uav_field_toolkit.tuning.scoring import MetricError, score_metrics, select_best

CONSTRAINTS = (
    ConstraintSpec("success_rate", ">=", 0.85),
    ConstraintSpec("collision_count", "<=", 0),
)
OBJECTIVES = (
    ObjectiveSpec("p95_replan_ms", "minimize", 2.0, 30.0, 100.0),
)


def test_feasible_candidate_beats_higher_scoring_infeasible_candidate():
    feasible = score_metrics(
        "feasible",
        {"success_rate": 0.9, "collision_count": 0, "p95_replan_ms": 60},
        CONSTRAINTS,
        OBJECTIVES,
    )
    infeasible = score_metrics(
        "fast-but-unsafe",
        {"success_rate": 0.9, "collision_count": 1, "p95_replan_ms": 30},
        CONSTRAINTS,
        OBJECTIVES,
    )

    assert select_best([infeasible, feasible]).candidate_id == "feasible"
    assert feasible.feasible is True


def test_all_infeasible_candidates_use_normalized_violation_first():
    close = score_metrics(
        "close",
        {"success_rate": 0.84, "collision_count": 0, "p95_replan_ms": 90},
        CONSTRAINTS,
        OBJECTIVES,
    )
    far = score_metrics(
        "far",
        {"success_rate": 0.5, "collision_count": 0, "p95_replan_ms": 30},
        CONSTRAINTS,
        OBJECTIVES,
    )

    assert select_best([far, close]).candidate_id == "close"


@pytest.mark.parametrize(
    "metrics",
    [
        {"success_rate": 0.9, "collision_count": 0},
        {"success_rate": True, "collision_count": 0, "p95_replan_ms": 50},
        {"success_rate": math.nan, "collision_count": 0, "p95_replan_ms": 50},
    ],
)
def test_missing_or_invalid_metrics_do_not_score(metrics):
    with pytest.raises(MetricError):
        score_metrics("candidate", metrics, CONSTRAINTS, OBJECTIVES)
