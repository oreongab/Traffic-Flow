"""
TraffixFlow — Flask Application Entry Point
=============================================
Initializes database, starts SUMO simulation, CCTV capture loop,
detection loop, traffic index calculation loop, and aggregation loop.
"""

import sys
import os
import json
import threading
from collections.abc import Callable

# Fix Windows console encoding
if sys.platform == "win32":
    # Some environments (older Python / type checkers) don't expose reconfigure()
    stdout_reconfigure = getattr(sys.stdout, "reconfigure", None)
    if callable(stdout_reconfigure):
        stdout_reconfigure(encoding="utf-8", errors="replace")
    stderr_reconfigure = getattr(sys.stderr, "reconfigure", None)
    if callable(stderr_reconfigure):
        stderr_reconfigure(encoding="utf-8", errors="replace")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flask import Flask, jsonify, redirect, request
from flask_cors import CORS
from config import Config
from database.connection import init_db
from services.signal_controller import get_runtime_backends


def _as_float(value, default=0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _as_int(value, default=0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _seed_cameras_from_json():
    """Seed (upsert) cameras, roads, junctions, and approaches from data/pathumwan_roads.json.

    This keeps the camera set consistent across frontend pages and ensures
    camera->road mapping exists for statistics.
    """
    try:
        from database.connection import get_session
        from database.models import (
            Camera as CameraModel,
            Road,
            Junction,
            Approach,
            RuntimeConfig,
            SignalController,
        )

        roads_path = os.path.join(Config.PROJECT_ROOT, "data", "pathumwan_roads.json")
        if not os.path.exists(roads_path):
            return
        with open(roads_path, encoding="utf-8") as f:
            data = json.load(f)

        session = get_session()
        try:
            # Seed roads
            for rd in data.get("roads", []):
                code = rd.get("code")
                if not code:
                    continue
                existing = session.query(Road).filter(Road.road_id == code).first()
                if not existing:
                    session.add(Road(
                        road_id=code,
                        road_name=rd.get("name_th", code),
                        free_flow_speed_kmh=rd.get("free_flow_speed_kmh", 50),
                    ))

            # Seed cameras + collect junction info
            cams = data.get("cameras", []) or []
            junction_set: dict[str, dict] = {}  # junction_id -> {lat, lng, name}
            cam_junction_map: list[dict] = []  # for approach seeding

            for cam in cams:
                cam_id = cam.get("id")
                if not cam_id:
                    continue
                lat = float(cam.get("lat", 0) or 0)
                lng = float(cam.get("lng", 0) or 0)

                junction_id = cam.get("junction", "")
                if junction_id:
                    if junction_id not in junction_set:
                        junction_set[junction_id] = {
                            "lat": cam.get("lat"),
                            "lng": cam.get("lng"),
                            "name": cam.get("name", junction_id),
                        }
                    cam_junction_map.append({
                        "camera_id": cam_id,
                        "junction_id": junction_id,
                        "road_id": cam.get("road", ""),
                    })

                existing = session.query(CameraModel).filter(CameraModel.camera_id == cam_id).first()
                if existing:
                    existing.name = cam.get("name", existing.name)
                    existing.road = cam.get("road", existing.road)
                    existing.junction = cam.get("junction", existing.junction)
                    existing.status = cam.get("status", existing.status or "active")
                    if existing.lat is None or existing.lng is None:
                        existing.lat = cam.get("lat", existing.lat)
                        existing.lng = cam.get("lng", existing.lng)
                else:
                    is_sim = (Config.SYSTEM_MODE == "sim" or Config.CAMERA_BACKEND == "sumo")
                    session.add(
                        CameraModel(
                            camera_id=cam_id,
                            name=cam.get("name", cam_id),
                            road=cam.get("road", ""),
                            lat=lat,
                            lng=lng,
                            junction=cam.get("junction", ""),
                            status=cam.get("status", "active"),
                            # stream columns (merged from camera_streams)
                            stream_url="",
                            stream_type="sim" if is_sim else "rtsp",
                            stream_enabled=str(cam.get("status", "active")).lower() == "active",
                            stream_status="online" if is_sim else "offline",
                            # calibration (merged from camera_calibrations)
                            calibration_data={
                                "image_width": 1280,
                                "image_height": 720,
                                "homography_matrix": [],
                                "anchor_lat": lat,
                                "anchor_lng": lng,
                                "bearing_deg": 0.0,
                                "pixels_per_meter": 0.0,
                            },
                            zones=[],
                        )
                    )

            # Seed junctions
            for jid, jinfo in junction_set.items():
                existing = session.query(Junction).filter(Junction.junction_id == jid).first()
                if not existing:
                    session.add(Junction(
                        junction_id=jid,
                        junction_name=jinfo.get("name", jid),
                        lat=jinfo.get("lat"),
                        lng=jinfo.get("lng"),
                    ))

                controller = session.query(SignalController).filter(SignalController.junction_id == jid).first()
                if controller is None:
                    session.add(
                        SignalController(
                            junction_id=jid,
                            controller_type="mock",
                            endpoint="",
                            auth_config={},
                            phase_map={},
                            enabled=True,
                        )
                    )

            # Seed approaches (camera → junction → road links)
            for entry in cam_junction_map:
                existing = session.query(Approach).filter(
                    Approach.junction_id == entry["junction_id"],
                    Approach.approach_id == entry["camera_id"],
                ).first()
                if not existing:
                    session.add(Approach(
                        junction_id=entry["junction_id"],
                        approach_id=entry["camera_id"],
                        road_id=entry["road_id"] or None,
                        camera_id=entry["camera_id"],
                        approach_name=f"{entry['camera_id']}→{entry['junction_id']}",
                        enabled=True,
                    ))

            runtime_settings = {
                "system_mode": Config.SYSTEM_MODE,
                "camera_backend": Config.CAMERA_BACKEND,
                "signal_backend": Config.SIGNAL_BACKEND,
                "ai_backend": Config.AI_BACKEND,
            }
            for key, value in runtime_settings.items():
                existing = session.query(RuntimeConfig).filter(RuntimeConfig.config_key == key).first()
                if existing is None:
                    session.add(RuntimeConfig(config_key=key, config_value=str(value)))
                else:
                    setattr(existing, "config_value", str(value))

            session.commit()
        finally:
            session.close()
    except Exception as e:
        print(f"  ⚠ Camera seed skipped: {e}")


def _sync_cameras_from_network() -> None:
    """Best-effort bootstrap sync so DB camera inventory matches all SUMO TLS rows."""
    try:
        from services.camera_sync import sync_cameras_from_network

        result = sync_cameras_from_network()
        print(
            f"  ✓ Camera inventory synced from network: active={result.get('active', 0)} inactive={result.get('inactive', 0)}"
        )
    except Exception as e:
        print(f"  ⚠ Camera network sync skipped: {e}")

# ═══ Flask App ═══
app = Flask(__name__)
app.config["SECRET_KEY"] = Config.JWT_SECRET_KEY
CORS(app, resources={r"/api/*": {"origins": "*"}})
_APP_BOOTSTRAPPED = False
_BACKGROUND_THREADS_STARTED = False
_BACKGROUND_THREADS_LOCK = threading.Lock()


def _frontend_url() -> str:
    # Allows overriding in backend/.env
    return os.getenv("FRONTEND_URL", "http://localhost:3000").rstrip("/")


# ═══ Root Route ═══
@app.route("/")
def index():
    frontend = _frontend_url()
    accept = (request.headers.get("Accept") or "").lower()
    if "text/html" in accept:
        # Friendly landing page when opened in a browser.
        return (
            "<!doctype html>"
            "<html lang='en'>"
            "<head><meta charset='utf-8'><title>TraffixFlow API</title></head>"
            "<body style='font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif; padding: 24px;'>"
            "<h2 style='margin: 0 0 8px 0;'>TraffixFlow API is running</h2>"
            "<p style='margin: 0 0 16px 0;'>API endpoints are under <code>/api/*</code>.</p>"
            f"<p style='margin: 0 0 16px 0;'>Open the web UI at <a href='{frontend}'>{frontend}</a>.</p>"
            "</body></html>",
            200,
            {"Content-Type": "text/html; charset=utf-8"},
        )
    return jsonify({
        "service": "TraffixFlow API",
        "status": "running",
        "docs": "All endpoints are under /api/*",
        "frontend": frontend,
    })


@app.route("/api/health")
def api_health():
    """Health check endpoint (documented in README)."""
    simulation_active = False
    camera_count = 0
    try:
        import simulation
        simulation_active = bool(getattr(simulation, "sim_active", False))
        camera_count = len(getattr(simulation, "camera_points", []) or [])
    except Exception:
        pass

    yolo_info = {"available": False, "reason": "not initialized"}
    try:
        from detection.yolo_detector import YOLO_STATUS
        yolo_info = dict(YOLO_STATUS)
    except Exception:
        pass

    return jsonify({
        "status": "ok",
        "service": "TraffixFlow API",
        "backends": get_runtime_backends(),
        "simulation_active": simulation_active,
        "camera_count": camera_count,
        "yolo": yolo_info,
    })


@app.route("/<path:path>")
def catch_all(path: str):
    """Avoid confusing 404s when users open frontend paths on the backend port."""
    # Keep API paths as real 404s to avoid hiding mistakes.
    if path.startswith("api/"):
        return jsonify({
            "status": "not_found",
            "path": f"/{path}",
            "hint": "API endpoints are under /api/* (e.g. /api/vehicles).",
        }), 404

    # For non-API paths, redirect to the frontend (preserve path).
    frontend = _frontend_url()
    return redirect(f"{frontend}/{path}", code=302)


# ═══ Register Blueprints ═══
def _register_blueprints():
    from routes.auth import auth_bp
    from routes.traffic import traffic_bp, init_traffic_routes
    from routes.cameras import cameras_bp, init_camera_routes
    from routes.admin import admin_bp, init_admin_routes
    from routes.stats import stats_bp

    import simulation

    init_traffic_routes(simulation)
    init_camera_routes(simulation)
    init_admin_routes(simulation)

    app.register_blueprint(auth_bp)
    app.register_blueprint(traffic_bp)
    app.register_blueprint(cameras_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(stats_bp)


def _prewarm_heavy_resources() -> None:
    """Eagerly load sumolib network + YOLO model so the first CCTV stream request
    doesn't pay the one-time model / network parse latency (visible to users as
    a slow first camera open)."""
    # 1) sumolib network — lazy-loaded inside cctv_renderer on first frame.
    try:
        import cctv_renderer
        cctv_renderer._load_network(Config.SUMO_NET_FILE)
        print("  🔥 Pre-warmed sumolib network")
    except Exception as e:
        print(f"  ⚠ sumolib prewarm skipped: {e}")

    # 2) YOLO model — lazy-loaded inside detector_service on first detection tick.
    try:
        from detection.yolo_detector import get_shared_detector
        det = get_shared_detector()
        if det is not None:
            print("  🔥 Pre-warmed YOLO model")
    except Exception as e:
        print(f"  ⚠ YOLO prewarm skipped: {e}")


def bootstrap_app() -> Flask:
    """Initialize DB state and routes for both direct runs and imported app usage."""
    global _APP_BOOTSTRAPPED
    if _APP_BOOTSTRAPPED:
        return app

    init_db()
    _seed_cameras_from_json()
    _sync_cameras_from_network()
    _prewarm_heavy_resources()
    _register_blueprints()
    # Start background threads eagerly so SUMO is already warming while the first
    # browser request is in flight — otherwise the user sees blank streams for
    # ~5-10s while the before_request hook triggers SUMO boot on demand.
    _start_background_threads()
    _APP_BOOTSTRAPPED = True
    return app


def _background_thread_specs() -> list[tuple[str, Callable[[], object]]]:
    threads: list[tuple[str, Callable[[], object]]] = [
        ("Index Calc", _index_calculation_loop),
        ("Aggregation", _start_aggregation),
        ("Daily Stats", _start_daily_stats),
    ]

    if Config.SYSTEM_MODE == "sim" or Config.CAMERA_BACKEND == "sumo":
        threads = [
            ("Simulation", _start_simulation),
            ("Camera Capture", _start_camera_capture),
            ("Detection", _start_detection),
            ("Signal Apply", _start_signal_apply_loop),
        ] + threads

    if Config.SYSTEM_MODE == "real" or Config.CAMERA_BACKEND == "rtsp":
        threads.extend([
            ("RTSP Ingest", _start_rtsp_ingest),
            # Optical Flow must start BEFORE Tracker so the tracker has flow data
            # available on its first tick. Tracker tolerates None gracefully if not yet ready.
            ("Optical Flow", _start_optical_flow),
            ("Tracker", _start_tracker_service),
        ])

    return threads


def _start_background_threads() -> None:
    global _BACKGROUND_THREADS_STARTED
    with _BACKGROUND_THREADS_LOCK:
        if _BACKGROUND_THREADS_STARTED:
            return
        for name, target in _background_thread_specs():
            thread = threading.Thread(target=target, daemon=True, name=name)
            thread.start()
            print(f"  ✓ Started {name} thread")
        _BACKGROUND_THREADS_STARTED = True


@app.before_request
def _ensure_runtime_started() -> None:
    # Defensive: if bootstrap_app() didn't run (unusual entrypoint like
    # `flask run` without module import), kick the threads on the first request.
    if not _BACKGROUND_THREADS_STARTED and (request.path == "/" or request.path.startswith("/api/")):
        _start_background_threads()


# ═══ Background Threads ═══

def _start_simulation():
    """Start SUMO simulation in a background thread."""
    import simulation
    print("🚀 Starting SUMO simulation...")
    simulation.simulation_loop()


def _start_detection():
    """Start YOLO detection loop in a background thread."""
    try:
        from detection.detector_service import start_detection_loop
        start_detection_loop()
    except Exception as e:
        print(f"  ⚠ Detection loop error: {e}")


def _start_rtsp_ingest():
    """Start real camera ingest loop in a background thread."""
    try:
        from services.rtsp_ingest import start_rtsp_ingest_loop

        start_rtsp_ingest_loop()
    except Exception as e:
        print(f"  ⚠ RTSP ingest loop error: {e}")


def _start_optical_flow():
    """Start Sparse Lucas-Kanade optical flow worker in a background thread."""
    if not Config.OPTICAL_FLOW_ENABLED:
        print("  ✓ Optical flow disabled by config")
        return
    try:
        from services.optical_flow import start_optical_flow_loop

        start_optical_flow_loop()
    except Exception as e:
        print(f"  ⚠ Optical flow loop error: {e}")


def _start_tracker_service():
    """Start real camera tracker/detection loop in a background thread."""
    try:
        from detection.tracker_service import start_tracker_service_loop

        start_tracker_service_loop()
    except Exception as e:
        print(f"  ⚠ Tracker loop error: {e}")


def _start_aggregation():
    """Start hourly aggregation loop in a background thread."""
    try:
        from services.aggregation import start_hourly_aggregation_loop
        start_hourly_aggregation_loop()
    except Exception as e:
        print(f"  ⚠ Aggregation loop error: {e}")


def _start_signal_apply_loop():
    """Reassert the most-recent SignalTiming per junction to the SUMO TLS.

    The /api/admin/signal/manual and /signal/phase endpoints already call TraCI
    at the moment of the request, but manual color overrides only hold for one
    SUMO step before the normal program overwrites them. This loop re-applies
    recent manual timings every few seconds so the user-visible state sticks
    until mode is switched or a newer timing row is written.
    """
    import time
    from sqlalchemy import func

    from database.connection import get_session
    from database.models import SignalTiming
    from services.signal_controller import (
        SimSignalController,
        decide_ai_actions,
        get_signal_controller,
        get_signal_mode,
    )

    # Re-apply window: only respect timings written within the last 10 min so
    # overrides from a previous session don't override a fresh program.
    TTL_SECONDS = 600
    POLL_INTERVAL = 2.0
    AI_DECISION_INTERVAL = 5.0  # seconds — recompute AI phase choice no faster than this

    applied_signature: dict[str, str] = {}  # junction_id -> hash of last applied payload
    manual_colors: dict[str, str] = {}  # junction_id -> color string to reassert
    last_ai_tick: float = 0.0
    print("✓ Signal Apply loop started (2s cadence)")

    while True:
        try:
            import simulation as _sim
            if not getattr(_sim, "sim_active", False):
                time.sleep(POLL_INTERVAL)
                continue

            controller = get_signal_controller(_sim)
            if not isinstance(controller, SimSignalController) or not controller.is_available():
                time.sleep(POLL_INTERVAL)
                continue

            mode = get_signal_mode()

            session = get_session()
            try:
                # One query: for each junction, pick the newest timing row.
                subq = (
                    session.query(
                        SignalTiming.junction_id.label("jid"),
                        func.max(SignalTiming.timestamp).label("ts"),
                    )
                    .group_by(SignalTiming.junction_id)
                    .subquery()
                )
                rows = (
                    session.query(SignalTiming)
                    .join(subq, (SignalTiming.junction_id == subq.c.jid)
                               & (SignalTiming.timestamp == subq.c.ts))
                    .all()
                )
            finally:
                session.close()

            now_ts = time.time()
            latest_junctions: set[str] = set()
            for row in rows:
                jid = str(row.junction_id or "")
                if not jid:
                    continue
                ts = row.timestamp
                if ts is None:
                    continue
                try:
                    age = now_ts - ts.timestamp()
                except Exception:
                    age = 0.0
                if age > TTL_SECONDS:
                    continue

                latest_junctions.add(jid)
                payload = row.phase_durations or []
                signature = f"{row.id}:{row.mode}:{payload}"

                if row.mode == "manual" and isinstance(payload, list) and payload:
                    head = payload[0] if isinstance(payload[0], dict) else {}
                    color = str(head.get("state") or "").strip().lower()
                    if color in {"red", "yellow", "green"}:
                        # Manual color override — reassert every tick because the
                        # normal TL program would otherwise reclaim the lights.
                        manual_colors[jid] = color
                        try:
                            controller.set_manual_color(jid, color)
                        except Exception:
                            pass
                        continue
                    # Phase duration edit — apply once; TraCI program is persistent.
                    if applied_signature.get(jid) != signature:
                        try:
                            controller.set_phase_plan(jid, [
                                p for p in payload if isinstance(p, dict) and "index" in p
                            ])
                            applied_signature[jid] = signature
                        except Exception:
                            pass
                        manual_colors.pop(jid, None)
                elif row.mode == "ai":
                    # AI decisions are applied by apply_ai_actions; nothing to do here.
                    manual_colors.pop(jid, None)
                    applied_signature.pop(jid, None)

            # For any junction with a stale manual color in memory but not in
            # latest_junctions (row aged out), stop reasserting.
            for jid in list(manual_colors.keys()):
                if jid not in latest_junctions:
                    manual_colors.pop(jid, None)
                    applied_signature.pop(jid, None)

            # If mode flipped to AI, let AI logic take over — clear overrides
            # and pick the best phase per junction every AI_DECISION_INTERVAL.
            if mode == "ai":
                manual_colors.clear()
                if (now_ts - last_ai_tick) >= AI_DECISION_INTERVAL:
                    last_ai_tick = now_ts
                    try:
                        actions, decisions = decide_ai_actions(_sim)
                        if actions:
                            controller.apply_ai_actions(actions)
                            for d in decisions:
                                print(
                                    f"  🤖 AI junction {d['junction_id']} "
                                    f"→ phase {d['phase']} (cars={d['cars']}, cameras={d['cameras']})"
                                )
                    except Exception as ai_exc:
                        print(f"  ⚠ AI decision error: {type(ai_exc).__name__}: {ai_exc}")

        except Exception as e:
            print(f"  ⚠ Signal apply tick error: {type(e).__name__}: {e}")

        time.sleep(POLL_INTERVAL)


def _start_daily_stats():
    """Start daily historical stats aggregation + retention loop."""
    try:
        from services.daily_stats import start_daily_stats_loop
        start_daily_stats_loop()
    except Exception as e:
        print(f"  ⚠ Daily stats loop error: {e}")


def _index_calculation_loop():
    """Periodically calculate traffic index from SUMO + YOLO data."""
    import time
    from services.traffic_index import calculate_area_index, save_traffic_index, save_road_density
    from services.density import compute_density_from_sumo, merge_detection_floor, get_detection_counts_by_road, get_road_name_map
    from services.live_state import get_latest_road_state

    from datetime import datetime
    import os
    import json

    # Load road info for Thai names and free-flow speeds
    roads_path = os.path.join(Config.PROJECT_ROOT, "data", "pathumwan_roads.json")
    name_map = {}
    ffs_map = {}
    road_info = {}
    try:
        with open(roads_path, encoding="utf-8") as f:
            road_info = json.load(f)
        for rd in road_info.get("roads", []):
            name_map[rd["code"]] = rd.get("name_th", rd["code"])
            ffs_map[rd["code"]] = rd.get("free_flow_speed_kmh", 50)
    except Exception:
        road_info = {}

    profile_data = {}
    try:
        profile_path = os.path.join(Config.PROJECT_ROOT, "data", "pathumwan_traffic_profile.json")
        with open(profile_path, encoding="utf-8") as f:
            _p = json.load(f)
            profile_data = _p.get("average_hourly_profile", {})
    except Exception:
        pass

    def _build_road_data_from_detections():
        """Fallback: build road_data from TrafficDetection table (YOLO counts).
        
        Speed estimation: uses a congestion-aware curve instead of linear reduction.
        Key insight: if YOLO detects FEW vehicles, it could mean:
          1. Road is actually empty (free-flow) — OR
          2. Traffic is so congested that vehicles are occluded/overlapping
        We use a conservative approach: don't assume free-flow when count is low.
        """
        det_by_road = get_detection_counts_by_road()
        rn_map = get_road_name_map()
        fallback_data = []
        for road_id, ffs in ffs_map.items():
            counts = det_by_road.get(road_id, {})
            total = int(counts.get("total", 0) or 0)
            road_name = rn_map.get(road_id, name_map.get(road_id, road_id))

            if total <= 0:
                # No YOLO detections — mark as no-data so the UI shows "ไม่มีข้อมูล"
                # instead of a fabricated free-flow reading.
                fallback_data.append({
                    "road_name": road_name,
                    "road_id": road_id,
                    "avg_speed": 0.0,
                    "free_flow_speed": ffs,
                    "vehicle_count": 0,
                    "travel_time": 0.0,
                    "vc_ratio": 0.0,
                    "has_speed_data": False,
                })
                continue

            # Congestion-aware speed estimation:
            # - 1-5 vehicles: moderate (70% of FFS) — could be light OR occluded
            # - 6-15 vehicles: getting busy (50% of FFS)
            # - 16-30 vehicles: congested (25% of FFS)
            # - 30+ vehicles: heavily congested (10% of FFS)
            if total <= 5:
                speed_factor = 0.7
            elif total <= 15:
                speed_factor = 0.5
            elif total <= 30:
                speed_factor = 0.25
            else:
                speed_factor = 0.1

            estimated_speed = max(3, ffs * speed_factor)

            fallback_data.append({
                "road_name": rn_map.get(road_id, name_map.get(road_id, road_id)),
                "road_id": road_id,
                "avg_speed": round(estimated_speed, 1),
                "free_flow_speed": ffs,
                "vehicle_count": total,
                "travel_time": 0.0,
                "vc_ratio": min(total / 40.0, 2.0),
                "has_speed_data": True,
            })
        return fallback_data

    while True:
        should_use_live_state = Config.SYSTEM_MODE == "real" or Config.CAMERA_BACKEND == "rtsp"
        if should_use_live_state:
            try:
                live_rows = get_latest_road_state()
                road_data = []
                for row in live_rows:
                    avg_speed = _as_float(row.get("avg_speed_kmh"))
                    occupancy_ratio = _as_float(row.get("occupancy_ratio"))
                    queue_length = _as_int(row.get("queue_length"))
                    road_id = str(row.get("road_id") or "")
                    if not road_id:
                        continue

                    vehicle_cnt = _as_int(row.get("vehicle_count"))
                    has_live_speed = avg_speed > 0
                    if not has_live_speed:
                        avg_speed = 0.0

                    if vehicle_cnt <= 1:
                        # Historical hour profile fill — counts as observed data because
                        # it reflects measured averages for this hour (not a fake FFS).
                        fallback = profile_data.get(str(datetime.now().hour), {})
                        if fallback:
                            profile_speed = float(fallback.get("speed_avg", 0) or 0)
                            if profile_speed > 0:
                                avg_speed = profile_speed
                                has_live_speed = True
                            occupancy_ratio = float(fallback.get("vc_avg", occupancy_ratio))

                    road_data.append({
                        "road_name": name_map.get(road_id, road_id),
                        "road_id": road_id,
                        "avg_speed": avg_speed,
                        "free_flow_speed": ffs_map.get(road_id, 50),
                        "vehicle_count": vehicle_cnt,
                        "travel_time": 0.0,
                        "vc_ratio": occupancy_ratio,
                        "has_speed_data": has_live_speed,
                    })

                # Fallback: if live state is empty, use YOLO detection counts
                if not road_data:
                    road_data = _build_road_data_from_detections()
                    if road_data:
                        print(f"  📡 Using detection fallback for index ({len(road_data)} roads)")

                if road_data:
                    area_idx, road_results = calculate_area_index(road_data)
                    save_traffic_index(area_idx, road_results)
                    for rr in road_results:
                        save_road_density(rr)
                    print(f"  📊 Live traffic index: {area_idx:.1f} ({len(road_results)} roads)")
                else:
                    # No live data at all. Prefer the hour profile (real historical
                    # average); otherwise flag as "ไม่มีข้อมูล" rather than fake FFS.
                    print(f"  📡 No active live data, generating empty state")
                    empty_data = []
                    hour_str = str(datetime.now().hour)
                    fallback = profile_data.get(hour_str, {})
                    profile_speed = float(fallback.get("speed_avg", 0) or 0) if fallback else 0.0
                    profile_vc = float(fallback.get("vc_avg", 0) or 0) if fallback else 0.0
                    has_profile_speed = profile_speed > 0
                    for rid, ffs in ffs_map.items():
                        empty_data.append({
                            "road_name": name_map.get(rid, rid),
                            "road_id": rid,
                            "avg_speed": profile_speed if has_profile_speed else 0.0,
                            "free_flow_speed": ffs,
                            "vehicle_count": 0,
                            "travel_time": 0.0,
                            "vc_ratio": profile_vc,
                            "has_speed_data": has_profile_speed,
                        })
                    area_idx, road_results = calculate_area_index(empty_data)
                    save_traffic_index(area_idx, road_results)
                    for rr in road_results:
                        save_road_density(rr)

            except Exception as e:
                print(f"  ⚠ Index calc error: {e}")

        else:
            try:
                import simulation

                if simulation.sim_active:
                    traci = simulation.get_traci()
                    with simulation.sim_lock:
                        road_data = compute_density_from_sumo(traci, simulation.road_mapping)

                    # Enrich with Thai road names (free_flow_speed already set by density.py)
                    for rd in road_data:
                        code = rd.get("road_id", "")
                        rd["road_name"] = name_map.get(code, code)

                    road_data = merge_detection_floor(road_data)

                    area_idx, road_results = calculate_area_index(road_data)
                    save_traffic_index(area_idx, road_results)

                    for rr in road_results:
                        save_road_density(rr)

                    print(f"  📊 Traffic index: {area_idx:.1f} ({len(road_results)} roads)")
                else:
                    # SUMO not running — try detection fallback
                    road_data = _build_road_data_from_detections()
                    if road_data:
                        area_idx, road_results = calculate_area_index(road_data)
                        save_traffic_index(area_idx, road_results)
                        for rr in road_results:
                            save_road_density(rr)
                        print(f"  📊 Detection fallback index: {area_idx:.1f} ({len(road_results)} roads)")

            except Exception as e:
                print(f"  ⚠ Index calc error: {e}")

        time.sleep(Config.INDEX_INTERVAL)


def _start_camera_capture():
    """Start CCTV camera capture loop."""
    import simulation
    simulation.camera_capture_loop()


# ═══ Main ═══
def main():
    print("=" * 60)
    print("  TraffixFlow — AI Traffic Management System")
    print(f"  Database: {Config.DATABASE_URI[:60]}...")
    print("=" * 60)

    print("\n📦 Initializing database and routes...")
    bootstrap_app()
    _start_background_threads()

    # Run Flask
    print(f"\n🌐 Starting Flask on {Config.FLASK_HOST}:{Config.FLASK_PORT}")
    app.run(
        host=Config.FLASK_HOST,
        port=Config.FLASK_PORT,
        debug=False,
        threaded=True,
        use_reloader=False,
    )


bootstrap_app()


if __name__ == "__main__":
    main()
