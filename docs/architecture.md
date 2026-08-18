# Architecture

## Local monitor

The first public release contains only a synthetic sender and host-side monitor:

1. `multi-uav-monitor-mock` emits status JSON and a tiny JPEG over two UDP ports.
2. `protocol.py` validates UAV IDs, packet lengths, CRC values, frame size, and chunk count.
3. `MonitorAggregator` keeps one bounded in-progress frame and the latest completed frame
   per UAV, with a maximum of 64 tracked identifiers.
4. `multi-uav-monitor` serves a local WebUI, snapshot JSON, SSE updates, and image streams.

Default ports are 17010 for status, 17011 for images, and 8088 for HTTP. All listeners
bind to `127.0.0.1` unless a user explicitly supplies both a different address and
`--allow-remote`.

There is no command channel from the host to a sender. No ROS adapter is shipped in v0.1.

## Offline processing

Each file under `scripts/` is independently executable with `uv run --script`. It reads a
local bag, selects user-specified topics, and writes a derived artifact beneath an explicit
output location. The scripts are not chained automatically, and none of them records or
repairs a bag.

## Simulation-only tuning

`multi-uav-tune` validates a finite experiment, evaluates the baseline, and then performs
bounded coordinate search around the best historical candidate. Each candidate runs in a
separate local Python process through the fixed `synthetic-planner-v1` module. Hard metric
constraints are evaluated before weighted objectives, and every round writes an auditable
decision artifact before the next neighborhood is generated.

The experiment schema cannot select an executable, module, argument vector, shell snippet,
network endpoint, or ROS integration. Candidate values are written to JSON and never
interpolated into a command. The selected result is inert and is not applied elsewhere.

## Trust boundaries

- UDP input is untrusted even on loopback and is parsed with size and identifier limits.
- Browser clients are untrusted readers of the local HTTP API; no mutation endpoint exists.
- ROS bags are untrusted binary input and are parsed by `rosbags` without a ROS installation.
- Tuning configuration and evaluator JSON are untrusted and have strict fields, finite
  numbers, bounded sizes, fixed dispatch, and output-directory overwrite protection.
- Derived files remain untrusted for publication until reviewed for scene and metadata leaks.
