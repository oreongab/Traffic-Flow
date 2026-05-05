"""Sparse Lucas-Kanade Optical Flow worker for real-mode RTSP cameras.

Augments YOLO + centroid tracker with motion-sensing data:
  1. YOLO Blindness Fallback — scene flow magnitude tells "moving" vs "empty"
  2. Speed accuracy           — per-bbox LK velocity blended with centroid speed
  3. Tracker association      — predict bbox center via accumulated flow vector
  4. Stop-and-go detection    — bbox flow magnitude < threshold ⇒ stopped

Architecture:
  • Worker thread runs at OPTICAL_FLOW_FPS_TARGET (default 2 Hz, round-robin)
  • Reads JPEG bytes from rtsp_ingest._frame_cache (read-only consumer, in-memory)
  • Stores per-camera prev_gray + sparse feature points + flow vectors in
    module-level _FLOW_STATE dict, lock-guarded
  • Public getters return deep-copied snapshots so callers never see partial state
  • All in-memory, no DB writes, no disk I/O
"""

from __future__ import annotations

import math
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any

import cv2
import numpy as np

from config import Config


_FLOW_LOCK = threading.Lock()
_FLOW_STATE: dict[str, dict[str, Any]] = {}
_CAMERA_CURSOR = 0


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_points(raw_points: object) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    if not isinstance(raw_points, list):
        return points
    for item in raw_points:
        if isinstance(item, dict):
            x_value = item.get("x")
            y_value = item.get("y")
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            x_value, y_value = item[0], item[1]
        else:
            continue
        try:
            points.append((float(x_value), float(y_value)))
        except (TypeError, ValueError):
            continue
    return points


def _point_in_polygon(point: tuple[float, float], polygon: list[tuple[float, float]]) -> bool:
    if len(polygon) < 3:
        return False
    contour = np.array(polygon, dtype=np.float32)
    return bool(cv2.pointPolygonTest(contour, point, False) >= 0)


def _allowlist() -> set[str]:
    raw = (Config.OPTICAL_FLOW_CAMERA_ALLOWLIST or "").strip()
    if not raw:
        return set()
    return {part.strip() for part in raw.split(",") if part.strip()}


def get_camera_scene_flow(camera_id: str) -> dict[str, Any] | None:
    """Return latest scene-level flow snapshot for a camera (None if unavailable)."""
    with _FLOW_LOCK:
        entry = _FLOW_STATE.get(str(camera_id))
        if not entry or not entry.get("has_flow"):
            return None
        return {
            "magnitude": float(entry.get("scene_magnitude", 0.0)),
            "direction_deg": float(entry.get("scene_direction_deg", 0.0)),
            "active_ratio": float(entry.get("active_ratio", 0.0)),
            "ts": entry.get("prev_ts"),
        }


def get_bbox_flow(camera_id: str, bbox: list[float] | tuple[float, ...]) -> dict[str, Any] | None:
    """Return mean velocity of feature points inside the bbox (original-image px)."""
    if not bbox or len(bbox) < 4:
        return None
    x1, y1, x2, y2 = (float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3]))
    with _FLOW_LOCK:
        entry = _FLOW_STATE.get(str(camera_id))
        if not entry or not entry.get("has_flow"):
            return None
        pts = entry.get("last_pts_orig")
        flow = entry.get("flow_vec_orig")
        dt_s = float(entry.get("dt_s") or 0.0)
        if pts is None or flow is None or dt_s <= 0 or len(pts) == 0:
            return None
        mask = (pts[:, 0] >= x1) & (pts[:, 0] <= x2) & (pts[:, 1] >= y1) & (pts[:, 1] <= y2)
        n_points = int(mask.sum())
        if n_points < 3:
            return None
        sub = flow[mask]
        vx = float(sub[:, 0].mean())
        vy = float(sub[:, 1].mean())
        return {
            "vx_px": vx,
            "vy_px": vy,
            "magnitude_px": float(math.hypot(vx, vy)),
            "n_points": n_points,
            "dt_s": dt_s,
        }


def get_zone_flow(camera_id: str, zone_id: str) -> dict[str, Any] | None:
    """Return scalar flow magnitude for a named zone (None if missing)."""
    with _FLOW_LOCK:
        entry = _FLOW_STATE.get(str(camera_id))
        if not entry:
            return None
        per_zone = entry.get("per_zone") or {}
        zone = per_zone.get(str(zone_id))
        if not zone:
            return None
        return dict(zone)


def get_predicted_center(
    camera_id: str,
    prev_center: tuple[float, float],
    dt_s: float,
) -> tuple[float, float] | None:
    """Predict where prev_center will move after dt_s seconds using nearby flow."""
    if dt_s <= 0:
        return None
    with _FLOW_LOCK:
        entry = _FLOW_STATE.get(str(camera_id))
        if not entry or not entry.get("has_flow"):
            return None
        pts = entry.get("last_pts_orig")
        flow = entry.get("flow_vec_orig")
        flow_dt = float(entry.get("dt_s") or 0.0)
        if pts is None or flow is None or flow_dt <= 0 or len(pts) == 0:
            return None
        # Nearest-3 neighbours by Euclidean distance
        diffs = pts - np.array([prev_center[0], prev_center[1]], dtype=np.float32)
        dists = np.hypot(diffs[:, 0], diffs[:, 1])
        k = min(3, len(dists))
        if k <= 0:
            return None
        idx = np.argpartition(dists, k - 1)[:k]
        local_flow = flow[idx]
        vx = float(local_flow[:, 0].mean())
        vy = float(local_flow[:, 1].mean())
        scale = dt_s / flow_dt
        return (prev_center[0] + vx * scale, prev_center[1] + vy * scale)


def _process_camera_tick(camera_id: str, zone_map: dict[str, list[dict[str, object]]]) -> None:
    """Run one LK tick for a single camera (called inside the worker loop)."""
    from services.rtsp_ingest import get_latest_frame, get_stream_status

    status = get_stream_status(camera_id)
    if status.get("status") != "online":
        _bump_offline(camera_id)
        return
    last_frame_at = status.get("last_frame_at")
    if isinstance(last_frame_at, datetime):
        if (_utcnow() - last_frame_at).total_seconds() > 2.0:
            _bump_offline(camera_id)
            return

    frame_bytes = get_latest_frame(camera_id, detect=False)
    if not frame_bytes:
        _bump_offline(camera_id)
        return

    buffer = np.frombuffer(frame_bytes, np.uint8)
    frame = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
    if frame is None or frame.size == 0:
        return
    frame_h, frame_w = frame.shape[:2]
    if frame_w <= 0 or frame_h <= 0:
        return

    target_w = max(64, int(Config.OPTICAL_FLOW_DOWNSAMPLE_WIDTH))
    if frame_w > target_w:
        downsample_ratio = target_w / float(frame_w)
        small = cv2.resize(frame, (target_w, max(1, int(frame_h * downsample_ratio))))
    else:
        downsample_ratio = 1.0
        small = frame
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)

    now = _utcnow()
    with _FLOW_LOCK:
        entry = _FLOW_STATE.setdefault(camera_id, {
            "prev_gray": None,
            "prev_pts": None,
            "feature_age": 0,
            "has_flow": False,
            "stream_offline_strikes": 0,
        })
        entry["stream_offline_strikes"] = 0  # we just got a frame

        prev_gray = entry.get("prev_gray")
        prev_pts = entry.get("prev_pts")
        feature_age = int(entry.get("feature_age") or 0)

    refresh_interval = float(Config.OPTICAL_FLOW_FEATURE_REFRESH_INTERVAL_SECONDS)
    refresh_ticks = max(1, int(refresh_interval * Config.OPTICAL_FLOW_FPS_TARGET))
    max_features = int(Config.OPTICAL_FLOW_MAX_FEATURES)
    needs_features = (
        prev_pts is None
        or prev_gray is None
        or feature_age >= refresh_ticks
        or len(prev_pts) < max(4, max_features // 4)
        or prev_gray.shape != gray.shape
    )

    if needs_features:
        new_pts = cv2.goodFeaturesToTrack(
            gray,
            maxCorners=max_features,
            qualityLevel=0.01,
            minDistance=7,
            blockSize=7,
        )
        with _FLOW_LOCK:
            entry["prev_gray"] = gray
            entry["prev_pts"] = new_pts if new_pts is not None else np.empty((0, 1, 2), dtype=np.float32)
            entry["feature_age"] = 0
            entry["prev_ts"] = now
            entry["downsample_ratio"] = downsample_ratio
            # Don't mark has_flow yet — need at least one LK pass to populate flow_vec
        return

    lk_params = dict(
        winSize=(15, 15),
        maxLevel=2,
        criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 10, 0.03),
    )
    new_pts, status_arr, _ = cv2.calcOpticalFlowPyrLK(prev_gray, gray, prev_pts, None, **lk_params)
    if new_pts is None or status_arr is None:
        with _FLOW_LOCK:
            entry["feature_age"] = feature_age + 1
        return

    # Forward-backward consistency check
    rev_pts, rev_status, _ = cv2.calcOpticalFlowPyrLK(gray, prev_gray, new_pts, None, **lk_params)
    if rev_pts is not None:
        fb_diff = np.linalg.norm((rev_pts - prev_pts).reshape(-1, 2), axis=1)
        good_mask = (status_arr.reshape(-1) == 1) & (fb_diff < 1.0)
    else:
        good_mask = status_arr.reshape(-1) == 1

    good_prev = prev_pts.reshape(-1, 2)[good_mask]
    good_new = new_pts.reshape(-1, 2)[good_mask]
    if len(good_new) < 3:
        with _FLOW_LOCK:
            entry["prev_gray"] = gray
            entry["prev_pts"] = new_pts
            entry["feature_age"] = feature_age + 1
        return

    # Compute flow vectors in original-image pixels
    flow_small = good_new - good_prev  # (N, 2) in downsampled px
    inv_ratio = 1.0 / downsample_ratio if downsample_ratio > 0 else 1.0
    flow_orig = flow_small * inv_ratio
    pts_orig = good_new * inv_ratio

    with _FLOW_LOCK:
        last_ts = entry.get("prev_ts")
    dt_s = max(0.001, (now - last_ts).total_seconds()) if isinstance(last_ts, datetime) else (
        1.0 / max(0.1, Config.OPTICAL_FLOW_FPS_TARGET)
    )

    magnitudes = np.hypot(flow_orig[:, 0], flow_orig[:, 1])
    noise_floor = float(Config.OPTICAL_FLOW_NOISE_FLOOR_PX)
    active_mask = magnitudes > noise_floor
    scene_magnitude = float(magnitudes.mean()) if len(magnitudes) > 0 else 0.0
    active_ratio = float(active_mask.mean()) if len(magnitudes) > 0 else 0.0
    if active_mask.any():
        active_flow = flow_orig[active_mask]
        scene_direction = math.degrees(math.atan2(float(active_flow[:, 1].mean()), float(active_flow[:, 0].mean())))
    else:
        scene_direction = 0.0

    # Per-zone aggregation
    per_zone: dict[str, dict[str, Any]] = {}
    for zone in zone_map.get(camera_id, []) or []:
        polygon = _normalize_points(zone.get("polygon_points"))
        if len(polygon) < 3:
            continue
        zone_id = str(zone.get("zone_id") or "")
        if not zone_id:
            continue
        contour = np.array(polygon, dtype=np.float32)
        in_zone_mags = []
        for i in range(len(pts_orig)):
            x, y = float(pts_orig[i, 0]), float(pts_orig[i, 1])
            if cv2.pointPolygonTest(contour, (x, y), False) >= 0:
                in_zone_mags.append(float(magnitudes[i]))
        if in_zone_mags:
            per_zone[zone_id] = {
                "magnitude": float(np.mean(in_zone_mags)),
                "n_points": int(len(in_zone_mags)),
            }

    with _FLOW_LOCK:
        entry["prev_gray"] = gray
        entry["prev_pts"] = good_new.reshape(-1, 1, 2).astype(np.float32)
        entry["last_pts_orig"] = pts_orig.astype(np.float32)
        entry["flow_vec_orig"] = flow_orig.astype(np.float32)
        entry["scene_magnitude"] = scene_magnitude
        entry["scene_direction_deg"] = scene_direction
        entry["active_ratio"] = active_ratio
        entry["per_zone"] = per_zone
        entry["dt_s"] = dt_s
        entry["prev_ts"] = now
        entry["feature_age"] = feature_age + 1
        entry["downsample_ratio"] = downsample_ratio
        entry["has_flow"] = True


def _bump_offline(camera_id: str) -> None:
    with _FLOW_LOCK:
        entry = _FLOW_STATE.get(camera_id)
        if entry is None:
            return
        strikes = int(entry.get("stream_offline_strikes") or 0) + 1
        entry["stream_offline_strikes"] = strikes
        if strikes > 5:
            _FLOW_STATE.pop(camera_id, None)


def shutdown_optical_flow() -> None:
    """Release per-camera state (called on graceful shutdown)."""
    with _FLOW_LOCK:
        _FLOW_STATE.clear()


def start_optical_flow_loop() -> None:
    """Background-thread entrypoint. Round-robin processes cameras at FPS_TARGET."""
    global _CAMERA_CURSOR

    if not Config.OPTICAL_FLOW_ENABLED:
        print("✓ Optical flow loop idle — disabled by config")
        return
    if Config.CAMERA_BACKEND != "rtsp" and Config.SYSTEM_MODE != "real":
        print("✓ Optical flow loop idle — real camera backend not enabled")

    fps_target = max(0.1, float(Config.OPTICAL_FLOW_FPS_TARGET))
    tick_period = 1.0 / fps_target
    print(f"✓ Optical flow loop started (fps_target={fps_target}, downsample_w={Config.OPTICAL_FLOW_DOWNSAMPLE_WIDTH})")

    from services.mapping import get_camera_catalog, get_camera_zone_map

    # Log effective per-camera revisit interval once we know the camera count.
    # The round-robin design keeps per-cam interval ≈ 1 s regardless of N as
    # long as the tick stays under budget; if you see warnings about "tick
    # over budget" the actual interval will degrade.
    _logged_interval = False

    while True:
        if not Config.OPTICAL_FLOW_ENABLED:
            time.sleep(2.0)
            continue
        if Config.CAMERA_BACKEND != "rtsp" and Config.SYSTEM_MODE != "real":
            time.sleep(2.0)
            continue

        try:
            catalog = get_camera_catalog()
            zone_map = get_camera_zone_map()
        except Exception as exc:
            print(f"  ⚠ Optical flow catalog error: {exc}")
            time.sleep(2.0)
            continue

        allow = _allowlist()
        all_cameras = sorted(catalog.keys())
        if allow:
            all_cameras = [c for c in all_cameras if c in allow]
        if not all_cameras:
            time.sleep(1.0)
            continue

        # Round-robin: process ceil(N / fps_target) cameras per tick.
        # This keeps per-camera revisit interval ≈ 1.0 s regardless of N, as
        # long as the tick stays under budget (per_tick * fps_target == N).
        per_tick = max(1, math.ceil(len(all_cameras) / fps_target))
        if not _logged_interval:
            per_cam_s = len(all_cameras) / max(1, per_tick * fps_target)
            print(f"  ℹ Optical flow: {len(all_cameras)} cams, per_tick={per_tick}, target per-cam interval ≈ {per_cam_s:.2f}s")
            _logged_interval = True
        tick_started = time.monotonic()
        processed = 0
        for _ in range(per_tick):
            camera_id = all_cameras[_CAMERA_CURSOR % len(all_cameras)]
            _CAMERA_CURSOR = (_CAMERA_CURSOR + 1) % len(all_cameras)
            try:
                _process_camera_tick(camera_id, zone_map)
            except Exception as exc:
                print(f"  ⚠ Optical flow tick [{camera_id}]: {exc}")
            processed += 1
            # Bail if we're over budget
            if time.monotonic() - tick_started > tick_period:
                break

        elapsed = time.monotonic() - tick_started
        if elapsed > tick_period:
            print(f"  ⚠ Optical flow tick over budget: {elapsed:.3f}s, processed {processed}/{per_tick}")
        else:
            time.sleep(tick_period - elapsed)
