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
_next_capture_at: dict[str, float] = {}
_worker_threads: dict[str, threading.Thread] = {}
_worker_stop_events: dict[str, threading.Event] = {}
_worker_configs: dict[str, tuple[str, int]] = {}
_last_db_status_update: dict[str, tuple[str, float]] = {}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _target_fps(raw_value: object | None = None) -> int:
    try:
        row_fps = int(raw_value or Config.CAMERA_STREAM_FPS_TARGET)
    except (TypeError, ValueError):
        row_fps = Config.CAMERA_STREAM_FPS_TARGET
    return max(1, min(12, row_fps))


def _encode_frame(frame) -> bytes | None:
    max_width = max(240, int(getattr(Config, "CAMERA_STREAM_MAX_WIDTH", 960) or 960))
    try:
        height, width = frame.shape[:2]
    except Exception:
        height = width = 0
    if width > max_width and height > 0:
        scale = max_width / float(width)
        frame = cv2.resize(frame, (max_width, max(1, int(height * scale))), interpolation=cv2.INTER_AREA)

    quality = max(45, min(90, int(getattr(Config, "CAMERA_STREAM_JPEG_QUALITY", 72) or 72)))
    ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
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


def _get_stream_configs() -> list[dict[str, object]]:
    session = get_session()
    try:
        rows = session.query(Camera).filter(Camera.stream_enabled == True).all()  # noqa: E712
        configs: list[dict[str, object]] = []
        for row in rows:
            camera_id = str(getattr(row, "camera_id", "") or "").strip()
            if not camera_id:
                continue
            configs.append({
                "camera_id": camera_id,
                "stream_url": str(getattr(row, "stream_url", "") or "").strip(),
                "fps_target": getattr(row, "fps_target", None),
            })
        return configs
    finally:
        session.close()


def _should_update_stream_row(camera_id: str, status: str) -> bool:
    heartbeat = max(1.0, float(getattr(Config, "CAMERA_STREAM_DB_HEARTBEAT_SECONDS", 5.0) or 5.0))
    now = time.monotonic()
    with _cache_lock:
        previous = _last_db_status_update.get(camera_id)
        if previous and previous[0] == status and (now - previous[1]) < heartbeat:
            return False
        _last_db_status_update[camera_id] = (status, now)
    return True


def _update_stream_row(camera_id: str, *, status: str, last_frame_at: datetime | None = None, force: bool = False) -> None:
    if not force and not _should_update_stream_row(camera_id, status):
        return
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


def _open_capture(camera_id: str, stream_url: str, target_fps: int):
    with _cache_lock:
        old_capture = _captures.pop(camera_id, None)
    if old_capture is not None:
        try:
            old_capture.release()
        except Exception:
            pass

    capture = cv2.VideoCapture(stream_url)
    for prop_name, value in (
        ("CAP_PROP_BUFFERSIZE", 1),
        ("CAP_PROP_FPS", target_fps),
        ("CAP_PROP_OPEN_TIMEOUT_MSEC", 3000),
        ("CAP_PROP_READ_TIMEOUT_MSEC", 3000),
    ):
        prop_id = getattr(cv2, prop_name, None)
        if prop_id is None:
            continue
        try:
            capture.set(prop_id, value)
        except Exception:
            continue

    with _cache_lock:
        _captures[camera_id] = capture
    return capture


def _release_capture(camera_id: str) -> None:
    with _cache_lock:
        capture = _captures.pop(camera_id, None)
    if capture is not None:
        try:
            capture.release()
        except Exception:
            pass


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


def _camera_worker(camera_id: str, stream_url: str, fps_target: int, stop_event: threading.Event) -> None:
    target_fps = _target_fps(fps_target)
    frame_interval = 1.0 / float(target_fps)
    reconnect_seconds = max(0.25, float(getattr(Config, "CAMERA_STREAM_RECONNECT_SECONDS", 1.5) or 1.5))
    capture = None
    print(f"  ✓ RTSP worker started [{camera_id}] target_fps={target_fps}")

    while not stop_event.is_set():
        loop_started = time.monotonic()
        try:
            if capture is None or not capture.isOpened():
                capture = _open_capture(camera_id, stream_url, target_fps)
                if not capture or not capture.isOpened():
                    _set_offline_placeholder(camera_id, "Camera offline")
                    _update_stream_row(camera_id, status="offline", last_frame_at=None)
                    _release_capture(camera_id)
                    capture = None
                    stop_event.wait(reconnect_seconds)
                    continue

            ok, frame = capture.read()
            if not ok or frame is None:
                _set_offline_placeholder(camera_id, "Frame unavailable")
                _update_stream_row(camera_id, status="error", last_frame_at=None)
                _release_capture(camera_id)
                capture = None
                stop_event.wait(reconnect_seconds)
                continue

            frame_bytes = _encode_frame(frame)
            if frame_bytes is None:
                _set_offline_placeholder(camera_id, "Frame encode failed")
                _update_stream_row(camera_id, status="error", last_frame_at=None)
                stop_event.wait(frame_interval)
                continue

            now = _utcnow()
            _set_frame(camera_id, frame_bytes, detect=False)
            _update_stream_row(camera_id, status="online", last_frame_at=now)
        except Exception as exc:
            _set_offline_placeholder(camera_id, "Camera stream recovering")
            _update_stream_row(camera_id, status="error", last_frame_at=None)
            _release_capture(camera_id)
            capture = None
            print(f"  ⚠ RTSP worker error [{camera_id}]: {type(exc).__name__}: {exc}")
            stop_event.wait(reconnect_seconds)
            continue

        elapsed = time.monotonic() - loop_started
        stop_event.wait(max(0.0, frame_interval - elapsed))

    _release_capture(camera_id)
    print(f"  ✓ RTSP worker stopped [{camera_id}]")


def _stop_worker(camera_id: str) -> None:
    with _cache_lock:
        stop_event = _worker_stop_events.pop(camera_id, None)
        thread = _worker_threads.pop(camera_id, None)
        _worker_configs.pop(camera_id, None)
    if stop_event is not None:
        stop_event.set()
    if thread is not None and thread.is_alive():
        thread.join(timeout=1.0)
    _release_capture(camera_id)


def _start_or_update_worker(camera_id: str, stream_url: str, fps_target: int) -> None:
    config_signature = (stream_url, _target_fps(fps_target))
    with _cache_lock:
        existing_thread = _worker_threads.get(camera_id)
        existing_config = _worker_configs.get(camera_id)
    if existing_thread is not None and existing_thread.is_alive() and existing_config == config_signature:
        return

    _stop_worker(camera_id)
    stop_event = threading.Event()
    thread = threading.Thread(
        target=_camera_worker,
        args=(camera_id, stream_url, config_signature[1], stop_event),
        daemon=True,
        name=f"RTSP Camera {camera_id}",
    )
    with _cache_lock:
        _worker_stop_events[camera_id] = stop_event
        _worker_threads[camera_id] = thread
        _worker_configs[camera_id] = config_signature
    thread.start()


def start_rtsp_ingest_loop() -> None:
    """Continuously manage per-camera RTSP/HTTP workers."""
    print(f"✓ RTSP ingest manager started (target_fps={Config.CAMERA_STREAM_FPS_TARGET})")

    while True:
        if Config.CAMERA_BACKEND != "rtsp" and Config.SYSTEM_MODE != "real":
            shutdown_rtsp_ingest()
            time.sleep(2)
            continue

        stream_rows = _get_stream_configs()
        active_ids: set[str] = set()
        for row in stream_rows:
            camera_id = str(row.get("camera_id") or "").strip()
            stream_url = str(row.get("stream_url") or "").strip()
            if not camera_id:
                continue
            active_ids.add(camera_id)

            if not stream_url:
                _stop_worker(camera_id)
                _set_offline_placeholder(camera_id, "Stream URL not configured")
                _update_stream_row(camera_id, status="offline", last_frame_at=None)
                continue

            _start_or_update_worker(camera_id, stream_url, _target_fps(row.get("fps_target")))

        with _cache_lock:
            known_workers = set(_worker_threads.keys())
        for camera_id in sorted(known_workers - active_ids):
            _stop_worker(camera_id)

        time.sleep(2.0)


def shutdown_rtsp_ingest() -> None:
    """Release all active RTSP/HTTP capture handles."""
    with _cache_lock:
        camera_ids = set(_worker_threads.keys()) | set(_worker_stop_events.keys()) | set(_captures.keys())
    for camera_id in sorted(camera_ids):
        _stop_worker(camera_id)
