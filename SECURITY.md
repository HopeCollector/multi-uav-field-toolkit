# Security and disclosure boundary

## Threat model

The monitor is a laboratory visualization aid, not a control plane. UDP packets have no
authentication, confidentiality, replay protection, or sender integrity. The HTTP server
also has no authentication. A network peer can spoof status, inject images, or read
telemetry when the server is exposed beyond loopback.

The default bind is `127.0.0.1`. Non-loopback binding requires the explicit
`--allow-remote` flag and should only be used on a trusted, isolated network with an
external access-control layer.

## Tuning boundary

The tuning CLI accepts bounded numeric configuration and writes inert result artifacts. An
experiment can select only the registered built-in synthetic evaluator; it cannot specify
an executable, argument vector, shell fragment, SSH target, ROS launch file, or network
endpoint. Candidate evaluation has finite round, candidate, result-size, log-size, and
timeout limits.

Child stdout is suppressed when non-empty. Stderr retains only the evaluator's stable
error-code line; unexpected output is replaced with a byte-count marker so a traceback
cannot persist local paths in a generated run.

A selected candidate is never written into another configuration or applied to a simulator
or vehicle. Generated recommendations must be treated as untrusted synthetic output. The
evaluator process boundary is not an operating-system sandbox.

## Supported use

- Local synthetic demonstrations.
- Local simulation-only automatic tuning with the bundled synthetic evaluator.
- Read-only offline analysis of local ROS bag files.
- Review of derived artifacts after an operator selects the input and output paths.

## Unsupported use

- Flight control, arming, takeoff, landing, mission upload, or actuator commands.
- Internet-facing deployment of the bundled HTTP/UDP service.
- SSH deployment, remote process management, ROS service calls, or bag recording.
- Executing an arbitrary evaluator or adapting the tuner into a generic command runner.
- Reading live ROS state or applying selected parameters to a simulator, planner, flight
  controller, or vehicle.
- Uploading field data or publishing outputs without a separate disclosure review.

## Data handling

Source bags are opened read-only. Generated metadata omits absolute source paths by
default, but topic names, frame IDs, timestamps, trajectories, images, and maps may still
identify a system or location. Keep `outputs/` out of version control and review every
artifact before sharing.

Tuning outputs in the bundled example are synthetic. Do not place real parameter sets,
field metrics, simulator logs, launch files, or operational configurations in the public
examples or commit generated runs.

## Reporting a vulnerability

After publication, use a private GitHub Security Advisory when possible. Do not put
credentials, private network details, field data, or reproducible sensitive payloads in a
public issue.
