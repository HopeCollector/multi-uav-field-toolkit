from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path

import numpy as np
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "livox-bag-to-occupancy-grid.py"
SPEC = importlib.util.spec_from_file_location("livox_grid", SCRIPT)
assert SPEC is not None
livox_grid = importlib.util.module_from_spec(SPEC)
sys.modules["livox_grid"] = livox_grid
assert SPEC.loader is not None
SPEC.loader.exec_module(livox_grid)


def test_quat_from_yaw_rotates_x_to_y() -> None:
    quat = livox_grid.quat_from_rpy_deg(0.0, 0.0, 90.0)
    rot = livox_grid.quat_to_matrix(quat)
    point = np.array([1.0, 0.0, 0.0])

    rotated = rot @ point

    assert np.allclose(rotated, np.array([0.0, 1.0, 0.0]), atol=1e-7)


def test_slerp_halfway_between_identity_and_yaw_180() -> None:
    q0 = livox_grid.quat_from_rpy_deg(0.0, 0.0, 0.0)
    q1 = livox_grid.quat_from_rpy_deg(0.0, 0.0, 180.0)

    half = livox_grid.quat_slerp(q0, q1, 0.5)
    rot = livox_grid.quat_to_matrix(half)
    rotated = rot @ np.array([1.0, 0.0, 0.0])

    assert math.isclose(np.linalg.norm(half), 1.0, rel_tol=1e-9)
    assert np.allclose(rotated, np.array([0.0, 1.0, 0.0]), atol=1e-7)


def test_render_grid_places_positive_y_at_top() -> None:
    image, meta = livox_grid.render_grid({(0, 0), (1, 1)}, 0.3)
    array = np.asarray(image)

    assert meta["width_cells"] == 2
    assert meta["height_cells"] == 2
    assert array[0, 1] == 0
    assert array[1, 0] == 0


def test_render_grid_rejects_excessive_dense_allocation() -> None:
    with pytest.raises(ValueError, match="refusing dense grid"):
        livox_grid.render_grid({(0, 0), (1000, 1000)}, 0.3, max_cells=100, max_side=100)
