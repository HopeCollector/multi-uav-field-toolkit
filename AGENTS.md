# Repository instructions

## Scope

Keep this repository public-safe and read-only. It contains a localhost synthetic monitor,
offline ROS bag derivation tools, and a bounded simulation-only automatic tuner. It must
not become a vehicle control, deployment, remote-execution, or live-parameter-application
repository.

## Never add

- Real IP addresses, hostnames, usernames, SSH paths, credentials, or network maps.
- Raw bags, ROS 2 databases, videos, point clouds, field photos, logs, or experiment indexes.
- Flight-control, arming, takeoff, landing, remote start/stop, time-sync, or recording code.
- Real tuned values, field metrics, simulator launch files, or operational planner config.
- Evaluator configuration that accepts an executable, argument vector, shell fragment,
  SSH target, ROS launch command, dynamic import, or network endpoint.
- Code that automatically applies a selected candidate to a simulator, ROS graph, flight
  controller, or vehicle.
- Content copied from the internal repository unless it passes an explicit allowlist and
  privacy review.

Use `192.0.2.0/24`, `198.51.100.0/24`, or `203.0.113.0/24` for documentation examples.
Use synthetic identifiers such as `uav1` and generic topics such as `/chosen/pose`.

## Development workflow

1. State the requested public behavior and safety boundary.
2. Add or update tests before behavior changes when practical.
3. Keep source bags read-only and generated outputs outside input directories.
4. Use `uv` for environments, dependencies, tests, and Python execution.
5. Run `uv run ruff check .` and `uv run pytest`.
6. Scan tracked content for secrets, private paths, and field identifiers before release.
7. Update README or docs when user-facing behavior changes.
8. Commit only after user confirmation.
9. Cap tuning rounds, candidates, parameter ranges, result sizes, logs, and duration.
10. Keep evaluator dispatch allowlisted and local; never make config a generic command runner.
11. Treat tuning recommendations as inert artifacts and keep generated runs out of Git.

Default services to loopback. Any non-loopback exposure must be explicit and documented as
unauthenticated. Preserve prior derived output or refuse replacement unless the user opts in.
