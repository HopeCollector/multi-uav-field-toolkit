---
name: process-uav-bag-data
description: Inspect local ROS bags and create non-destructive topic inventories, pose tables, compressed-image previews, or Livox occupancy grids. Use for offline UAV field-data review and reproducible derived artifacts. Do not use for recording, moving, deleting, uploading, or remotely processing bags.
---

# Process UAV Bag Data

Treat every input bag as read-only. Keep outputs outside the source-data directory and avoid `--overwrite` unless the user explicitly approves replacement.

## Choose the smallest operation

When topic names are unknown, inspect first:

`uv run --script scripts/inspect-bag-topics.py INPUT -o outputs/topics.csv --include-all`

Then run only the operation needed:

- PoseStamped trajectory: `uv run --script scripts/extract-pose-trajectory.py INPUT.bag --pose-topic /chosen/pose -o outputs/trajectory.csv`
- Compressed-image preview: `uv run --script scripts/bag-images-to-video.py INPUT --topic /chosen/image/compressed -o outputs/video-preview`
- Livox occupancy grid: `uv run --script scripts/livox-bag-to-occupancy-grid.py INPUT.bag --lidar-topic /chosen/lidar --pose-topic /chosen/pose -o outputs/grids`

Pass topic names discovered from the bag. Do not infer coordinate frames, extrinsics, or time alignment. Supply LiDAR translation and rotation only from user-provided calibration.

## Verify outputs

- Check processed bag and topic counts against the requested scope.
- Inspect sample/frame counts and reported timestamp ranges.
- For occupancy grids, review resolution, grid size, pose delta, and skipped-frame counts.
- Confirm generated CSV and JSON use source basenames rather than absolute paths.
- Treat images, trajectories, frame IDs, and timestamps as potentially sensitive even after path redaction.

## Safety boundary

- Do not alter, repair, rename, move, or delete source bags.
- Do not upload raw bags or derived artifacts unless the user explicitly asks and confirms disclosure scope.
- Do not run an entire pipeline by default; select the minimum script that answers the request.
- Stop when an output already exists. Add `--overwrite` only with explicit authorization and report any backup directory created by the occupancy-grid script.
