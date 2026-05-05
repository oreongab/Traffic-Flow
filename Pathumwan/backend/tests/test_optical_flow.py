"""Unit tests for backend/services/optical_flow.py.

These tests bypass the worker loop and exercise the public getters directly
by seeding `_FLOW_STATE`. The synthetic frame trick is also validated end-to-end:
two frames, the second translated by a known number of pixels, must produce
LK velocity estimates close to the ground-truth translation.

Run from `Pathumwan/backend/`:
    python -m pytest tests/test_optical_flow.py -v
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

import cv2
import numpy as np

# Allow `python -m pytest` from backend/ to import top-level modules
HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(HERE)
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from services import optical_flow  # noqa: E402


def _make_checkerboard(width: int = 320, height: int = 240, square: int = 20) -> np.ndarray:
    """Return a BGR checkerboard image suitable for goodFeaturesToTrack."""
    grid = np.indices((height, width))
    pattern = ((grid[0] // square) + (grid[1] // square)) % 2
    gray = (pattern * 255).astype(np.uint8)
    return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)


def _shifted(image: np.ndarray, dx: int = 10, dy: int = 0) -> np.ndarray:
    """Translate an image by (dx, dy) pixels with edge-fill."""
    h, w = image.shape[:2]
    matrix = np.float32([[1, 0, dx], [0, 1, dy]])
    return cv2.warpAffine(image, matrix, (w, h), borderMode=cv2.BORDER_REPLICATE)


def _seed_flow_state(camera_id: str, prev_frame: np.ndarray, next_frame: np.ndarray) -> None:
    """Run two LK passes against synthetic frames to populate `_FLOW_STATE[camera_id]`."""
    optical_flow.shutdown_optical_flow()

    prev_gray = cv2.cvtColor(prev_frame, cv2.COLOR_BGR2GRAY)
    next_gray = cv2.cvtColor(next_frame, cv2.COLOR_BGR2GRAY)
    prev_pts = cv2.goodFeaturesToTrack(prev_gray, maxCorners=200, qualityLevel=0.01, minDistance=7, blockSize=7)
    assert prev_pts is not None and len(prev_pts) > 10, "checkerboard should yield many corners"

    new_pts, status_arr, _ = cv2.calcOpticalFlowPyrLK(
        prev_gray, next_gray, prev_pts, None,
        winSize=(15, 15),
        maxLevel=2,
        criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 10, 0.03),
    )
    good_mask = status_arr.reshape(-1) == 1
    good_prev = prev_pts.reshape(-1, 2)[good_mask]
    good_new = new_pts.reshape(-1, 2)[good_mask]
    flow = good_new - good_prev

    now = datetime.now(timezone.utc)
    with optical_flow._FLOW_LOCK:  # type: ignore[attr-defined]
        optical_flow._FLOW_STATE[camera_id] = {  # type: ignore[attr-defined]
            "prev_gray": next_gray,
            "prev_pts": good_new.reshape(-1, 1, 2).astype(np.float32),
            "last_pts_orig": good_new.astype(np.float32),
            "flow_vec_orig": flow.astype(np.float32),
            "scene_magnitude": float(np.hypot(flow[:, 0], flow[:, 1]).mean()),
            "scene_direction_deg": 0.0,
            "active_ratio": 1.0,
            "per_zone": {},
            "dt_s": 0.5,
            "prev_ts": now,
            "feature_age": 1,
            "downsample_ratio": 1.0,
            "stream_offline_strikes": 0,
            "has_flow": True,
        }


def test_get_bbox_flow_returns_translation():
    """Synthetic 10-px translation should be recovered by LK + reported via get_bbox_flow."""
    prev = _make_checkerboard()
    nxt = _shifted(prev, dx=10, dy=0)
    _seed_flow_state("cam_test", prev, nxt)

    # Bbox covering the whole frame
    flow = optical_flow.get_bbox_flow("cam_test", [0, 0, 320, 240])
    assert flow is not None
    assert flow["n_points"] >= 10
    assert flow["dt_s"] == 0.5
    # LK on a clean checkerboard recovers translation within ~1 px
    assert abs(flow["vx_px"] - 10.0) < 1.5, f"expected vx≈10, got {flow['vx_px']}"
    assert abs(flow["vy_px"]) < 1.5, f"expected vy≈0, got {flow['vy_px']}"
    assert flow["magnitude_px"] > 8.0


def test_get_bbox_flow_handles_unknown_camera():
    optical_flow.shutdown_optical_flow()
    assert optical_flow.get_bbox_flow("does_not_exist", [0, 0, 100, 100]) is None


def test_get_camera_scene_flow_snapshot():
    prev = _make_checkerboard()
    nxt = _shifted(prev, dx=5, dy=3)
    _seed_flow_state("cam_scene", prev, nxt)

    scene = optical_flow.get_camera_scene_flow("cam_scene")
    assert scene is not None
    assert scene["magnitude"] > 0.0
    assert "direction_deg" in scene
    assert "active_ratio" in scene


def test_get_predicted_center_extrapolates_with_dt():
    prev = _make_checkerboard()
    nxt = _shifted(prev, dx=10, dy=0)
    _seed_flow_state("cam_pred", prev, nxt)

    # dt_s = 1.0 (2x the flow's 0.5s gap) → expect ~20 px shift
    predicted = optical_flow.get_predicted_center("cam_pred", (160.0, 120.0), dt_s=1.0)
    assert predicted is not None
    dx = predicted[0] - 160.0
    dy = predicted[1] - 120.0
    assert 17.0 < dx < 23.0, f"expected ~20 px x-shift, got {dx}"
    assert abs(dy) < 3.0


def test_get_predicted_center_zero_dt_returns_none():
    prev = _make_checkerboard()
    nxt = _shifted(prev, dx=10, dy=0)
    _seed_flow_state("cam_pred_zero", prev, nxt)
    assert optical_flow.get_predicted_center("cam_pred_zero", (100.0, 100.0), dt_s=0.0) is None


def test_shutdown_clears_state():
    prev = _make_checkerboard()
    nxt = _shifted(prev, dx=10, dy=0)
    _seed_flow_state("cam_clear", prev, nxt)
    assert optical_flow.get_camera_scene_flow("cam_clear") is not None
    optical_flow.shutdown_optical_flow()
    assert optical_flow.get_camera_scene_flow("cam_clear") is None
