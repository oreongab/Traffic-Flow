"""
Camera Routes — CCTV camera list, frames, streams, per-camera vehicle data
"""

import json
import os
import time
from flask import Blueprint, Response, jsonify, request
from config import Config
from services.live_state import get_latest_camera_state, get_latest_camera_vehicle_tracks
from services.camera_sync import get_offline_camera_inventory, repair_camera_inventory_if_needed
from services.rtsp_ingest import get_latest_frame, get_stream_status
from cctv import is_in_pathumwan

cameras_bp = Blueprint("cameras", __name__, url_prefix="/api/cameras")

_sim = None

# Load defined camera IDs so we can enrich labels and detect isolated road configs
_DEFINED_IDS: set = set()
_ROAD_NAME_MAP: dict = {}
_CAMERA_DEF_MAP: dict = {}
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


def _as_float(value, default=0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _timestamp_string(value) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else str(value or "")


def _camera_identity(camera: dict[str, object] | None) -> str:
    if not camera:
        return ""
    return str(
        camera.get("sumo_tls_id")
        or camera.get("junction_id")
        or camera.get("camera_id")
        or ""
    )


@cameras_bp.route("/")
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

    # Decide which camera IDs to expose.
    # Keep DB-backed metadata, but do not let a stale DB hide live TLS cameras.
    if db_cameras_by_identity or sim_by_identity or offline_by_identity:
        allowed_ids = set(db_cameras_by_identity.keys()) | set(sim_by_identity.keys()) | set(offline_by_identity.keys())
    else:
        allowed_ids = set(_DEFINED_IDS)

    live_camera_state = {
        str(item.get("camera_id") or ""): item
        for item in get_latest_camera_state()
    } if _live_mode_enabled() else {}

    cam_list = []
    for idx, identity in enumerate(sorted(allowed_ids)):
        db_row = db_cameras_by_identity.get(identity)
        sim_row = sim_by_identity.get(identity)
        offline_row = offline_by_identity.get(identity)
        camera_id = str(
            (db_row or {}).get("camera_id")
            or (sim_row or {}).get("camera_id")
            or (offline_row or {}).get("camera_id")
            or identity
        )
        def_row = _CAMERA_DEF_MAP.get(camera_id, {})

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
            if not is_in_pathumwan(float(lat), float(lng)):
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

        db_pk = None
        if db_row and db_row.get("id") is not None:
            try:
                db_pk = int(str(db_row.get("id")))
            except Exception:
                db_pk = None

        cam_list.append({
            # Use a simple sequential id for frontend list keys; camera_id is the canonical identifier.
            "id": db_pk if db_pk is not None else (idx + 1),
            "camera_id": camera_id,
            "name": (db_row or {}).get("name") or (sim_row or {}).get("name") or (offline_row or {}).get("name") or def_row.get("name") or camera_id,
            "road": road_name,
            "road_id": road_code,
            "lat": lat,
            "lng": lng,
            "junction": (db_row or {}).get("junction") or (sim_row or {}).get("junction") or (offline_row or {}).get("junction") or def_row.get("junction") or "",
            "junction_id": (db_row or {}).get("junction_id") or (offline_row or {}).get("sumo_tls_id") or "",
            "sumo_tls_id": (db_row or {}).get("sumo_tls_id") or (sim_row or {}).get("sumo_tls_id") or (offline_row or {}).get("sumo_tls_id") or "",
            "stream_status": str(live_camera_state.get(camera_id, {}).get("stream_status", "online" if sim_row else "offline")),
            "freshness_seconds": _as_float(live_camera_state.get(camera_id, {}).get("freshness_seconds", 0.0 if sim_row else Config.STALE_THRESHOLD_SECONDS)),
        })

    cam_list.sort(key=lambda camera: str(camera.get("name", "")))
    return jsonify({
        "status": "ok",
        "cameras": cam_list,
        "active": bool(getattr(_sim, "sim_active", False)),
        "error": getattr(_sim, "sim_error", ""),
    })


@cameras_bp.route("/<camera_id>/frame")
def api_camera_frame(camera_id):
    """Get a single CCTV frame (JPEG image) rendered from SUMO data."""
    if _live_mode_enabled():
        detect = request.args.get("detect", "false").lower() == "true"
        frame_bytes = get_latest_frame(str(camera_id), detect=detect)
        return Response(frame_bytes or _placeholder_frame("Camera offline"), mimetype="image/jpeg")

    if _sim is None:
        return Response(b"", mimetype="image/jpeg", status=503)

    sim = _sim

    detect = request.args.get("detect", "false").lower() == "true"
    zoom_level = request.args.get("zoom", "near")
    # Prefer pre-cached frame; fall back to direct render
    if not detect:
        frame_bytes = sim.get_cached_frame(camera_id)
        if frame_bytes:
            return Response(frame_bytes, mimetype="image/jpeg")

    frame_bytes = sim.capture_cctv_frame(camera_id, zoom_level=zoom_level, show_detection=detect)
    return Response(frame_bytes, mimetype="image/jpeg")


@cameras_bp.route("/<camera_id>/stream")
def api_camera_stream(camera_id):
    """MJPEG stream for continuous CCTV viewing (rendered from SUMO data)."""
    if _live_mode_enabled():
        def generate_live():
            while True:
                try:
                    frame = get_latest_frame(str(camera_id), detect=False) or _placeholder_frame("Camera offline")
                    yield (
                        b"--frame\r\n"
                        b"Content-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
                    )
                except GeneratorExit:
                    return
                except Exception:
                    break
                time.sleep(0.18)

        return Response(generate_live(), mimetype="multipart/x-mixed-replace; boundary=frame")

    if _sim is None:
        return Response("Simulation not running", status=503)

    sim = _sim
    zoom_level = request.args.get("zoom", "near")

    def generate():
        last_frame_id = 0
        while True:
            try:
                # Serve from cache (populated by camera_capture_loop)
                frame = sim.get_cached_frame(camera_id)
                if not frame:
                    frame = sim.capture_cctv_frame(camera_id, zoom_level=zoom_level)
                if frame:
                    # Hash by id() of the bytes object — capture loop replaces the
                    # object when a new frame is ready, so we only send fresh bytes.
                    fid = id(frame)
                    if fid != last_frame_id:
                        last_frame_id = fid
                        yield (
                            b"--frame\r\n"
                            b"Content-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
                        )
            except GeneratorExit:
                return
            except Exception:
                break
            time.sleep(0.08)

    return Response(
        generate(),
        mimetype="multipart/x-mixed-replace; boundary=frame",
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
                    yield (
                        b"--frame\r\n"
                        b"Content-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
                    )
                except GeneratorExit:
                    return
                except Exception:
                    break
                time.sleep(0.25)

        return Response(generate_live_detect(), mimetype="multipart/x-mixed-replace; boundary=frame")

    if _sim is None:
        return Response("Simulation not running", status=503)

    sim = _sim
    zoom_level = request.args.get("zoom", "near")

    def generate():
        while True:
            try:
                frame = sim.capture_cctv_frame(
                    camera_id, zoom_level=zoom_level, show_detection=True,
                )
                if frame:
                    yield (
                        b"--frame\r\n"
                        b"Content-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
                    )
            except GeneratorExit:
                return
            except Exception:
                break
            time.sleep(0.25)  # detection mode is heavier but should still feel live

    return Response(
        generate(),
        mimetype="multipart/x-mixed-replace; boundary=frame",
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

    default_radius = getattr(_sim, "DEFAULT_RADIUS", 40)
    radius = float(request.args.get("radius", default_radius))
    vehicles = _sim.get_camera_vehicles(camera_id, radius)
    counts = {"car": 0, "motorcycle": 0, "bus": 0, "truck": 0}
    for v in vehicles:
        cls = v.get("class", "car")
        if cls in counts:
            counts[cls] += 1
    counts["total"] = sum(counts.values())

    camera = _sim._find_camera(camera_id)

    return jsonify({
        "status": "ok",
        "camera_id": camera_id,
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
        return Response(frame_bytes or _placeholder_frame("Detection unavailable"), mimetype="image/jpeg")

    if _sim is None:
        return Response(b"", mimetype="image/jpeg", status=503)

    frame_bytes = _sim.capture_cctv_frame(camera_id, show_detection=True)
    if not frame_bytes:
        return Response(b"", mimetype="image/jpeg", status=404)
    return Response(frame_bytes, mimetype="image/jpeg")


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

    # Prioritize live SUMO counts if simulation is active
    if _sim and getattr(_sim, "sim_active", False):
        try:
            counts = _sim.get_camera_counts(cam_id)
            return jsonify({
                "status": "ok",
                "camera_id": cam_id,
                "counts": counts,
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                "source": "sumo-sim",
            })
        except Exception:
            pass

    # Fallback to DB-stored detection (YOLO loop). If no detections exist yet, return zeros.
    try:
        from database.connection import get_session
        from database.models import TrafficDetection

        session = get_session()
        try:
            det = (
                session.query(TrafficDetection)
                .filter(TrafficDetection.camera_id == cam_id)
                .order_by(TrafficDetection.timestamp.desc())
                .first()
            )
        finally:
            session.close()

        if det is not None:
            raw = det.vehicle_counts or {}
            counts = {
                "car": int(raw.get("car", 0) or 0),
                "motorcycle": int(raw.get("motorcycle", 0) or 0),
                "bus": int(raw.get("bus", 0) or 0),
                "truck": int(raw.get("truck", 0) or 0),
            }
            counts["total"] = int(raw.get("total", sum(counts.values())) or 0)
            ts = det.timestamp.isoformat() if getattr(det, "timestamp", None) else ""
            return jsonify({
                "status": "ok",
                "camera_id": cam_id,
                "counts": counts,
                "timestamp": ts,
                "source": "yolo",
            })
    except Exception:
        pass

    return jsonify({
        "status": "ok",
        "camera_id": cam_id,
        "counts": {"car": 0, "motorcycle": 0, "bus": 0, "truck": 0, "total": 0},
        "timestamp": "",
        "source": "yolo",
    })
