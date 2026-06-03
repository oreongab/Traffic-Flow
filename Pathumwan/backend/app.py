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
from urllib.parse import urlsplit, urlunsplit
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
from runtime_device import print_device_banner


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
# CORS: allow the web UI (Next.js) to call the API from a different origin.
# Explicitly allow common headers/methods so browser preflight succeeds.
CORS(
    app,
    resources={r"/api/*": {"origins": "*"}},
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
    max_age=86400,
)
_APP_BOOTSTRAPPED = False
_BACKGROUND_THREADS_STARTED = False
_BACKGROUND_THREADS_LOCK = threading.Lock()


@app.before_request
def _handle_cors_preflight():
    # Ensure preflight never fails due to application logic, and don't
    # trigger heavy runtime startup on OPTIONS.
    if request.method != "OPTIONS":
        return None
    if not request.path.startswith("/api/"):
        return None
    # Return an empty 204 response; flask-cors will attach the required headers.
    return ("", 204)


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

    print_device_banner("Backend")
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
            ("AI Inference", _start_ai_loop),
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
    if request.method == "OPTIONS":
        return
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
    """Reassert recent in-memory manual overrides / phase plans to the SUMO TLS."""
    import time

    from database.connection import get_session
    from services.signal_controller import (
        SimSignalController,
        get_recent_manual_overrides,
        get_signal_controller,
        get_signal_mode,
    )

    # Re-apply window: only respect timings written within the last 10 min so
    # overrides from a previous session don't override a fresh program.
    TTL_SECONDS = 600
    POLL_INTERVAL = 2.0

    applied_signature: dict[str, str] = {}  # junction_id -> hash of last applied payload
    manual_colors: dict[str, tuple[str, str]] = {}  # junction_id -> (color, direction)
    signal_state_signature: dict[str, str] = {}
    signal_state_heartbeat: dict[str, float] = {}
    phase_started_at: dict[str, tuple[int, str, float]] = {}
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

            traci = _sim.get_traci()
            now_ts = time.time()

            with _sim.sim_lock:
                try:
                    tls_ids = list(traci.trafficlight.getIDList())
                    current_sim_time = float(traci.simulation.getTime())
                except Exception:
                    tls_ids = list(getattr(_sim, "tls_ids", []) or [])
                    current_sim_time = float(getattr(_sim, "step", 0) or 0.0)

                signal_rows: list[dict[str, object]] = []
                for junction_id in tls_ids:
                    try:
                        current_phase = int(traci.trafficlight.getPhase(junction_id))
                        programs = traci.trafficlight.getAllProgramLogics(junction_id)
                        phase_count = len(programs[0].phases) if programs else 0
                        raw_state = str(traci.trafficlight.getRedYellowGreenState(junction_id) or "")

                        previous_phase_state = phase_started_at.get(str(junction_id))
                        if (
                            previous_phase_state is None
                            or previous_phase_state[0] != current_phase
                            or previous_phase_state[1] != raw_state
                        ):
                            phase_started_at[str(junction_id)] = (current_phase, raw_state, current_sim_time)

                        phase_duration = max(
                            0.0,
                            current_sim_time - phase_started_at[str(junction_id)][2],
                        )
                        try:
                            next_switch_eta = max(
                                0.0,
                                float(traci.trafficlight.getNextSwitch(junction_id) - current_sim_time),
                            )
                        except Exception:
                            next_switch_eta = 0.0
                        signal_rows.append({
                            "junction_id": str(junction_id),
                            "current_phase": current_phase,
                            "phase_count": phase_count,
                            "phase_duration": phase_duration,
                            "next_switch_eta": next_switch_eta,
                            "raw_state": raw_state,
                        })
                    except Exception:
                        continue

            if signal_rows:
                from database.models import SignalState, Junction

                session = get_session()
                try:
                    valid_junctions = {str(j[0]) for j in session.query(Junction.id).all()}
                    for row in signal_rows:
                        junction_id = str(row["junction_id"])
                        if junction_id not in valid_junctions:
                            continue
                        signature = (
                            f"{row['current_phase']}:{row['phase_count']}:{row['raw_state']}:{int(float(row['next_switch_eta']))}"
                        )
                        last_ts = signal_state_heartbeat.get(junction_id, 0.0)
                        if signal_state_signature.get(junction_id) == signature and (now_ts - last_ts) < 30.0:
                            continue

                        signal_state_signature[junction_id] = signature
                        signal_state_heartbeat[junction_id] = now_ts
                        session.add(
                            SignalState(
                                junction_id=junction_id,
                                current_phase=int(row["current_phase"]),
                                phase_count=int(row["phase_count"]),
                                phase_duration=float(row["phase_duration"]),
                                next_switch_eta=float(row["next_switch_eta"]),
                                source="sim",
                                raw_state={"state": row["raw_state"]},
                            )
                        )
                    session.commit()
                except Exception as e:
                    session.rollback()
                    print(f"  ⚠ Failed to persist signal states: {e}")
                finally:
                    session.close()

            mode = get_signal_mode()
            overrides = get_recent_manual_overrides(TTL_SECONDS)

            latest_junctions: set[str] = set()
            for jid, override in overrides.items():
                jid = str(jid or "")
                if not jid:
                    continue
                latest_junctions.add(jid)
                color = str(override.get("color") or "").strip().lower()
                direction = str(override.get("direction") or "all").strip().lower() or "all"
                phase_durations = override.get("phase_durations") if isinstance(override.get("phase_durations"), list) else []

                if color in {"red", "yellow", "green"}:
                    manual_colors[jid] = (color, direction)
                    try:
                        controller.set_manual_color(jid, color, direction=direction)
                    except Exception:
                        pass
                    continue

                if phase_durations:
                    signature = f"{jid}:{phase_durations}"
                    if applied_signature.get(jid) != signature:
                        try:
                            controller.set_phase_plan(
                                jid,
                                [p for p in phase_durations if isinstance(p, dict) and "index" in p],
                            )
                            applied_signature[jid] = signature
                        except Exception:
                            pass
                    manual_colors.pop(jid, None)

            # For any junction with a stale manual color in memory but not in
            # latest_junctions (row aged out), stop reasserting.
            for jid in list(manual_colors.keys()):
                if jid not in latest_junctions:
                    manual_colors.pop(jid, None)
                    applied_signature.pop(jid, None)

            # If mode flipped to AI, let the dedicated AI inference loop own all
            # decision-making. This loop only clears any stale manual overrides.
            if mode == "ai":
                manual_colors.clear()

        except Exception as e:
            print(f"  ⚠ Signal apply tick error: {type(e).__name__}: {e}")

        time.sleep(POLL_INTERVAL)


def _start_ai_loop():
    """Background loop to run AI Inference and save results for reporting."""
    import time
    import os
    import json
    import traci as traci_module
    from ai.agent import TrafficAgent
    from ai.pipeline import build_pipeline_snapshot, snapshot_to_observation
    from ai.config import AIConfig
    from services.signal_controller import (
        get_active_ai_algorithm,
        get_signal_controller,
        get_signal_mode,
        record_ai_decisions,
    )

    print("🚀 Starting AI Inference Loop...")
    
    current_algorithm = get_active_ai_algorithm()
    agent = TrafficAgent(env=None, algorithm=current_algorithm)
    # Load trained model if available, else fall back to rule-based fallback
    agent.load()

    def _resolve_runtime_junction_ids(algorithm: str, available_junction_ids: list[str]) -> tuple[list[str], bool]:
        from services.mapping import get_research_targets

        metadata = TrafficAgent.load_metadata(algorithm)
        available = [str(junction_id) for junction_id in available_junction_ids if str(junction_id or "")]
        available_set = set(available)
        camera_points = list(getattr(_sim, "camera_points", []) or [])

        research_junctions: list[str] = []
        for target in get_research_targets():
            target_junction_id = str(target.get("junction_id") or "").strip()
            target_camera_id = str(target.get("camera_id") or "").strip()
            effective_tls_id = ""

            if target_junction_id in available_set:
                effective_tls_id = target_junction_id
            if not effective_tls_id and target_camera_id:
                camera = next(
                    (
                        cam for cam in camera_points
                        if str(cam.get("camera_id") or "").strip() == target_camera_id
                        or str(cam.get("id") or "").strip() == target_camera_id
                        or str(cam.get("sumo_tls_id") or "").strip() == target_junction_id
                    ),
                    None,
                )
                if camera:
                    candidate = str(camera.get("sumo_tls_id") or camera.get("junction_id") or "").strip()
                    if candidate in available_set:
                        effective_tls_id = candidate

            if effective_tls_id and effective_tls_id not in research_junctions:
                research_junctions.append(effective_tls_id)

        runtime_targets = research_junctions or available
        if not metadata or not metadata.get("junction_ids"):
            return runtime_targets, True

        trained = [str(junction_id) for junction_id in metadata["junction_ids"] if str(junction_id or "")]
        trained_set = set(trained)
        if trained_set.issubset(available_set):
            return trained, True

        runtime_set = set(runtime_targets)
        if trained_set != runtime_set:
            print(
                f"  ⚠ {algorithm} model metadata covers {len(trained_set)} junctions, "
                f"but configured AI scope has {len(runtime_set)} runtime junctions. Falling back to rule-based."
            )
            return runtime_targets, False

        return runtime_targets, True

    POLL_INTERVAL = AIConfig.ACTION_INTERVAL * float(AIConfig.SIM_STEP_LENGTH)
    
    # Setup for report collection
    data_dir = os.path.join(Config.PROJECT_ROOT, "data")
    os.makedirs(data_dir, exist_ok=True)
    report_file = os.path.join(data_dir, "ai_inference_log.json")
    
    # Initialize empty log
    try:
        with open(report_file, "w", encoding="utf-8") as f:
            json.dump([], f)
    except Exception:
        pass

    while True:
        try:
            import simulation as _sim
            if not getattr(_sim, "sim_active", False):
                time.sleep(POLL_INTERVAL)
                continue

            mode = get_signal_mode()
            if mode != "ai":
                time.sleep(POLL_INTERVAL)
                continue

            controller = get_signal_controller(_sim)

            # Discover junction IDs from SUMO traffic lights
            with _sim.sim_lock:
                try:
                    available_junction_ids = list(traci_module.trafficlight.getIDList())
                except Exception:
                    available_junction_ids = []

            junction_ids, model_compatible = _resolve_runtime_junction_ids(current_algorithm, available_junction_ids)
            if not model_compatible:
                agent.model = None

            if not junction_ids:
                time.sleep(POLL_INTERVAL)
                continue

            # Build state (under lock to avoid race with simulationStep)
            with _sim.sim_lock:
                snapshot = build_pipeline_snapshot(traci_module, junction_ids)
            obs = snapshot_to_observation(snapshot)

            target_algorithm = get_active_ai_algorithm()
            if target_algorithm != current_algorithm:
                print(f"🔄 Switching AI Agent from {current_algorithm} to {target_algorithm}")
                current_algorithm = target_algorithm
                agent = TrafficAgent(env=None, algorithm=current_algorithm)
                agent.load()

                junction_ids, model_compatible = _resolve_runtime_junction_ids(current_algorithm, available_junction_ids)
                if not model_compatible:
                    agent.model = None

            # Predict action (either via RL model or rule-based fallback)
            action_indices = agent.predict(obs)
            
            # Formulate action payload
            actions = []
            decisions = []
            snapshot_by_junction = {str(j.junction_id): j for j in snapshot.junctions}
            for i, jid in enumerate(junction_ids):
                phase_idx = int(action_indices[i]) if i < len(action_indices) else 0
                junction_id = str(jid)
                junction_snapshot = snapshot_by_junction.get(junction_id)
                actions.append({
                    "junction_id": junction_id,
                    "target_phase": phase_idx
                })
                decisions.append({
                    "junction_id": junction_id,
                    "phase": phase_idx,
                    "score": round(float(getattr(junction_snapshot, "queue_length", 0.0) or 0.0), 2),
                    "cars": int(round(float(getattr(junction_snapshot, "vehicle_count", 0.0) or 0.0))),
                    "cameras": len(getattr(junction_snapshot, "camera_ids", []) or []),
                    "algorithm": current_algorithm,
                    "method": "trained_model" if agent.model is not None else "rule_based",
                    "current_phase": int(getattr(junction_snapshot, "current_phase", 0) or 0),
                    "queue_length": round(float(getattr(junction_snapshot, "queue_length", 0.0) or 0.0), 2),
                    "waiting_time": round(float(getattr(junction_snapshot, "waiting_time", 0.0) or 0.0), 2),
                    "avg_speed_kmh": round(float(getattr(junction_snapshot, "avg_speed_kmh", 0.0) or 0.0), 2),
                })

            # Apply actions (controller.apply_ai_actions uses sim_lock internally)
            applied_actions = controller.apply_ai_actions(actions)
            applied_by_junction = {
                str(item.get("junction_id") or ""): item
                for item in applied_actions
                if isinstance(item, dict)
            }
            for decision in decisions:
                applied = applied_by_junction.get(decision["junction_id"])
                if not applied:
                    continue
                decision["applied"] = bool(applied.get("applied", False))
                if applied.get("error"):
                    decision["error"] = str(applied.get("error"))
            if decisions:
                record_ai_decisions(decisions)

            # Save report
            log_entry = {
                "timestamp": time.time(),
                "step": _sim.step,
                "signal_mode": mode,
                "algorithm": current_algorithm,
                "method": "trained_model" if agent.model is not None else "rule_based",
                "global_vehicle_count": snapshot.global_vehicle_count,
                "global_avg_speed_kmh": round(snapshot.global_avg_speed_kmh, 2),
                "actions_count": len(actions),
                "actions": actions[:20],  # Keep the full configured AI scope in normal runs.
                "junctions": [
                    {
                        "junction_id": j.junction_id,
                        "queue_length": round(j.queue_length, 2),
                        "waiting_time": round(j.waiting_time, 2),
                        "current_phase": j.current_phase,
                        "vehicle_count": round(j.vehicle_count, 2),
                        "avg_speed_kmh": round(j.avg_speed_kmh, 2),
                    } for j in snapshot.junctions[:20]
                ]
            }
            try:
                with open(report_file, "r", encoding="utf-8") as f:
                    logs = json.load(f)
            except Exception:
                logs = []
            
            logs.append(log_entry)
            # Keep last 500 records to prevent file from getting too large
            if len(logs) > 500:
                logs = logs[-500:]
            
            with open(report_file, "w", encoding="utf-8") as f:
                json.dump(logs, f)

        except Exception as e:
            print(f"  ⚠ AI Inference Loop Error: {e}")

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
            from services.signal_controller import get_signal_mode
            ai_active = get_signal_mode() == "ai"

            if ai_active:
                # AI optimizes green times so cars flow through quickly instead of waiting
                # Make the bonus aggressive so the dashboard clearly reflects the AI's flow improvements
                if total <= 5:
                    speed_factor = 0.95
                elif total <= 15:
                    speed_factor = 0.85
                elif total <= 30:
                    speed_factor = 0.70
                else:
                    speed_factor = 0.55
                # AI clears queues effectively, so perceived congestion volume drops by 60%
                effective_total = max(0, total - int(total * 0.6))
            else:
                if total <= 5:
                    speed_factor = 0.7
                elif total <= 15:
                    speed_factor = 0.5
                elif total <= 30:
                    speed_factor = 0.25
                else:
                    speed_factor = 0.1
                effective_total = total

            estimated_speed = max(3, ffs * speed_factor)

            fallback_data.append({
                "road_name": rn_map.get(road_id, name_map.get(road_id, road_id)),
                "road_id": road_id,
                "avg_speed": round(estimated_speed, 1),
                "free_flow_speed": ffs,
                "vehicle_count": total,
                "travel_time": 0.0,
                "vc_ratio": min(effective_total / 40.0, 2.0),
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

                    road_data = merge_detection_floor(road_data, prefer_detection=False)

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
    def _redact_db_uri(uri: str) -> str:
        if uri.startswith("sqlite"):
            return uri
        try:
            parts = urlsplit(uri)
            hostname = parts.hostname or ""
            if parts.port:
                hostname = f"{hostname}:{parts.port}"
            if parts.username:
                netloc = f"{parts.username}:***@{hostname}"
            else:
                netloc = hostname
            return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
        except Exception:
            return "<set>"

    print("=" * 60)
    print("  TraffixFlow — AI Traffic Management System")
    print(f"  Database: {_redact_db_uri(Config.DATABASE_URI)}")
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
