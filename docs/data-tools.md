# Offline data tools

| Script | Input | Output | Extra dependency |
| --- | --- | --- | --- |
| `inspect-bag-topics.py` | ROS 1 bag or directory | topic inventory CSV | `rosbags` |
| `extract-pose-trajectory.py` | ROS 1 bag + PoseStamped topic | pose CSV + metadata JSON | `rosbags` |
| `bag-images-to-video.py` | ROS 1/2 bag + CompressedImage topic | MP4 + manifest CSV | `rosbags`, `ffmpeg` |
| `livox-bag-to-occupancy-grid.py` | ROS 1 Livox + PoseStamped topics | PNG, JSON, optional CSV/frames | `rosbags`, NumPy, Pillow |

Run topic inspection before choosing other operations. Topic names are explicit parameters
because a public tool should not encode a private field topology as defaults.

All scripts refuse to replace outputs unless `--overwrite` is supplied. The occupancy-grid
script moves a prior run to a named backup directory before committing replacement output.
It also caps dense grid dimensions before allocation. Batch video conversion rejects output
name collisions. CSV and JSON metadata store source basenames instead of absolute paths.

These protections do not make an artifact anonymous. Review topic names, frame IDs,
timestamps, trajectory coordinates, images, maps, and source filenames before disclosure.
