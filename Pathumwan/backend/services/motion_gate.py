"""Motion-gated YOLO scheduling helpers.

The gate is intentionally conservative: it skips YOLO only when a cheap motion
signal says the scene is quiet, but it still allows periodic heartbeat runs and
briefly keeps YOLO warm after recent positive detections.
"""

from __future__ import annotations

import time
from typing import Any

from config import Config


_GATE_STATE: dict[str, dict[str, float]] = {}


def _total_count(counts: dict[str, Any] | None) -> int:
    if not counts:
        return 0
    try:
        return int(counts.get("total", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _state(camera_id: str) -> dict[str, float]:
    return _GATE_STATE.setdefault(str(camera_id), {})


def should_run_yolo(
    camera_id: str,
    *,
    scene_flow: dict[str, Any] | None = None,
    sim_counts: dict[str, Any] | None = None,
) -> bool:
    """Return True when YOLO should run for this camera in the current tick.

    ``sim_counts`` is preferred in simulation because TraCI already gives a
    cheap ground-truth occupancy signal. ``scene_flow`` is used for RTSP cameras
    and should come from optical_flow.get_camera_scene_flow(). If no cheap
    signal is available, the gate fails open and lets YOLO run.
    """
    if not Config.YOLO_MOTION_GATE_ENABLED:
        return True

    now = time.monotonic()
    state = _state(camera_id)
    heartbeat_seconds = max(5.0, float(Config.YOLO_MOTION_GATE_HEARTBEAT_SECONDS))
    hold_seconds = max(0.0, float(Config.YOLO_MOTION_GATE_ACTIVE_HOLD_SECONDS))

    last_yolo_at = float(state.get("last_yolo_at", 0.0) or 0.0)
    if now - last_yolo_at >= heartbeat_seconds:
        return True

    if sim_counts is not None:
        if _total_count(sim_counts) > 0:
            state["last_motion_at"] = now
            return True
    elif scene_flow is not None:
        try:
            magnitude = float(scene_flow.get("magnitude") or 0.0)
        except (TypeError, ValueError):
            magnitude = 0.0
        try:
            active_ratio = float(scene_flow.get("active_ratio") or 0.0)
        except (TypeError, ValueError):
            active_ratio = 0.0
        if (
            magnitude >= float(Config.YOLO_MOTION_GATE_MIN_MAGNITUDE_PX)
            or active_ratio >= float(Config.YOLO_MOTION_GATE_MIN_ACTIVE_RATIO)
        ):
            state["last_motion_at"] = now
            return True
    else:
        return True

    if now - float(state.get("last_motion_at", 0.0) or 0.0) <= hold_seconds:
        return True
    if now - float(state.get("last_positive_at", 0.0) or 0.0) <= hold_seconds:
        return True
    return False


def record_yolo_result(camera_id: str, counts: dict[str, Any] | None) -> None:
    """Update gate state after an actual YOLO inference."""
    now = time.monotonic()
    state = _state(camera_id)
    state["last_yolo_at"] = now
    if _total_count(counts) > 0:
        state["last_positive_at"] = now
