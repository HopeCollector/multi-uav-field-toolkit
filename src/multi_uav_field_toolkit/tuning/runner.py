from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import SYNTHETIC_METRIC_DOMAINS, SYNTHETIC_METRICS, TuningConfig
from .scoring import MetricError, Score, score_metrics, select_best
from .search import Candidate, generate_round

MAX_RESULT_BYTES = 128 * 1024
MAX_LOG_BYTES = 64 * 1024


class TuningError(RuntimeError):
    pass


class OutputExistsError(TuningError):
    pass


class EvaluationFailed(TuningError):
    pass


@dataclass(frozen=True)
class CompletedCandidate:
    candidate: Candidate
    evaluation: dict[str, Any]
    score: Score


def _reject_constant(value: str) -> None:
    raise ValueError(value)


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _config_snapshot(config: TuningConfig) -> dict[str, object]:
    return {
        "schema_version": 1,
        "experiment_id": config.experiment_id,
        "budget": {
            "rounds": config.budget.rounds,
            "max_candidates_per_round": config.budget.max_candidates_per_round,
        },
        "evaluator": {
            "id": config.evaluator.evaluator_id,
            "timeout_seconds": config.evaluator.timeout_seconds,
        },
        "scenarios": "scenarios.json",
        "parameters": {
            name: {
                "type": spec.kind,
                "baseline": spec.baseline,
                "min": spec.minimum,
                "max": spec.maximum,
                "step": spec.step,
            }
            for name, spec in sorted(config.parameters.items())
        },
        "constraints": [
            {"metric": spec.metric, "operator": spec.operator, "threshold": spec.threshold}
            for spec in config.constraints
        ],
        "objectives": [
            {
                "metric": spec.metric,
                "direction": spec.direction,
                "weight": spec.weight,
                "range": [spec.range_min, spec.range_max],
            }
            for spec in config.objectives
        ],
        "search": {"shrink_factor": config.shrink_factor},
    }


def _candidate_document(candidate: Candidate, scenario_sha256: str) -> dict[str, object]:
    return {**candidate.to_dict(), "scenario_sha256": scenario_sha256}


def _minimal_environment() -> dict[str, str]:
    allowed = {
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "WINDIR",
        "TEMP",
        "TMP",
        "TMPDIR",
        "LANG",
        "LC_ALL",
    }
    environment = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONUTF8"] = "1"
    return environment


def _sanitize_log(path: Path, stream_name: str) -> None:
    size = path.stat().st_size
    data = path.read_bytes()[:MAX_LOG_BYTES]
    text = data.decode("utf-8", errors="replace").strip()
    stable_error = re.fullmatch(r"evaluation failed: [a-z0-9:-]+", text)
    if not text:
        path.write_text("", encoding="utf-8")
    elif stream_name == "stderr" and stable_error:
        suffix = "\n[log truncated]" if size > MAX_LOG_BYTES else ""
        path.write_text(f"{text}{suffix}\n", encoding="utf-8")
    else:
        path.write_text(
            f"[{stream_name} output suppressed; {size} byte(s)]\n",
            encoding="utf-8",
        )


def _read_evaluation(
    path: Path, candidate: Candidate, evaluator_id: str, expected_scenario_count: int
) -> dict[str, Any]:
    try:
        if path.stat().st_size > MAX_RESULT_BYTES:
            raise EvaluationFailed("result-too-large")
        value = json.loads(path.read_text(encoding="utf-8"), parse_constant=_reject_constant)
    except FileNotFoundError as exc:
        raise EvaluationFailed("result-missing") from exc
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise EvaluationFailed("result-invalid-json") from exc
    if not isinstance(value, dict):
        raise EvaluationFailed("result-invalid-shape")
    expected_fields = {
        "schema_version",
        "evaluator_id",
        "candidate_id",
        "simulated",
        "status",
        "scenario_count",
        "metrics",
        "diagnostics",
    }
    if set(value) != expected_fields or (
        value.get("schema_version") != 1
        or value.get("candidate_id") != candidate.candidate_id
        or value.get("evaluator_id") != evaluator_id
        or value.get("simulated") is not True
        or value.get("status") != "ok"
        or isinstance(value.get("scenario_count"), bool)
        or value.get("scenario_count") != expected_scenario_count
        or not isinstance(value.get("metrics"), dict)
    ):
        raise EvaluationFailed("result-contract-mismatch")
    metrics = value["metrics"]
    if set(metrics) != SYNTHETIC_METRICS:
        raise EvaluationFailed("result-metric-contract-mismatch")
    for name, metric_value in metrics.items():
        kind, minimum, maximum = SYNTHETIC_METRIC_DOMAINS[name]
        if isinstance(metric_value, bool) or not isinstance(metric_value, (int, float)):
            raise EvaluationFailed("result-metric-contract-mismatch")
        if kind == "integer" and not isinstance(metric_value, int):
            raise EvaluationFailed("result-metric-contract-mismatch")
        if not minimum <= metric_value <= maximum:
            raise EvaluationFailed("result-metric-contract-mismatch")
    diagnostics = value.get("diagnostics")
    if not isinstance(diagnostics, dict) or set(diagnostics) != {"notes"}:
        raise EvaluationFailed("result-contract-mismatch")
    notes = diagnostics.get("notes")
    if (
        not isinstance(notes, list)
        or len(notes) > 16
        or any(not isinstance(note, str) or len(note) > 512 for note in notes)
    ):
        raise EvaluationFailed("result-contract-mismatch")
    return value


def _evaluate_candidate(
    config: TuningConfig,
    candidate: Candidate,
    candidate_dir: Path,
    scenarios_path: Path,
    scenarios_sha256: str,
) -> tuple[dict[str, Any], Score]:
    candidate_path = candidate_dir / "candidate.json"
    result_path = candidate_dir / "evaluation.json"
    stdout_path = candidate_dir / "stdout.log"
    stderr_path = candidate_dir / "stderr.log"
    _atomic_json(candidate_path, _candidate_document(candidate, scenarios_sha256))
    argv = [
        sys.executable,
        "-m",
        "multi_uav_field_toolkit.tuning.synthetic_evaluator",
        "--candidate",
        str(candidate_path.resolve()),
        "--scenarios",
        str(scenarios_path),
        "--result",
        str(result_path.resolve()),
    ]
    try:
        with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
            completed = subprocess.run(
                argv,
                shell=False,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                cwd=candidate_dir,
                env=_minimal_environment(),
                timeout=config.evaluator.timeout_seconds,
                check=False,
            )
    except subprocess.TimeoutExpired as exc:
        raise EvaluationFailed("evaluator-timeout") from exc
    except OSError as exc:
        raise EvaluationFailed("evaluator-start-failed") from exc
    finally:
        if stdout_path.exists():
            _sanitize_log(stdout_path, "stdout")
        if stderr_path.exists():
            _sanitize_log(stderr_path, "stderr")
    if completed.returncode != 0:
        raise EvaluationFailed("evaluator-nonzero-exit")
    evaluation = _read_evaluation(
        result_path, candidate, config.evaluator.evaluator_id, config.scenario_count
    )
    try:
        score = score_metrics(
            candidate.candidate_id,
            evaluation["metrics"],
            config.constraints,
            config.objectives,
        )
    except MetricError as exc:
        raise EvaluationFailed("evaluator-invalid-metrics") from exc
    _atomic_json(candidate_dir / "score.json", score.to_dict())
    return evaluation, score


def _candidate_summary(completed: CompletedCandidate) -> dict[str, object]:
    return {
        "candidate": completed.candidate.to_dict(),
        "metrics": completed.evaluation["metrics"],
        "score": completed.score.to_dict(),
        "simulated": True,
    }


def run_experiment(
    config: TuningConfig, output_dir: str | Path, dry_run: bool = False
) -> dict[str, object]:
    output = Path(output_dir)
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        output.mkdir(exist_ok=False)
    except FileExistsError as exc:
        raise OutputExistsError("output directory already exists") from exc

    inputs_dir = output / "inputs"
    experiment_snapshot_path = inputs_dir / "experiment.json"
    scenarios_snapshot_path = inputs_dir / "scenarios.json"
    _atomic_json(experiment_snapshot_path, _config_snapshot(config))
    _atomic_json(
        scenarios_snapshot_path,
        {
            "schema_version": 1,
            "description": "Normalized synthetic scenarios pinned for this run.",
            "scenarios": list(config.scenarios),
        },
    )
    experiment_sha256 = _file_sha256(experiment_snapshot_path)
    scenarios_sha256 = _file_sha256(scenarios_snapshot_path)

    run_document: dict[str, object] = {
        "schema_version": 1,
        "experiment_id": config.experiment_id,
        "source_configuration": config.source_name,
        "configuration_snapshot": "inputs/experiment.json",
        "configuration_sha256": experiment_sha256,
        "scenarios_snapshot": "inputs/scenarios.json",
        "scenarios_sha256": scenarios_sha256,
        "scenario_count": config.scenario_count,
        "evaluator_id": config.evaluator.evaluator_id,
        "simulated": True,
        "status": "running",
    }
    _atomic_json(output / "run.json", run_document)

    completed_candidates: list[CompletedCandidate] = []
    seen: set[str] = set()
    center: dict[str, int | float] | None = None
    incumbent: CompletedCandidate | None = None
    rounds_completed = 0

    for round_index in range(config.budget.rounds):
        candidates = generate_round(
            config.parameters,
            round_index,
            center,
            config.shrink_factor,
            seen=seen,
            max_candidates=config.budget.max_candidates_per_round,
        )
        round_dir = output / "rounds" / f"{round_index:03d}"
        round_dir.mkdir(parents=True)
        for candidate in candidates:
            seen.add(candidate.candidate_id)
            candidate_dir = round_dir / "candidates" / candidate.candidate_id
            candidate_dir.mkdir(parents=True)
            _atomic_json(
                candidate_dir / "candidate.json", _candidate_document(candidate, scenarios_sha256)
            )

        if dry_run:
            _atomic_json(
                round_dir / "decision.json",
                {
                    "schema_version": 1,
                    "round": round_index,
                    "status": "planned",
                    "candidate_ids": [candidate.candidate_id for candidate in candidates],
                    "note": "Dry-run stops before evaluator execution.",
                },
            )
            run_document["status"] = "dry-run"
            _atomic_json(output / "run.json", run_document)
            return {"status": "dry-run", "candidate_count": len(candidates), "simulated": True}

        successful_this_round = []
        failures = []
        for candidate in candidates:
            candidate_dir = round_dir / "candidates" / candidate.candidate_id
            try:
                evaluation, score = _evaluate_candidate(
                    config,
                    candidate,
                    candidate_dir,
                    scenarios_snapshot_path.resolve(),
                    scenarios_sha256,
                )
            except EvaluationFailed as exc:
                error_code = str(exc)
                failures.append({"candidate_id": candidate.candidate_id, "error": error_code})
                _atomic_json(
                    candidate_dir / "error.json",
                    {"schema_version": 1, "candidate_id": candidate.candidate_id, "error": error_code},
                )
                continue
            item = CompletedCandidate(candidate, evaluation, score)
            completed_candidates.append(item)
            successful_this_round.append(item)

        incumbent_score = select_best(item.score for item in completed_candidates)
        if incumbent_score is not None:
            incumbent = next(
                item
                for item in completed_candidates
                if item.candidate.candidate_id == incumbent_score.candidate_id
            )
            center = dict(incumbent.candidate.parameters)

        if not candidates:
            decision_status = "search-space-exhausted"
        elif not successful_this_round and incumbent is None:
            decision_status = "no-successful-evaluation"
        else:
            decision_status = "continue" if round_index + 1 < config.budget.rounds else "budget-complete"
        violated = []
        if incumbent is not None:
            violated = [
                result["metric"]
                for result in incumbent.score.constraint_results
                if result["passed"] is False
            ]
        _atomic_json(
            round_dir / "decision.json",
            {
                "schema_version": 1,
                "round": round_index,
                "status": decision_status,
                "evaluated": len(successful_this_round),
                "failed": failures,
                "incumbent_candidate_id": incumbent.candidate.candidate_id if incumbent else None,
                "incumbent_feasible": incumbent.score.feasible if incumbent else False,
                "violated_constraints": violated,
                "next_step_scale": round(config.shrink_factor**round_index, 12),
            },
        )
        rounds_completed += 1
        if decision_status in {"search-space-exhausted", "no-successful-evaluation"}:
            break

    ordered = sorted(completed_candidates, key=lambda item: item.score.rank_key)
    _atomic_json(
        output / "leaderboard.json",
        {
            "schema_version": 1,
            "simulated": True,
            "candidates": [_candidate_summary(item) for item in ordered],
        },
    )
    if incumbent is not None:
        _atomic_json(output / "best.json", _candidate_summary(incumbent))
        status = "complete" if incumbent.score.feasible else "no-feasible-candidate"
    else:
        status = "failed"
    run_document["status"] = status
    run_document["rounds_completed"] = rounds_completed
    run_document["successful_candidates"] = len(completed_candidates)
    _atomic_json(output / "run.json", run_document)
    return {
        "status": status,
        "rounds_completed": rounds_completed,
        "candidate_count": len(completed_candidates),
        "simulated": True,
    }
