"""
Camera Routes — CCTV camera list, frames, streams, per-camera vehicle data
"""

import json
import os
import re
import time
from datetime import datetime, timezone
from flask import Blueprint, Response, jsonify, request, stream_with_context
from config import Config
from services.live_state import get_latest_camera_state, get_latest_camera_vehicle_tracks
from services.camera_sync import get_offline_camera_inventory, repair_camera_inventory_if_needed
from services.mapping import (
    get_junction_display_name,
    get_research_target_by_camera,
    get_research_target_by_junction,
    looks_like_machine_id,
)
from services.rtsp_ingest import get_latest_frame, get_stream_status
from cctv import is_in_pathumwan

cameras_bp = Blueprint("cameras", __name__, url_prefix="/api/cameras")

_sim = None

# Load defined camera IDs so we can enrich labels and detect isolated road configs
_DEFINED_IDS: set = set()
_ROAD_NAME_MAP: dict = {}
_CAMERA_DEF_MAP: dict = {}
_CAMERA_DEF_BY_JUNCTION: dict = {}
try:
    _roads_path = os.path.join(Config.PROJECT_ROOT, "data", "pathumwan_roads.json")
    with open(_roads_path, encoding="utf-8") as _f:
        _rd = json.load(_f)
    _DEFINED_IDS = {
        cam["id"]
        for cam in _rd.get("cameras", [])
        if str(cam.get("status", "active")).lower() == "active"
    }
    _ROAD_NAME_MAP = {r.get("code"): r.get("name_th", r.get("code")) for r in _rd.get("roads", []) if r.get("code")}
    _CAMERA_DEF_MAP = {
        cam.get("id"): cam
        for cam in _rd.get("cameras", [])
        if cam.get("id") and str(cam.get("status", "active")).lower() == "active"
    }
    _CAMERA_DEF_BY_JUNCTION = {
        str(cam.get("junction") or "").strip().lower(): cam
        for cam in _rd.get("cameras", [])
        if cam.get("id") and str(cam.get("junction") or "").strip()
    }
except Exception:
    pass


def init_camera_routes(simulation_module):
    global _sim
    _sim = simulation_module


def _live_mode_enabled() -> bool:
    return Config.SYSTEM_MODE == "real" or Config.CAMERA_BACKEND == "rtsp"


def _placeholder_frame(message: str) -> bytes:
    from cctv_renderer import render_placeholder

    return render_placeholder(message)


def _media_headers(stream: bool = False) -> dict[str, str]:
    headers = {
        "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
        "Pragma": "no-cache",
        "Expires": "0",
        "X-Accel-Buffering": "no",
        "X-Content-Type-Options": "nosniff",
    }
    if stream:
        headers["Connection"] = "keep-alive"
    return headers


def _mjpeg_frame(frame: bytes) -> bytes:
    return b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"

def _stream_sleep_seconds(default_fps: float) -> float:
    try:
        requested_fps = float(request.args.get("fps", default_fps) or default_fps)
    except (TypeError, ValueError):
        requested_fps = float(default_fps)
    fps = max(1.0, min(12.0, requested_fps))
    return 1.0 / fps



def _sanitize_camera_alias(raw_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9]+", "_", str(raw_id or "")).strip("_")
    if not safe:
        safe = "TLS"
    safe = safe[:32]
    import hashlib
    suffix = hashlib.md5(str(raw_id or "").encode("utf-8")).hexdigest()[:6]
    return f"TLS_{safe}_{suffix}"


def _as_float(value, default=0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _timestamp_string(value) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else str(value or "")


def _normalize_counts(raw: dict | None) -> dict[str, int]:
    source = raw or {}
    counts = {
        "car": int(source.get("car", 0) or 0),
        "motorcycle": int(source.get("motorcycle", 0) or 0),
        "bus": int(source.get("bus", 0) or 0),
        "truck": int(source.get("truck", 0) or 0),
    }
    counts["total"] = int(source.get("total", sum(counts.values())) or 0)
    return counts


def _detection_camera_candidates(camera_id: str) -> set[str]:
    requested = str(camera_id or "").strip()
    candidates = {requested} if requested else set()
    resolved = _resolve_sim_camera_id(requested)
    if resolved:
        candidates.add(str(resolved))
    if _sim is not None:
        for cam in list(getattr(_sim, "camera_points", []) or []):
            aliases = {
                str(cam.get("camera_id") or "").strip(),
                str(cam.get("id") or "").strip(),
                str(cam.get("sumo_tls_id") or "").strip(),
            }
            if candidates & aliases:
                candidates.update(alias for alias in aliases if alias)
    return {candidate for candidate in candidates if candidate}


def _latest_detection_counts(camera_id: str) -> dict[str, object] | None:
    candidates = _detection_camera_candidates(camera_id)
    if not candidates:
        return None
    try:
        from database.connection import get_session
        from database.models import TrafficDetection

        session = get_session()
        try:
            det = (
                session.query(TrafficDetection)
                .filter(TrafficDetection.camera_id.in_(sorted(candidates)))
                .order_by(TrafficDetection.timestamp.desc())
                .first()
            )
        finally:
            session.close()

        if det is None:
            return None

        ts_value = getattr(det, "timestamp", None)
        if hasattr(ts_value, "tzinfo"):
            ts_for_age = ts_value
            if ts_for_age.tzinfo is None:
                ts_for_age = ts_for_age.replace(tzinfo=timezone.utc)
            age_seconds = max(0.0, (datetime.now(timezone.utc) - ts_for_age.astimezone(timezone.utc)).total_seconds())
            if age_seconds > max(float(Config.STALE_THRESHOLD_SECONDS) * 2.0, 15.0):
                return None

        return {
            "camera_id": str(getattr(det, "camera_id", camera_id) or camera_id),
            "counts": _normalize_counts(getattr(det, "vehicle_counts", {}) or {}),
            "timestamp": _timestamp_string(ts_value),
            "source": "yolo-detection" if float(getattr(det, "confidence_avg", 0.0) or 0.0) > 0 else "camera-detection",
        }
    except Exception:
        return None


def _camera_identity(camera: dict[str, object] | None) -> str:
    if not camera:
        return ""
    return str(
        camera.get("sumo_tls_id")
        or camera.get("junction_id")
        or camera.get("camera_id")
        or ""
    )


def _clean_text(value) -> str:
    return str(value or "").strip()


def _format_road_label(value: str) -> str:
    label = _clean_text(value)
    if not label:
        return ""
    return label if label.startswith("ถนน") else f"ถนน{label}"


def _build_camera_labels(camera_id: str, base_name: str, road_name: str, junction_name: str) -> tuple[str, str]:
    name = _clean_text(base_name)
    road = _clean_text(road_name)
    junction = _clean_text(junction_name)
    road_label = _format_road_label(road)

    if name and not looks_like_machine_id(name) and name not in {road, junction}:
        display_name = name
    elif junction and road:
        display_name = f"{junction} • {road}"
    else:
        display_name = junction or road or name or camera_id

    detail_parts: list[str] = []
    if junction and junction not in display_name:
        detail_parts.append(junction)
    if road_label and road not in display_name and road_label not in display_name:
        detail_parts.append(road_label)
    detail_parts.append(f"ID {camera_id}")

    deduped: list[str] = []
    for part in detail_parts:
        if part and part not in deduped:
            deduped.append(part)

    return display_name, " • ".join(deduped)


def _find_defined_camera(
    camera_id: str = "",
    raw_junction: str = "",
    junction_id: str = "",
) -> dict[str, object]:
    if camera_id:
        hit = _CAMERA_DEF_MAP.get(str(camera_id))
        if hit:
            return hit
    if raw_junction:
        hit = _CAMERA_DEF_BY_JUNCTION.get(str(raw_junction).strip().lower())
        if hit:
            return hit
    if junction_id:
        research_target = get_research_target_by_junction().get(str(junction_id).strip())
        if research_target:
            hit = _CAMERA_DEF_MAP.get(str(research_target.get("camera_id") or ""))
            if hit:
                return hit
    return {}


def _resolve_sim_camera_id(camera_id: str) -> str:
    requested = str(camera_id or "").strip()
    if not requested or _sim is None:
        return requested
    research_by_junction = get_research_target_by_junction()
    research_by_camera = get_research_target_by_camera()
    requested_def = _CAMERA_DEF_MAP.get(requested, {})
    if not requested_def and requested in research_by_camera:
        requested_def = dict(research_by_camera.get(requested) or {})
    for cam in list(getattr(_sim, "camera_points", []) or []):
        sumo_tls_id = str(cam.get("sumo_tls_id") or "").strip()
        defined = _find_defined_camera(
            str(cam.get("camera_id") or "").strip(),
            str(cam.get("junction") or "").strip(),
            sumo_tls_id,
        )
        research_target = research_by_junction.get(sumo_tls_id, {})
        candidates = {
            str(cam.get("camera_id") or "").strip(),
            str(cam.get("id") or "").strip(),
            sumo_tls_id,
            _sanitize_camera_alias(sumo_tls_id),
            str(defined.get("id") or "").strip(),
            str(research_target.get("camera_id") or "").strip(),
        }
        if requested in candidates:
            return str(cam.get("camera_id") or cam.get("id") or requested)
    if requested_def:
        try:
            target_lat = float(requested_def.get("lat"))
            target_lng = float(requested_def.get("lng"))
        except (TypeError, ValueError):
            target_lat = target_lng = None
        if target_lat is not None and target_lng is not None:
            nearest = None
            nearest_dist = float("inf")
            for cam in list(getattr(_sim, "camera_points", []) or []):
                try:
                    cam_lat = float(cam.get("lat"))
                    cam_lng = float(cam.get("lng"))
                except (TypeError, ValueError):
                    continue
                dist = (cam_lat - target_lat) ** 2 + (cam_lng - target_lng) ** 2
                if dist < nearest_dist:
                    nearest = cam
                    nearest_dist = dist
            if nearest:
                return str(nearest.get("camera_id") or nearest.get("id") or requested)
    return requested


def _build_defined_camera_fallback(camera_id: str, idx: int) -> dict[str, object] | None:
    def_row = _CAMERA_DEF_MAP.get(camera_id, {})
    if not def_row:
        return None
    road_code = str(def_row.get("road") or "")
    road_name = _ROAD_NAME_MAP.get(road_code, road_code)
    raw_junction = str(def_row.get("junction") or "")
    display_junction = get_junction_display_name(raw_junction, def_row.get("name")) or raw_junction
    base_name = str(def_row.get("name") or camera_id)
    display_name, location_hint = _build_camera_labels(
        camera_id,
        base_name,
        str(road_name),
        display_junction,
    )
    research_target = get_research_target_by_camera().get(camera_id)
    research_order = 9999
    if research_target:
        try:
            research_order = int(str(research_target.get("id", "0")).rsplit("-", 1)[-1])
        except Exception:
            research_order = 0
        display_name = str(research_target.get("camera_label_th") or research_target.get("label_th") or display_name)
    return {
        "id": idx + 1,
        "camera_id": camera_id,
        "name": base_name,
        "display_name": display_name,
        "location_hint": location_hint,
        "road": road_name,
        "road_id": road_code,
        "lat": float(def_row.get("lat") or 0.0),
        "lng": float(def_row.get("lng") or 0.0),
        "junction": display_junction,
        "junction_id": "",
        "sumo_tls_id": "",
        "stream_status": "offline",
        "freshness_seconds": float(Config.STALE_THRESHOLD_SECONDS),
        "research_target": bool(research_target),
        "research_order": research_order,
    }


@cameras_bp.route("/", strict_slashes=False)
def api_cameras():
    """Return CCTV cameras.

    Source of truth (for consistency across pages):
    1) Active cameras from DB
    2) Union with live SUMO camera inventory when simulation is active
    3) Fallback to the defined camera IDs from data/pathumwan_roads.json
    This avoids collapsing the frontend back to the stale JSON-only DB inventory.
    """
    try:
        repair_camera_inventory_if_needed()
    except Exception:
        pass

    db_cameras_by_identity: dict[str, dict[str, object]] = {}
    try:
        from database.connection import get_session
        from database.models import Camera as CameraModel
        session = get_session()
        try:
            rows = session.query(CameraModel).filter(CameraModel.status == "active").all()
            for r in rows:
                camera_id = str(getattr(r, "camera_id", "") or "")
                if not camera_id:
                    continue
                row_payload = {
                    "id": int(getattr(r, "id", 0) or 0),
                    "camera_id": camera_id,
                    "name": str(getattr(r, "name", "") or camera_id),
                    "road": str(getattr(r, "road", "") or ""),
                    "road_id": str(getattr(r, "road_id", "") or getattr(r, "road", "") or ""),
                    "lat": float(getattr(r, "lat", 0) or 0),
                    "lng": float(getattr(r, "lng", 0) or 0),
                    "junction": str(getattr(r, "junction", "") or ""),
                    "junction_id": str(getattr(r, "junction_id", "") or getattr(r, "sumo_tls_id", "") or ""),
                    "sumo_tls_id": str(getattr(r, "sumo_tls_id", "") or ""),
                }
                db_cameras_by_identity[_camera_identity(row_payload) or camera_id] = row_payload
        finally:
            session.close()
    except Exception:
        pass

    offline_by_identity = {
        _camera_identity(camera) or str(camera.get("camera_id") or ""): camera
        for camera in get_offline_camera_inventory()
        if camera.get("camera_id")
    }

    sim_by_identity: dict[str, dict[str, object]] = {}
    if _sim and _sim.sim_active and getattr(_sim, "camera_points", None):
        for c in _sim.camera_points:
            identity = _camera_identity(c)
            if identity and identity not in sim_by_identity:
                sim_by_identity[identity] = c

    research_by_camera = get_research_target_by_camera()
    research_by_junction = get_research_target_by_junction()
    research_camera_ids = {
        str(camera_id)
        for camera_id in research_by_camera.keys()
        if str(camera_id).strip()
    }

    # Decide which camera IDs to expose.
    # The 14 Pathumwan/edge intersections plus 2 Ratchathewi feeder junctions
    # are canonical research targets, so keep them visible even when the live
    # SUMO inventory is smaller or a feeder sits just outside the Pathumwan bbox.
    if sim_by_identity:
        allowed_ids = set(sim_by_identity.keys()) | research_camera_ids
    elif db_cameras_by_identity or offline_by_identity:
        allowed_ids = set(db_cameras_by_identity.keys()) | set(offline_by_identity.keys()) | set(_DEFINED_IDS) | research_camera_ids
    else:
        allowed_ids = set(_DEFINED_IDS) | research_camera_ids

    live_camera_state = {
        str(item.get("camera_id") or ""): item
        for item in get_latest_camera_state()
    } if _live_mode_enabled() else {}

    cam_list = []
    seen_camera_ids: set[str] = set()
    for idx, identity in enumerate(sorted(allowed_ids)):
        db_row = db_cameras_by_identity.get(identity)
        sim_row = sim_by_identity.get(identity)
        offline_row = offline_by_identity.get(identity)
        raw_camera_id = str(
            (sim_row or {}).get("camera_id")
            or (offline_row or {}).get("camera_id")
            or (db_row or {}).get("camera_id")
            or (offline_row or {}).get("camera_id")
            or identity
        )
        raw_junction = str(
            (db_row or {}).get("junction")
            or (sim_row or {}).get("junction")
            or (offline_row or {}).get("junction")
            or ""
        )
        junction_id = str(
            (db_row or {}).get("junction_id")
            or (sim_row or {}).get("sumo_tls_id")
            or (offline_row or {}).get("sumo_tls_id")
            or ""
        )
        def_row = _find_defined_camera(raw_camera_id, raw_junction, junction_id)
        camera_id = str(def_row.get("id") or raw_camera_id or identity)
        if camera_id in seen_camera_ids:
            continue

        research_target = (
            research_by_camera.get(camera_id)
            or research_by_junction.get(str((db_row or {}).get("junction_id") or (sim_row or {}).get("sumo_tls_id") or (offline_row or {}).get("sumo_tls_id") or ""))
            or research_by_junction.get(str((db_row or {}).get("sumo_tls_id") or (sim_row or {}).get("sumo_tls_id") or (offline_row or {}).get("sumo_tls_id") or ""))
            or research_by_junction.get(str(def_row.get("junction") or ""))
        )

        # Prefer live SUMO position when available
        lat = (sim_row or {}).get("lat") if sim_row else None
        lng = (sim_row or {}).get("lng") if sim_row else None
        if lat is None and db_row:
            lat = db_row.get("lat")
            lng = db_row.get("lng")
        if lat is None and offline_row:
            lat = offline_row.get("lat")
            lng = offline_row.get("lng")
        if lat is None and def_row:
            lat = def_row.get("lat")
            lng = def_row.get("lng")

        if lat is None or lng is None:
            continue

        # Drop any TLS outside the Pathumwan bbox (ghost cameras from the wider
        # OSM extract such as 13.7471, 100.4996). Defensive filter in case the
        # DB still has legacy rows that slipped past the sync layer.
        try:
            if not is_in_pathumwan(float(lat), float(lng)) and not research_target:
                continue
        except (TypeError, ValueError):
            continue

        road_code = ""
        if db_row and db_row.get("road"):
            road_code = db_row.get("road_id") or db_row.get("road")
        elif sim_row and sim_row.get("road"):
            road_code = sim_row.get("road")
        elif offline_row and offline_row.get("road"):
            road_code = str(offline_row.get("road") or "")
        elif def_row and def_row.get("road"):
            road_code = def_row.get("road")
        road_name = _ROAD_NAME_MAP.get(road_code, road_code)
        raw_junction = raw_junction or str(def_row.get("junction") or "")
        junction_name = get_junction_display_name(
            junction_id
            or raw_junction,
            raw_junction,
        ) or _clean_text(raw_junction)
        base_name = (
            (db_row or {}).get("name")
            or (sim_row or {}).get("name")
            or (offline_row or {}).get("name")
            or def_row.get("name")
            or camera_id
        )
        display_name, location_hint = _build_camera_labels(
            camera_id,
            str(base_name),
            str(road_name),
            str(junction_name),
        )

        db_pk = None
        if db_row and db_row.get("id") is not None:
            try:
                db_pk = int(str(db_row.get("id")))
            except Exception:
                db_pk = None

        research_order = 9999
        if research_target:
            try:
                research_order = int(str(research_target.get("id", "0")).rsplit("-", 1)[-1])
            except Exception:
                research_order = 0
            display_name = str(research_target.get("camera_label_th") or research_target.get("label_th") or display_name)

        seen_camera_ids.add(camera_id)
        cam_list.append({
            # Use a simple sequential id for frontend list keys; camera_id is the canonical identifier.
            "id": db_pk if db_pk is not None else (idx + 1),
            "camera_id": camera_id,
            "name": str(base_name),
            "display_name": display_name,
            "location_hint": location_hint,
            "road": road_name,
            "road_id": road_code,
            "lat": lat,
            "lng": lng,
            "junction": junction_name,
            "junction_id": junction_id,
            "sumo_tls_id": (db_row or {}).get("sumo_tls_id") or (sim_row or {}).get("sumo_tls_id") or (offline_row or {}).get("sumo_tls_id") or junction_id,
            "stream_status": str(live_camera_state.get(camera_id, {}).get("stream_status", "online" if sim_row else "offline")),
            "freshness_seconds": _as_float(live_camera_state.get(camera_id, {}).get("freshness_seconds", 0.0 if sim_row else Config.STALE_THRESHOLD_SECONDS)),
            "research_target": bool(research_target),
            "research_order": research_order,
        })

    if not cam_list:
        for idx, camera_id in enumerate(sorted(_DEFINED_IDS)):
            fallback = _build_defined_camera_fallback(camera_id, idx)
            if fallback is not None:
                cam_list.append(fallback)

    cam_list.sort(key=lambda camera: (0 if camera.get("research_target") else 1, int(camera.get("research_order") or 9999), str(camera.get("display_name") or camera.get("name") or "")))
    return jsonify({
        "status": "ok",
        "cameras": cam_list,
        "active": bool(getattr(_sim, "sim_active", False)),
        "error": getattr(_sim, "sim_error", ""),
    })


@cameras_bp.route("/<camera_id>/frame")
def api_camera_frame(camera_id):
    """Get a single CCTV frame (JPEG image) rendered from SUMO data.

    Always returns a JPEG (200) — falls back to a labelled placeholder when the
    camera is offline / sim not ready / not yet calibrated, so the frontend
    `<img>` never triggers `onError` for a rostered camera.
    """
    if _live_mode_enabled():
        detect = request.args.get("detect", "false").lower() == "true"
        frame_bytes = get_latest_frame(str(camera_id), detect=detect)
        return Response(
            frame_bytes or _placeholder_frame("Camera offline"),
            mimetype="image/jpeg",
            headers=_media_headers(),
        )

    if _sim is None:
        return Response(
            _placeholder_frame("รอ Runtime เริ่มต้น"),
            mimetype="image/jpeg",
            headers=_media_headers(),
        )

    sim = _sim
    resolved_camera_id = _resolve_sim_camera_id(camera_id)

    detect = request.args.get("detect", "false").lower() == "true"
    zoom_level = request.args.get("zoom", "near")
    # Prefer pre-cached frame; fall back to direct render
    if not detect:
        frame_bytes = sim.get_cached_frame(resolved_camera_id)
        if frame_bytes:
            return Response(frame_bytes, mimetype="image/jpeg", headers=_media_headers())

    frame_bytes = sim.capture_cctv_frame(resolved_camera_id, zoom_level=zoom_level, show_detection=detect)
    if not frame_bytes:
        frame_bytes = _placeholder_frame(f"กล้อง {resolved_camera_id} ยังไม่พร้อมใช้งาน")
    return Response(frame_bytes, mimetype="image/jpeg", headers=_media_headers())


@cameras_bp.route("/<camera_id>/stream")
def api_camera_stream(camera_id):
    """MJPEG stream for continuous CCTV viewing.

    Always emits valid JPEG frames — when the underlying source is unavailable
    (sim down, frame render failed, etc.) we yield a labelled placeholder so the
    `<img>` MJPEG client never errors out for a rostered camera.
    """
    if _live_mode_enabled():
        def generate_live():
            while True:
                try:
                    frame = get_latest_frame(str(camera_id), detect=False) or _placeholder_frame("Camera offline")
                    yield _mjpeg_frame(frame)
                except GeneratorExit:
                    return
                except Exception:
                    yield _mjpeg_frame(_placeholder_frame("Camera stream recovering"))
                    time.sleep(1.0)
                    continue
                time.sleep(_stream_sleep_seconds(5.5))

        return Response(
            stream_with_context(generate_live()),
            mimetype="multipart/x-mixed-replace; boundary=frame",
            headers=_media_headers(stream=True),
        )

    if _sim is None:
        # Stream a placeholder loop instead of returning 503 — keeps the UI
        # showing a friendly "waiting" message instead of an `<img>` error.
        def generate_waiting():
            placeholder = _placeholder_frame("รอ Runtime เริ่มต้น")
            while True:
                try:
                    yield _mjpeg_frame(placeholder)
                except GeneratorExit:
                    return
                except Exception:
                    time.sleep(1.0)
                    continue
                time.sleep(1.0)

        return Response(
            stream_with_context(generate_waiting()),
            mimetype="multipart/x-mixed-replace; boundary=frame",
            headers=_media_headers(stream=True),
        )

    sim = _sim
    resolved_camera_id = _resolve_sim_camera_id(camera_id)
    zoom_level = request.args.get("zoom", "near")

    def generate():
        last_frame_id = 0
        while True:
            try:
                # Serve from cache (populated by camera_capture_loop)
                frame = sim.get_cached_frame(resolved_camera_id)
                if not frame:
                    frame = sim.capture_cctv_frame(resolved_camera_id, zoom_level=zoom_level)
                if not frame:
                    frame = _placeholder_frame(f"กล้อง {resolved_camera_id} ยังไม่พร้อมใช้งาน")
                # Hash by id() of the bytes object — capture loop replaces the
                # object when a new frame is ready, so we only send fresh bytes.
                fid = id(frame)
                if fid != last_frame_id:
                    last_frame_id = fid
                    yield _mjpeg_frame(frame)
            except GeneratorExit:
                return
            except Exception:
                yield _mjpeg_frame(_placeholder_frame("Camera stream recovering"))
                time.sleep(1.0)
                continue
            time.sleep(_stream_sleep_seconds(12.0))

    return Response(
        stream_with_context(generate()),
        mimetype="multipart/x-mixed-replace; boundary=frame",
        headers=_media_headers(stream=True),
    )


@cameras_bp.route("/<camera_id>/detect/stream")
def api_camera_detect_stream(camera_id):
    """MJPEG stream with YOLO detection overlays (rendered + detected)."""
    if _live_mode_enabled():
        def generate_live_detect():
            while True:
                try:
                    frame = get_latest_frame(str(camera_id), detect=True)
                    if not frame:
                        frame = get_latest_frame(str(camera_id), detect=False)
                    frame = frame or _placeholder_frame("Detection unavailable")
                    yield _mjpeg_frame(frame)
                except GeneratorExit:
                    return
                except Exception:
                    yield _mjpeg_frame(_placeholder_frame("Detection stream recovering"))
                    time.sleep(1.0)
                    continue
                time.sleep(_stream_sleep_seconds(4.0))

        return Response(
            stream_with_context(generate_live_detect()),
            mimetype="multipart/x-mixed-replace; boundary=frame",
            headers=_media_headers(stream=True),
        )

    if _sim is None:
        def generate_waiting_detect():
            placeholder = _placeholder_frame("รอ Runtime เริ่มต้น")
            while True:
                try:
                    yield _mjpeg_frame(placeholder)
                except GeneratorExit:
                    return
                except Exception:
                    time.sleep(1.0)
                    continue
                time.sleep(1.0)

        return Response(
            stream_with_context(generate_waiting_detect()),
            mimetype="multipart/x-mixed-replace; boundary=frame",
            headers=_media_headers(stream=True),
        )

    sim = _sim
    resolved_camera_id = _resolve_sim_camera_id(camera_id)
    zoom_level = request.args.get("zoom", "near")

    # Mark as requested so the background cache loop starts generating overlay frames.
    try:
        sim.mark_detect_stream_camera(resolved_camera_id)
    except Exception:
        pass

    def generate():
        placeholder = _placeholder_frame("กำลังเตรียม YOLO overlay")
        while True:
            try:
                frame = None
                try:
                    frame = sim.get_cached_detect_frame(resolved_camera_id)
                except Exception:
                    frame = None

                # If cache hasn't warmed up yet, fall back to raw cached frame to
                # keep multi-camera grids responsive (avoid per-client rendering).
                if not frame:
                    try:
                        frame = sim.get_cached_frame(resolved_camera_id)
                    except Exception:
                        frame = None
                frame = frame or placeholder

                yield _mjpeg_frame(frame)
            except GeneratorExit:
                return
            except Exception:
                yield _mjpeg_frame(_placeholder_frame("Detection stream recovering"))
                time.sleep(1.0)
                continue
            time.sleep(_stream_sleep_seconds(4.0))

    return Response(
        stream_with_context(generate()),
        mimetype="multipart/x-mixed-replace; boundary=frame",
        headers=_media_headers(stream=True),
    )


@cameras_bp.route("/<camera_id>/analytics/stream")
def api_camera_analytics_stream(camera_id):
    """MJPEG stream for the schematic/analytics camera background.

    This endpoint intentionally prefers the SUMO renderer even when the system
    is configured with a real/RTSP camera backend.  The UI uses it as the large
    background layer, while `/stream` remains available for the small raw/live
    inset view.
    """
    detect = request.args.get("detect", "true").lower() not in {"0", "false", "no", "off"}
    zoom_level = request.args.get("zoom", "near")

    if _sim is None or not getattr(_sim, "sim_active", False):
        def generate_waiting_analytics():
            while True:
                try:
                    if _live_mode_enabled():
                        frame = get_latest_frame(str(camera_id), detect=detect)
                        if not frame:
                            frame = get_latest_frame(str(camera_id), detect=False)
                        frame = frame or _placeholder_frame("Analytics runtime waiting")
                    else:
                        frame = _placeholder_frame("รอ Analytics Runtime เริ่มต้น")
                    yield _mjpeg_frame(frame)
                except GeneratorExit:
                    return
                except Exception:
                    time.sleep(1.0)
                    continue
                time.sleep(0.5)

        return Response(
            stream_with_context(generate_waiting_analytics()),
            mimetype="multipart/x-mixed-replace; boundary=frame",
            headers=_media_headers(stream=True),
        )

    sim = _sim
    resolved_camera_id = _resolve_sim_camera_id(camera_id)

    def generate():
        last_frame_id = 0
        while True:
            try:
                if detect:
                    frame = sim.capture_cctv_frame(
                        resolved_camera_id,
                        zoom_level=zoom_level,
                        show_detection=True,
                        show_hud=True,
                        show_camera_marker=True,
                        show_detection_labels=True,
                        show_vehicle_labels=True,
                    )
                else:
                    frame = sim.capture_cctv_frame(
                        resolved_camera_id,
                        zoom_level=zoom_level,
                        show_detection=False,
                        show_hud=True,
                        show_camera_marker=True,
                        show_detection_labels=False,
                        show_vehicle_labels=True,
                    )
                if not frame:
                    frame = _placeholder_frame(f"กล้อง {resolved_camera_id} ยังไม่พร้อมใช้งาน")
                fid = id(frame)
                if detect or fid != last_frame_id:
                    last_frame_id = fid
                    yield _mjpeg_frame(frame)
            except GeneratorExit:
                return
            except Exception:
                yield _mjpeg_frame(_placeholder_frame("Analytics stream recovering"))
                time.sleep(1.0)
                continue
            time.sleep(_stream_sleep_seconds(4.0 if detect else 12.0))

    return Response(
        stream_with_context(generate()),
        mimetype="multipart/x-mixed-replace; boundary=frame",
        headers=_media_headers(stream=True),
    )


@cameras_bp.route("/<camera_id>/vehicles")
def api_camera_vehicles(camera_id):
    """Get all vehicles near a specific camera (JSON)."""
    if _live_mode_enabled():
        vehicles = get_latest_camera_vehicle_tracks(str(camera_id))
        counts = {"car": 0, "motorcycle": 0, "bus": 0, "truck": 0}
        for vehicle in vehicles:
            vehicle_class = str(vehicle.get("class") or "car")
            if vehicle_class in counts:
                counts[vehicle_class] += 1
        counts["total"] = sum(counts.values())
        latest_ts = max((str(item.get("last_seen_at") or "") for item in vehicles), default="")
        return jsonify({
            "status": "ok",
            "camera_id": str(camera_id),
            "camera_name": str(camera_id),
            "vehicles": vehicles,
            "counts": counts,
            "timestamp": latest_ts,
            "sim_step": 0,
            "source": "live-tracks",
        })

    if _sim is None or not _sim.sim_active:
        return jsonify({"status": "waiting", "vehicles": [], "counts": {}})

    resolved_camera_id = _resolve_sim_camera_id(camera_id)
    default_radius = getattr(_sim, "DEFAULT_RADIUS", 40)
    radius = float(request.args.get("radius", default_radius))
    vehicles = _sim.get_camera_vehicles(resolved_camera_id, radius)
    counts = {"car": 0, "motorcycle": 0, "bus": 0, "truck": 0}
    for v in vehicles:
        cls = v.get("class", "car")
        if cls in counts:
            counts[cls] += 1
    counts["total"] = sum(counts.values())

    camera = _sim._find_camera(resolved_camera_id)

    return jsonify({
        "status": "ok",
        "camera_id": resolved_camera_id,
        "camera_name": camera.get("name", "") if camera else "",
        "vehicles": vehicles,
        "counts": counts,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "sim_step": _sim.step,
    })


@cameras_bp.route("/search")
def api_camera_search():
    """Search cameras by name or location."""
    if _sim is None and not _live_mode_enabled():
        return jsonify({"status": "waiting", "cameras": []})

    q = request.args.get("q", "").lower()
    if _live_mode_enabled() and (_sim is None or not getattr(_sim, "camera_points", None)):
        results = [
            c for c in get_latest_camera_state()
            if q in str(c.get("name", "")).lower() or q in str(c.get("camera_id", "")).lower()
        ]
    else:
        camera_points = list(getattr(_sim, "camera_points", []) or [])
        results = [
            c for c in camera_points
            if q in c.get("name", "").lower() or q in str(c.get("camera_id", c.get("id", ""))).lower()
        ]
    return jsonify({"status": "ok", "cameras": results})


@cameras_bp.route("/<camera_id>/detect")
def api_camera_detect(camera_id):
    """Run YOLO-style detection on a single frame and return annotated JPEG."""
    if _live_mode_enabled():
        frame_bytes = get_latest_frame(str(camera_id), detect=True) or get_latest_frame(str(camera_id), detect=False)
        return Response(
            frame_bytes or _placeholder_frame("Detection unavailable"),
            mimetype="image/jpeg",
            headers=_media_headers(),
        )

    if _sim is None:
        return Response(
            _placeholder_frame("รอ Runtime เริ่มต้น"),
            mimetype="image/jpeg",
            headers=_media_headers(),
        )

    resolved_camera_id = _resolve_sim_camera_id(camera_id)

    # Mark as requested so the background loop will start generating detect frames
    try:
        _sim.mark_detect_stream_camera(resolved_camera_id)
    except Exception:
        pass

    frame_bytes = None
    try:
        frame_bytes = _sim.get_cached_detect_frame(resolved_camera_id)
    except Exception:
        pass

    # Fallback to normal frame while waiting for background thread to catch up
    if not frame_bytes:
        try:
            frame_bytes = _sim.get_cached_frame(resolved_camera_id)
        except Exception:
            pass

    if not frame_bytes:
        frame_bytes = _placeholder_frame(f"กล้อง {resolved_camera_id} กำลังโหลด...")
        
    return Response(frame_bytes, mimetype="image/jpeg", headers=_media_headers())



@cameras_bp.route("/<camera_id>/counts")
def api_camera_counts(camera_id):
    """Get vehicle counts for a camera."""
    cam_id = str(camera_id)

    if _live_mode_enabled():
        live_row = next((row for row in get_latest_camera_state() if str(row.get("camera_id") or "") == cam_id), None)
        if live_row is not None:
            timestamp_value = live_row.get("timestamp")
            stream_status = get_stream_status(cam_id)
            return jsonify({
                "status": "ok",
                "camera_id": cam_id,
                "counts": live_row.get("counts", {"car": 0, "motorcycle": 0, "bus": 0, "truck": 0, "total": 0}),
                "timestamp": _timestamp_string(timestamp_value),
                "source": str(live_row.get("source") or "live-state"),
                "stream_status": stream_status.get("status", "offline"),
            })

    # Prefer the latest YOLO/detection snapshot.  The UI labels this as YOLO
    # analytics, and downstream dashboard widgets should see the same source.
    detection_snapshot = _latest_detection_counts(cam_id)
    if detection_snapshot is not None:
        return jsonify({"status": "ok", **detection_snapshot})

    # Fallback to live SUMO proximity counts if detection has not produced a
    # fresh snapshot yet.  This keeps the camera usable during warm-up.
    if _sim and getattr(_sim, "sim_active", False):
        try:
            resolved_camera_id = _resolve_sim_camera_id(cam_id)
            counts = _normalize_counts(_sim.get_camera_counts(resolved_camera_id))
            return jsonify({
                "status": "ok",
                "camera_id": resolved_camera_id,
                "counts": counts,
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                "source": "sumo-sim-fallback",
            })
        except Exception:
            pass

    return jsonify({
        "status": "ok",
        "camera_id": cam_id,
        "counts": {"car": 0, "motorcycle": 0, "bus": 0, "truck": 0, "total": 0},
        "timestamp": "",
        "source": "unknown",
    })
