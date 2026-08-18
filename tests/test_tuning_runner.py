import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from multi_uav_field_toolkit.tuning.config import load_config
from multi_uav_field_toolkit.tuning.runner import (
    EvaluationFailed,
    OutputExistsError,
    _read_evaluation,
    run_experiment,
)
from multi_uav_field_toolkit.tuning.search import Candidate, candidate_id

ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT = ROOT / "examples" / "synthetic" / "tuning" / "experiment.json"


def test_dry_run_never_starts_an_evaluator(tmp_path, monkeypatch):
    def fail_if_called(*args, **kwargs):
        raise AssertionError("subprocess must not run during dry-run")

    monkeypatch.setattr("multi_uav_field_toolkit.tuning.runner.subprocess.run", fail_if_called)
    output = tmp_path / "preview"

    summary = run_experiment(load_config(EXPERIMENT), output, dry_run=True)

    assert summary["status"] == "dry-run"
    assert (output / "rounds" / "000" / "decision.json").is_file()
    assert not list(output.rglob("evaluation.json"))


def test_existing_output_is_preserved(tmp_path):
    output = tmp_path / "existing"
    output.mkdir()
    sentinel = output / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")

    with pytest.raises(OutputExistsError):
        run_experiment(load_config(EXPERIMENT), output)

    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_runner_uses_fixed_module_without_a_shell(tmp_path, monkeypatch):
    calls = []

    def fail_evaluator(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=9)

    monkeypatch.setattr("multi_uav_field_toolkit.tuning.runner.subprocess.run", fail_evaluator)

    summary = run_experiment(load_config(EXPERIMENT), tmp_path / "failed-run")

    assert summary["status"] == "failed"
    assert len(calls) == 1
    argv, kwargs = calls[0]
    assert isinstance(argv, list)
    assert argv[1:3] == ["-m", "multi_uav_field_toolkit.tuning.synthetic_evaluator"]
    assert kwargs["shell"] is False
    assert kwargs["timeout"] == 10
    assert "lookahead_m" not in argv


def test_evaluator_start_failure_is_recorded_without_crashing(tmp_path, monkeypatch):
    def fail_to_start(*args, **kwargs):
        raise OSError("private host detail must not be persisted")

    monkeypatch.setattr("multi_uav_field_toolkit.tuning.runner.subprocess.run", fail_to_start)
    output = tmp_path / "failed-start"

    summary = run_experiment(load_config(EXPERIMENT), output)

    assert summary["status"] == "failed"
    error = next(output.rglob("error.json")).read_text(encoding="utf-8")
    assert "evaluator-start-failed" in error
    assert "private host detail" not in error


def test_unexpected_child_output_is_suppressed_before_persistence(tmp_path, monkeypatch):
    def noisy_failure(argv, **kwargs):
        kwargs["stderr"].write(b"Traceback at X:\\example-private\\field.py")
        return SimpleNamespace(returncode=7)

    monkeypatch.setattr("multi_uav_field_toolkit.tuning.runner.subprocess.run", noisy_failure)
    output = tmp_path / "noisy-failure"

    summary = run_experiment(load_config(EXPERIMENT), output)

    stderr = next(output.rglob("stderr.log")).read_text(encoding="utf-8")
    assert summary["status"] == "failed"
    assert "X:\\example-private" not in stderr
    assert "output suppressed" in stderr


@pytest.mark.parametrize("corruption", ["extra-metric", "boolean-metric", "extra-envelope"])
def test_result_contract_rejects_untrusted_extensions(tmp_path, corruption):
    parameters = {
        "lookahead_m": 7.0,
        "replan_distance_m": 3.5,
        "grid_resolution_m": 0.15,
        "safety_margin_m": 0.35,
        "speed_limit_mps": 2.0,
        "sample_stride": 2,
    }
    identifier = candidate_id(parameters)
    candidate = Candidate(identifier, 0, "baseline", parameters)
    payload = {
        "schema_version": 1,
        "evaluator_id": "synthetic-planner-v1",
        "candidate_id": identifier,
        "simulated": True,
        "status": "ok",
        "scenario_count": 6,
        "metrics": {
            "success_rate": 0.9,
            "collision_count": 0,
            "p95_replan_ms": 50.0,
            "minimum_clearance_m": 0.2,
            "max_command_gap_ms": 80.0,
            "completion_time_s": 400.0,
            "path_length_ratio": 1.1,
        },
        "diagnostics": {"notes": []},
    }
    if corruption == "extra-metric":
        payload["metrics"]["private_path"] = 1
    elif corruption == "boolean-metric":
        payload["metrics"]["collision_count"] = False
    else:
        payload["private_path"] = "do-not-copy"
    path = tmp_path / "evaluation.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(EvaluationFailed, match="contract"):
        _read_evaluation(path, candidate, "synthetic-planner-v1", 6)


def test_end_to_end_loop_writes_auditable_recommendation(tmp_path):
    output = tmp_path / "run"

    summary = run_experiment(load_config(EXPERIMENT), output)

    assert summary["status"] == "complete"
    best = json.loads((output / "best.json").read_text(encoding="utf-8"))
    leaderboard = json.loads((output / "leaderboard.json").read_text(encoding="utf-8"))
    assert best["simulated"] is True
    assert best["score"]["feasible"] is True
    assert len(leaderboard["candidates"]) > 1
    run_text = (output / "run.json").read_text(encoding="utf-8")
    run = json.loads(run_text)
    candidate = json.loads(next(output.rglob("candidate.json")).read_text(encoding="utf-8"))
    assert str(tmp_path) not in run_text
    assert (output / run["configuration_snapshot"]).is_file()
    assert (output / run["scenarios_snapshot"]).is_file()
    assert candidate["scenario_sha256"] == run["scenarios_sha256"]


def test_run_uses_validated_scenario_snapshot_when_source_changes(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    experiment = json.loads(EXPERIMENT.read_text(encoding="utf-8"))
    experiment["budget"]["rounds"] = 1
    config_path = config_dir / "experiment.json"
    config_path.write_text(json.dumps(experiment), encoding="utf-8")
    source_scenarios = json.loads(
        (EXPERIMENT.parent / "scenarios.json").read_text(encoding="utf-8")
    )
    scenarios_path = config_dir / "scenarios.json"
    scenarios_path.write_text(json.dumps(source_scenarios), encoding="utf-8")
    config = load_config(config_path)

    source_scenarios["scenarios"][0]["complexity"] = 10
    scenarios_path.write_text(json.dumps(source_scenarios), encoding="utf-8")
    output = tmp_path / "snapshot-run"
    summary = run_experiment(config, output)

    pinned = json.loads((output / "inputs" / "scenarios.json").read_text(encoding="utf-8"))
    assert summary["status"] == "complete"
    assert pinned["scenarios"][0]["complexity"] == 1.0
