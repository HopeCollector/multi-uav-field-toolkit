"""Run the repository's bounded synthetic automatic-tuning example."""

from __future__ import annotations

import argparse
from pathlib import Path

from multi_uav_field_toolkit.tuning.config import load_config
from multi_uav_field_toolkit.tuning.runner import run_experiment

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "examples" / "synthetic" / "tuning" / "experiment.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output-dir", "-o", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    summary = run_experiment(load_config(args.config), args.output_dir, args.dry_run)
    print(
        f"{summary['status']}: {summary['candidate_count']} candidate(s), "
        f"simulated={str(summary['simulated']).lower()}"
    )
    return 0 if summary["status"] not in {"failed", "no-feasible-candidate"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
