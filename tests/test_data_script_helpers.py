import csv
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_script(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_topic_classification_and_csv_path_redaction(tmp_path):
    script = load_script("inspect_bag_topics", "inspect-bag-topics.py")
    row = script.TopicRow(
        bag=tmp_path / "private" / "capture.bag",
        topic="/chosen/pose",
        msgtype="geometry_msgs/msg/PoseStamped",
        count=5,
        role=script.classify_topic("/chosen/pose", "geometry_msgs/msg/PoseStamped"),
    )
    output = tmp_path / "topics.csv"

    script.write_csv(output, [row])

    with output.open(newline="", encoding="utf-8") as handle:
        written = next(csv.DictReader(handle))
    assert written["bag"] == "capture.bag"
    assert written["role"] == "odometry_candidate"
    assert "private" not in output.read_text(encoding="utf-8")


def test_video_manifest_uses_basenames(tmp_path):
    script = load_script("bag_images_to_video", "bag-images-to-video.py")
    result = script.ExportResult(
        bag=tmp_path / "private" / "capture.bag",
        output=tmp_path / "outputs" / "preview.mp4",
        topic="/chosen/image/compressed",
        frames=10,
        fps=20.0,
        status="ok",
        message="",
    )
    output = tmp_path / "manifest.csv"

    script.write_manifest(output, [result])

    with output.open(newline="", encoding="utf-8") as handle:
        written = next(csv.DictReader(handle))
    assert written["bag"] == "capture.bag"
    assert written["output"] == "preview.mp4"
    assert "private" not in output.read_text(encoding="utf-8")


def test_video_batch_rejects_sanitized_name_collision(tmp_path):
    script = load_script("bag_images_to_video_collision", "bag-images-to-video.py")
    bags = [
        tmp_path / "run+a" / "capture.bag",
        tmp_path / "run a" / "capture.bag",
    ]

    with pytest.raises(ValueError, match="multiple inputs map"):
        script.build_jobs(bags, tmp_path / "outputs", overwrite=False)


def test_trajectory_output_paths_cannot_collide(tmp_path):
    script = load_script("extract_pose_trajectory", "extract-pose-trajectory.py")
    bag = tmp_path / "capture.bag"
    output = tmp_path / "trajectory.csv"

    with pytest.raises(ValueError, match="different files"):
        script.validate_output_paths(bag, output, output)
    with pytest.raises(ValueError, match="source bag"):
        script.validate_output_paths(bag, bag, tmp_path / "metadata.json")
