# Provenance and privacy

This repository is a clean public extraction from a larger internal field-work workspace.
Only an explicit allowlist of generic code was copied. The internal Git history was not
carried over.

Excluded material includes real network configuration, SSH and remote orchestration,
recording and flight-session commands, experiment indexes, schedules, field photos,
presentations, logs, raw bags, videos, point clouds, real tuned values, real evaluation
metrics, simulator launch files, and derived outputs.

Before each public release:

1. Review every newly tracked file rather than relying on `.gitignore`.
2. Search current content and Git history for credentials, private paths, real network
   addresses, hostnames, field names, timestamps, and participant identifiers.
3. Verify that examples are synthetic and documentation IPs use RFC 5737 ranges.
4. Confirm that no raw or derived field artifact has entered Git history.
5. Re-run tests and review the generated package contents.
