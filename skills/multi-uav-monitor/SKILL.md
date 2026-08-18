---
name: multi-uav-monitor
description: Run, verify, and diagnose this repository's localhost-only multi-UAV UDP/WebUI monitor with synthetic telemetry and JPEG packets. Use for the bundled monitor demo, packet-flow checks, stale-state diagnosis, or WebUI verification. Do not use for flight control, SSH deployment, ROS service calls, or remote process management.
---

# Multi-UAV Monitor

Keep the demo isolated from real vehicles. The bundled sender produces synthetic, disarmed status only.

## Run the demo

1. From the repository root, start the host with `uv run multi-uav-monitor --quiet`.
2. In a second terminal, run `uv run multi-uav-monitor-mock --duration-seconds 10`.
3. Open `http://127.0.0.1:8088` or inspect `http://127.0.0.1:8088/api/snapshot`.
4. Confirm that all expected UAV IDs have fresh status and images.
5. Stop the host with `Ctrl+C` when verification is complete.

Use `--uav ID` repeatedly when the expected set differs from `uav1`, `uav2`, and `uav3`. Adjust ports explicitly when a local port is occupied.

## Diagnose

- If no UAVs appear, compare the host and sender status/image ports.
- If status appears without images, inspect image-port settings and protocol errors.
- If a card is stale, rerun the bounded mock sender and recheck `/api/snapshot`.
- Prefer tests for protocol questions: `uv run pytest tests/test_protocol.py tests/test_aggregator.py tests/test_monitor_security.py`.

## Safety boundary

- Keep `--bind 127.0.0.1` by default.
- A non-loopback bind exposes unauthenticated telemetry and images. Use `--allow-remote` only when the user explicitly requests network exposure and acknowledges the risk.
- Do not invoke SSH, `roslaunch`, `rosservice`, flight-controller APIs, arming, takeoff, landing, or remote process commands.
- Do not replace synthetic packets with live vehicle data without a separate, reviewed integration.
