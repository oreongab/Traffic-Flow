"""RTSP/HTTP camera ingest with cached JPEG frames for real-mode CCTV routes."""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone

import cv2

from config import Config
from cctv_renderer import render_placeholder
from database.connection import get_session
from database.models import Camera

_cache_lock = threading.Lock()
_frame_cache: dict[str, bytes] = {}
_detect_frame_cache: dict[str, bytes] = {}
_stream_status_cache: dict[str, dict[str, object]] = {}
_captures: dict[str, cv2.VideoCapture] = {}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _encode_frame(frame) -> bytes | None:
    ok, encoded = cv2.imencode(".jpg", frame)
    if not ok:
        return None
    return encoded.tobytes()


def _set_frame(camera_id: str, frame_bytes: bytes, *, detect: bool = False) -> None:
    with _cache_lock:
        if detect:
            _detect_frame_cache[camera_id] = frame_bytes
        else:
            _frame_cache[camera_id] = frame_bytes
        _stream_status_cache[camera_id] = {
            "status": "online",
            "last_frame_at": _utcnow(),
            "source": "rtsp",
        }


def set_detect_frame(camera_id: str, frame_bytes: bytes) -> None:
    """Update cached annotated frame for detection stream endpoints."""
    _set_frame(str(camera_id), frame_bytes, detect=True)


def get_latest_frame(camera_id: str, *, detect: bool = False) -> bytes | None:
    with _cache_lock:
        if detect and camera_id in _detect_frame_cache:
            return _detect_frame_cache.get(camera_id)
        return _frame_cache.get(camera_id)


def get_stream_status(camera_id: str) -> dict[str, object]:
    with _cache_lock:
        return dict(_stream_status_cache.get(str(camera_id), {"status": "offline", "source": "rtsp"}))


def _get_stream_configs() -> list[Camera]:
    session = get_session()
    try:
        return session.query(Camera).filter(Camera.stream_enabled == True).all()  # noqa: E712
    finally:
        session.close()


def _update_stream_row(camera_id: str, *, status: str, last_frame_at: datetime | None = None) -> None:
    session = get_session()
    try:
        row = session.query(Camera).filter(Camera.camera_id == str(camera_id)).first()
        if row is None:
            return
        row.stream_status = status
        row.last_frame_at = last_frame_at
        session.commit()
    except Exception:
        session.rollback()
    finally:
        session.close()


def _ensure_capture(camera_id: str, stream_url: str):
    capture = _captures.get(camera_id)
    if capture is not None and capture.isOpened():
        return capture
    if capture is not None:
        try:
            capture.release()
        except Exception:
            pass
    capture = cv2.VideoCapture(stream_url)
    _captures[camera_id] = capture
    return capture


def _set_offline_placeholder(camera_id: str, message: str) -> None:
    frame_bytes = render_placeholder(message)
    with _cache_lock:
        _frame_cache[camera_id] = frame_bytes
        _stream_status_cache[camera_id] = {
            "status": "offline",
            "last_frame_at": None,
            "source": "rtsp",
            "message": message,
        }


def start_rtsp_ingest_loop() -> None:
    """Continuously fetch latest frames from configured real camera streams."""
    print(f"✓ RTSP ingest loop started (target_fps={Config.CAMERA_STREAM_FPS_TARGET})")
    frame_sleep = max(0.05, 1.0 / max(1, Config.CAMERA_STREAM_FPS_TARGET))

    while True:
        if Config.CAMERA_BACKEND != "rtsp" and Config.SYSTEM_MODE != "real":
            time.sleep(2)
            continue

        stream_rows = _get_stream_configs()
        if not stream_rows:
            time.sleep(2)
            continue

        for row in stream_rows:
            camera_id = str(row.camera_id or "")
            stream_url = str(row.stream_url or "").strip()
            if not camera_id:
                continue
            if not stream_url:
                _set_offline_placeholder(camera_id, "Stream URL not configured")
                _update_stream_row(camera_id, status="offline", last_frame_at=None)
                continue

            capture = _ensure_capture(camera_id, stream_url)
            if not capture or not capture.isOpened():
                _set_offline_placeholder(camera_id, "Camera offline")
                _update_stream_row(camera_id, status="offline", last_frame_at=None)
                continue

            ok, frame = capture.read()
            if not ok or frame is None:
                _set_offline_placeholder(camera_id, "Frame unavailable")
                _update_stream_row(camera_id, status="error", last_frame_at=None)
                continue

            frame_bytes = _encode_frame(frame)
            if frame_bytes is None:
                _set_offline_placeholder(camera_id, "Frame encode failed")
                _update_stream_row(camera_id, status="error", last_frame_at=None)
                continue

            now = _utcnow()
            _set_frame(camera_id, frame_bytes, detect=False)
            _update_stream_row(camera_id, status="online", last_frame_at=now)
            time.sleep(frame_sleep)


def shutdown_rtsp_ingest() -> None:
    """Release all active RTSP/HTTP capture handles."""
    with _cache_lock:
        captures = list(_captures.items())
        _captures.clear()
    for _, capture in captures:
        try:
            capture.release()
        except Exception:
            continue