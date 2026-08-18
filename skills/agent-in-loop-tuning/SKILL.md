---
name: agent-in-loop-tuning
description: Run, inspect, and diagnose this repository's bounded simulation-only automatic parameter-tuning loop. Use for the bundled synthetic experiment, candidate generation, constraint-aware ranking, or tuning-result review. Do not use for live UAVs, ROS or SSH operations, arbitrary evaluator commands, or applying parameters to a vehicle.
---

# Agent-in-Loop Tuning

Use only the repository's bundled local synthetic evaluator. The CLI completes proposal,
evaluation, ranking, and refinement automatically; it does not require manual candidate
selection between rounds.

Read `docs/agent-in-loop-tuning.md` before changing an experiment or interpreting its
constraints and scores.

## Run

From the repository root, validate the example:

```console
uv run multi-uav-tune validate examples/synthetic/tuning/experiment.json
```

Preview the baseline without starting the evaluator:

```console
uv run multi-uav-tune run examples/synthetic/tuning/experiment.json --output-dir outputs/tuning-preview --dry-run
```

Run the complete synthetic loop:

```console
uv run multi-uav-tune run examples/synthetic/tuning/experiment.json --output-dir outputs/tuning-demo
```

Choose a new output directory for every run. Do not delete or replace a prior run merely to
reuse its path.

## Review

- Confirm the configuration uses finite bounds, timeout, round count, and candidate cap.
- Check feasibility and constraint violations before comparing aggregate scores.
- Inspect per-candidate results and logs when an evaluation fails or times out.
- Use `uv run multi-uav-tune report outputs/tuning-demo` to print the existing `best.json`
  recommendation; the command does not create another report file.
- Treat the selected candidate as a synthetic recommendation only.
- If no candidate is feasible, report the violated constraints; do not silently weaken them.

## Safety boundary

- Do not invoke `ssh`, `scp`, `roslaunch`, `rosservice`, vehicle APIs, or remote processes.
- Do not add an executable, argument vector, shell fragment, endpoint, or private simulator
  adapter to an experiment configuration.
- Do not use real field configurations, metrics, logs, bags, maps, trajectories, or tuned
  values in examples or generated fixtures.
- Do not apply a recommendation to a live simulator or vehicle.
- If a request crosses this boundary, explain that this skill supports only the bundled
  local synthetic workflow.
