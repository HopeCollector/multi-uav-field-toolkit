from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import ConfigError, load_config
from .runner import TuningError, run_experiment


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run bounded simulation-only automatic tuning")
    commands = parser.add_subparsers(dest="command", required=True)

    validate = commands.add_parser("validate", help="validate an experiment configuration")
    validate.add_argument("config", type=Path)

    run = commands.add_parser("run", help="run the complete local synthetic loop")
    run.add_argument("config", type=Path)
    run.add_argument("--output-dir", "-o", required=True, type=Path)
    run.add_argument("--dry-run", action="store_true")

    report = commands.add_parser("report", help="print the selected synthetic candidate")
    report.add_argument("run_dir", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "validate":
            config = load_config(args.config)
            print(
                f"valid: {config.experiment_id} ({len(config.parameters)} parameters, "
                f"{config.budget.rounds} rounds)"
            )
            return 0
        if args.command == "run":
            summary = run_experiment(load_config(args.config), args.output_dir, args.dry_run)
            print(json.dumps(summary, sort_keys=True))
            return 2 if summary["status"] in {"no-feasible-candidate", "failed"} else 0
        best_path = args.run_dir / "best.json"
        if not best_path.is_file():
            raise TuningError("run directory has no best.json")
        best = json.loads(best_path.read_text(encoding="utf-8"))
        print(json.dumps(best, indent=2, sort_keys=True))
        return 0
    except (ConfigError, TuningError, OSError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
