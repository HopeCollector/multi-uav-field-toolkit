import json

import pytest

from multi_uav_field_toolkit.tuning.config import ConfigError, load_config


def valid_config() -> dict:
    return {
        "schema_version": 1,
        "experiment_id": "synthetic-navigation-test",
        "budget": {"rounds": 3, "max_candidates_per_round": 12},
        "evaluator": {"id": "synthetic-planner-v1", "timeout_seconds": 10},
        "scenarios": "scenarios.json",
        "parameters": {
            "lookahead_m": {
                "type": "float",
                "baseline": 6.0,
                "min": 4.0,
                "max": 8.0,
                "step": 1.0,
            },
            "replan_distance_m": {
                "type": "float",
                "baseline": 3.0,
                "min": 2.0,
                "max": 5.0,
                "step": 0.5,
            },
            "grid_resolution_m": {
                "type": "float",
                "baseline": 0.15,
                "min": 0.1,
                "max": 0.3,
                "step": 0.05,
            },
            "safety_margin_m": {
                "type": "float",
                "baseline": 0.35,
                "min": 0.15,
                "max": 0.6,
                "step": 0.05,
            },
            "speed_limit_mps": {
                "type": "float",
                "baseline": 2.0,
                "min": 1.0,
                "max": 3.0,
                "step": 0.25,
            },
            "sample_stride": {
                "type": "integer",
                "baseline": 2,
                "min": 1,
                "max": 3,
                "step": 1,
            },
        },
        "constraints": [
            {"metric": "success_rate", "operator": ">=", "threshold": 0.85},
            {"metric": "collision_count", "operator": "<=", "threshold": 0},
        ],
        "objectives": [
            {
                "metric": "p95_replan_ms",
                "direction": "minimize",
                "weight": 2,
                "range": [30, 100],
            }
        ],
        "search": {"shrink_factor": 0.5},
    }


def write_config(tmp_path, payload):
    (tmp_path / "scenarios.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "scenarios": [
                    {"id": "open", "complexity": 1, "narrowness": 0.1, "length_m": 100}
                ],
            }
        ),
        encoding="utf-8",
    )
    path = tmp_path / "experiment.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_loads_bounded_synthetic_config(tmp_path):
    config = load_config(write_config(tmp_path, valid_config()))

    assert config.experiment_id == "synthetic-navigation-test"
    assert config.parameters["sample_stride"].baseline == 2
    assert config.scenarios_path.name == "scenarios.json"


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda data: data["evaluator"].update(command="echo unsafe"), "unknown field"),
        (lambda data: data["evaluator"].update(id="custom-command"), "registered evaluator"),
        (lambda data: data["budget"].update(rounds=True), "integer"),
        (lambda data: data["parameters"]["lookahead_m"].update(step=0), "greater than zero"),
        (lambda data: data["parameters"]["lookahead_m"].update(step=1e-20), "too small"),
        (lambda data: data["parameters"]["lookahead_m"].update(baseline=20), "within"),
        (lambda data: data["parameters"].pop("sample_stride"), "evaluator contract"),
        (lambda data: data["parameters"]["sample_stride"].update(type="float"), "type must match"),
        (lambda data: data["parameters"]["speed_limit_mps"].update(max=20), "domain"),
        (lambda data: data["objectives"][0].update(weight=1e308), "at most 1000"),
        (lambda data: data["objectives"][0].update(range=[-1, 2]), "metric domain"),
        (lambda data: data["constraints"][0].update(threshold=2), "metric domain"),
        (lambda data: data.update(unexpected=True), "unknown field"),
    ],
)
def test_rejects_unsafe_or_invalid_config(tmp_path, mutate, message):
    payload = valid_config()
    mutate(payload)

    with pytest.raises(ConfigError, match=message):
        load_config(write_config(tmp_path, payload))


def test_rejects_non_finite_json(tmp_path):
    path = write_config(tmp_path, valid_config())
    path.write_text(path.read_text(encoding="utf-8").replace("6.0", "NaN", 1), encoding="utf-8")

    with pytest.raises(ConfigError, match="non-finite"):
        load_config(path)


def test_rejects_invalid_scenario_contract(tmp_path):
    path = write_config(tmp_path, valid_config())
    (tmp_path / "scenarios.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "scenarios": [
                    {"id": "open", "complexity": 1, "narrowness": 0.1, "length_m": 1e20}
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="at most 10000"):
        load_config(path)
