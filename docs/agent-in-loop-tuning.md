# Agent-in-loop automatic tuning

This repository includes a bounded, simulation-only automatic parameter-tuning loop. It
proposes candidates, runs a local evaluator, checks hard constraints, ranks valid results,
and uses the best candidate as the center of the next round without pausing for manual
selection.

The "agent" is the deterministic local search controller. It does not require a cloud
model or a network connection. The bundled planner-like evaluator and every value under
`examples/synthetic/tuning/` are fabricated; they do not reproduce field measurements,
vehicle settings, or deployment configuration.

## What the loop does

1. Validate the experiment schema, numeric bounds, round limit, candidate cap, and timeout.
2. Evaluate the declared baseline.
3. Generate a bounded coordinate-search neighborhood around the current incumbent.
4. Run only the registered built-in synthetic evaluator for each candidate.
5. Apply hard metric constraints before comparing weighted objective scores.
6. Carry the best feasible candidate into the next round and reduce the numeric step size.
7. Write candidate inputs, evaluator results, logs, scores, decisions, a leaderboard, and a
   final recommendation to a new output directory.

This is a closed local loop: no candidate needs to be selected by hand between rounds. If
no candidate is feasible, the controller follows the least-violating result and reports
that no feasible recommendation was found.

## Run the synthetic example

Prerequisites are `uv` and Python 3.10 or newer.

Validate the configuration:

```console
uv run multi-uav-tune validate examples/synthetic/tuning/experiment.json
```

Preview the baseline candidate without starting an evaluator:

```console
uv run multi-uav-tune run examples/synthetic/tuning/experiment.json --output-dir outputs/tuning-preview --dry-run
```

Run the complete automatic loop:

```console
uv run multi-uav-tune run examples/synthetic/tuning/experiment.json --output-dir outputs/tuning-demo
```

The thin script wrapper provides the same demo:

```console
uv run scripts/run-synthetic-tuning.py --output-dir outputs/tuning-demo-script
```

Each output directory must be new. Generated runs belong under `outputs/`, which is
excluded from Git.

## Configuration and ranking

An experiment declares finite bounds and an initial step for each numeric parameter,
finite compute budgets, hard metric constraints, weighted objectives, and the fixed
`synthetic-planner-v1` evaluator. Round zero evaluates only the baseline. Later rounds vary
one parameter at a time around the best historical candidate, shrink floating-point steps,
and globally skip duplicate fingerprints.

Ranking is constraint-first:

1. A feasible candidate always ranks ahead of an infeasible one.
2. Infeasible candidates are ordered by total normalized constraint violation.
3. Remaining ties are ordered by their weighted objective score and stable candidate ID.

Use hard constraints for requirements such as successful completion, zero collisions, or a
latency ceiling. Use weights only to rank candidates after those requirements are checked.

## Artifacts

Each run contains:

- `run.json`: a path-redacted run summary and SHA-256 references to normalized inputs.
- `inputs/experiment.json` and `inputs/scenarios.json`: immutable-by-convention normalized
  snapshots used by every evaluator process in that run.
- `rounds/NNN/candidates/<sha256>/`: candidate, evaluation, score, and size-capped,
  path-suppressing logs.
- `rounds/NNN/decision.json`: the incumbent and violated constraints used for the next round.
- `leaderboard.json`: every successfully evaluated candidate in ranking order.
- `best.json`: the final synthetic recommendation.

`multi-uav-tune report RUN_DIR` prints that run's existing `best.json`; it does not create
a second report artifact.

Each candidate records the pinned scenario digest, so a modified input is rejected rather
than silently mixed into the run. These files expose the complete proposal-to-decision chain without publishing private
field inputs. A selected candidate is a synthetic recommendation, not a flight-ready
parameter set or evidence of real-world safety.

## Safety boundary

The tuner:

- Runs locally and performs no network access.
- Invokes only a fixed bundled module with `shell=False` and a finite timeout.
- Does not accept an executable, argument vector, shell fragment, endpoint, or dynamic import.
- Does not read ROS topics, call ROS services, launch ROS processes, or connect to a vehicle.
- Does not edit source configuration or apply selected values anywhere.
- Does not contain SSH, SCP, remote process, arming, takeoff, landing, or mission commands.

The evaluator is isolated by interface and process boundaries, not by an operating-system
sandbox. Do not replace the synthetic fixtures with real field artifacts in this public
repository. A live simulator or vehicle adapter belongs in a separate private integration
with an independent safety and disclosure review.
