from multi_uav_field_toolkit.tuning.synthetic_evaluator import evaluate

SCENARIOS = [
    {"id": "open", "complexity": 1, "narrowness": 0.1, "length_m": 80},
    {"id": "cluttered", "complexity": 4, "narrowness": 0.6, "length_m": 120},
]


def test_synthetic_evaluator_is_deterministic_and_has_real_tradeoffs():
    baseline = {
        "lookahead_m": 7.0,
        "replan_distance_m": 3.5,
        "grid_resolution_m": 0.15,
        "safety_margin_m": 0.35,
        "speed_limit_mps": 2.0,
        "sample_stride": 2,
    }
    reckless = {**baseline, "safety_margin_m": 0.15, "speed_limit_mps": 3.0}

    first = evaluate(baseline, SCENARIOS)
    second = evaluate(baseline, SCENARIOS)
    unsafe = evaluate(reckless, SCENARIOS)

    assert first == second
    assert first["minimum_clearance_m"] > unsafe["minimum_clearance_m"]
    assert first["collision_count"] <= unsafe["collision_count"]
    assert unsafe["completion_time_s"] < first["completion_time_s"]
