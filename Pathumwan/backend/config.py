"""
TraffixFlow Configuration — loads from .env
"""

import os
try:
    from dotenv import load_dotenv  # type: ignore[import-not-found]
except ImportError:
    def load_dotenv(*_args, **_kwargs):
        env_path = _args[0] if _args else None
        if not env_path or not os.path.exists(env_path):
            return False

        loaded = False
        try:
            with open(env_path, encoding="utf-8") as env_file:
                for raw_line in env_file:
                    line = raw_line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, value = line.split("=", 1)
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    if key and key not in os.environ:
                        os.environ[key] = value
                        loaded = True
        except OSError:
            return False
        return loaded

# Load .env from backend/ directory first, then parent (Pathumwan/) as fallback
_backend_dir = os.path.dirname(os.path.abspath(__file__))
_root_dir = os.path.dirname(_backend_dir)
load_dotenv(os.path.join(_backend_dir, '.env'))   # backend/.env (overrides)
load_dotenv(os.path.join(_root_dir, '.env'))       # Pathumwan/.env (root)


class Config:
    # Database (PostgreSQL or SQLite fallback)
    # Prefer explicit DATABASE_URI, but allow NEON_DATABASE_URI as a convenience
    # for deployments that store the Neon connection string under a separate name.
    _db_uri = os.getenv("DATABASE_URI") or os.getenv("NEON_DATABASE_URI")
    DATABASE_URI = _db_uri or (
        f"sqlite:///{os.path.join(os.path.dirname(os.path.abspath(__file__)), 'database', 'traffixflow.db')}"
    )

    # JWT
    JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "change-me")
    JWT_ACCESS_TOKEN_EXPIRES = int(os.getenv("JWT_ACCESS_TOKEN_EXPIRES", 3600))

    # SUMO
    SUMO_HOME = os.getenv("SUMO_HOME", "")
    # Set SUMO_GUI=1 to launch sumo-gui (visible window) instead of headless sumo.
    # The GUI window makes it easy to see TLS/signal changes while iterating on AI/manual control.
    SUMO_GUI = os.getenv("SUMO_GUI", "0").strip().lower() in ("1", "true", "yes", "on")
    PROJECT_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    _default_sumo_net = os.path.join(PROJECT_ROOT, "osm.net.xml")
    _runtime_sumo_net = os.path.join(PROJECT_ROOT, "osm.runtime.net.xml")
    _default_sumo_cfg = os.path.join(PROJECT_ROOT, "osm.sumocfg")
    _runtime_sumo_cfg = os.path.join(PROJECT_ROOT, "osm.runtime.sumocfg")
    SUMO_NET_FILE = os.getenv(
        "SUMO_NET_FILE",
        _runtime_sumo_net if os.path.exists(_runtime_sumo_net) else _default_sumo_net,
    )
    SUMO_CFG_FILE = os.getenv(
        "SUMO_CFG_FILE",
        _runtime_sumo_cfg if os.path.exists(_runtime_sumo_cfg) else _default_sumo_cfg,
    )

    # YOLO
    YOLO_MODEL_PATH = os.getenv("YOLO_MODEL_PATH", "yolo12n.pt")
    YOLO_CONFIDENCE = float(os.getenv("YOLO_CONFIDENCE", 0.25))
    # YOLO inference image size. 480 keeps quality high while running fast on
    # both CPU (Intel Iris Xe) and GPU (T4 / 30-series). Set higher (e.g. 640)
    # for tiny vehicles, lower (e.g. 320) for very weak CPUs.
    YOLO_IMGSZ = int(os.getenv("YOLO_IMGSZ", 480))

    # Detection
    DETECTION_INTERVAL = int(os.getenv("DETECTION_INTERVAL", 5))
    INDEX_INTERVAL = int(os.getenv("INDEX_INTERVAL", 30))
    # Hourly vehicle aggregation cadence. 30s keeps daily-count / TOP-10 stats
    # near-realtime; raise on weak hosts where DB writes are expensive.
    AGGREGATION_INTERVAL = int(os.getenv("AGGREGATION_INTERVAL", 30))
    # Capture loop FPS target (sim mode renderer). Lower this if backend
    # struggles on weak hosts; upper bound is naturally limited by render cost.
    CAMERA_RENDER_FPS = int(os.getenv("CAMERA_RENDER_FPS", 4))

    # Signal program mode (Issue 7):
    #   "pair"        — keep SUMO's default NS/EW paired phases (legacy).
    #   "sequential4" — at sim startup, override every 4-arm TLS with an
    #                   8-phase logic so each direction goes green by itself.
    SIGNAL_PROGRAM_MODE = os.getenv("SIGNAL_PROGRAM_MODE", "pair").strip().lower() or "pair"

    # Runtime backends
    SYSTEM_MODE = os.getenv("SYSTEM_MODE", "sim").strip().lower() or "sim"
    SIGNAL_BACKEND = os.getenv("SIGNAL_BACKEND", "sim").strip().lower() or "sim"
    CAMERA_BACKEND = os.getenv("CAMERA_BACKEND", "sumo").strip().lower() or "sumo"
    AI_BACKEND = os.getenv("AI_BACKEND", "disabled").strip().lower() or "disabled"
    AI_ALGORITHM = os.getenv("AI_ALGORITHM", "PPO").strip().upper() or "PPO"

    # Vision/state thresholds - configurable to avoid hidden fixed constants
    STOPPED_SPEED_THRESHOLD_KMH = float(os.getenv("STOPPED_SPEED_THRESHOLD_KMH", 5))
    # Detections older than this are considered stale and dropped from live-state aggregation.
    # Must be > DETECTION_INTERVAL. Detector now emits a heartbeat every ~0.75 * this value
    # so unchanged counts don't look stale.
    STALE_THRESHOLD_SECONDS = float(os.getenv("STALE_THRESHOLD_SECONDS", 60))
    COUNTING_WINDOW_SECONDS = int(os.getenv("COUNTING_WINDOW_SECONDS", 60))
    OCCUPANCY_WINDOW_SECONDS = int(os.getenv("OCCUPANCY_WINDOW_SECONDS", 5))
    MIN_TRACK_CONFIDENCE = float(os.getenv("MIN_TRACK_CONFIDENCE", 0.25))
    LIVE_STATE_MAX_TRACK_AGE_SECONDS = float(os.getenv("LIVE_STATE_MAX_TRACK_AGE_SECONDS", 10))
    CAMERA_STREAM_FPS_TARGET = int(os.getenv("CAMERA_STREAM_FPS_TARGET", 8))
    CAMERA_STREAM_MAX_WIDTH = int(os.getenv("CAMERA_STREAM_MAX_WIDTH", 960))
    CAMERA_STREAM_JPEG_QUALITY = int(os.getenv("CAMERA_STREAM_JPEG_QUALITY", 72))
    CAMERA_STREAM_DB_HEARTBEAT_SECONDS = float(os.getenv("CAMERA_STREAM_DB_HEARTBEAT_SECONDS", 5.0))
    CAMERA_STREAM_RECONNECT_SECONDS = float(os.getenv("CAMERA_STREAM_RECONNECT_SECONDS", 1.5))

    # YOLO Motion Gate
    # Uses cheap motion signals to skip expensive YOLO inference on quiet cameras,
    # while still forcing periodic checks so stopped traffic is not ignored forever.
    YOLO_MOTION_GATE_ENABLED = os.getenv("YOLO_MOTION_GATE_ENABLED", "1").strip().lower() in ("1", "true", "yes", "on")
    YOLO_MOTION_GATE_HEARTBEAT_SECONDS = float(os.getenv("YOLO_MOTION_GATE_HEARTBEAT_SECONDS", 20.0))
    YOLO_MOTION_GATE_ACTIVE_HOLD_SECONDS = float(os.getenv("YOLO_MOTION_GATE_ACTIVE_HOLD_SECONDS", 12.0))
    YOLO_MOTION_GATE_MIN_ACTIVE_RATIO = float(os.getenv("YOLO_MOTION_GATE_MIN_ACTIVE_RATIO", 0.01))
    YOLO_MOTION_GATE_MIN_MAGNITUDE_PX = float(os.getenv("YOLO_MOTION_GATE_MIN_MAGNITUDE_PX", 1.0))

    # Optical Flow (Sparse Lucas-Kanade) — augments YOLO+tracker in real mode
    OPTICAL_FLOW_ENABLED = os.getenv("OPTICAL_FLOW_ENABLED", "1").strip().lower() in ("1", "true", "yes", "on")
    OPTICAL_FLOW_FPS_TARGET = float(os.getenv("OPTICAL_FLOW_FPS_TARGET", 2.0))
    OPTICAL_FLOW_DOWNSAMPLE_WIDTH = int(os.getenv("OPTICAL_FLOW_DOWNSAMPLE_WIDTH", 320))
    OPTICAL_FLOW_MAX_FEATURES = int(os.getenv("OPTICAL_FLOW_MAX_FEATURES", 200))
    OPTICAL_FLOW_FEATURE_REFRESH_INTERVAL_SECONDS = float(os.getenv("OPTICAL_FLOW_FEATURE_REFRESH_INTERVAL_SECONDS", 5.0))
    OPTICAL_FLOW_BLINDNESS_FALLBACK_ENABLED = os.getenv("OPTICAL_FLOW_BLINDNESS_FALLBACK_ENABLED", "1").strip().lower() in ("1", "true", "yes", "on")
    OPTICAL_FLOW_QUEUE_MAGNITUDE_THRESHOLD_PX = float(os.getenv("OPTICAL_FLOW_QUEUE_MAGNITUDE_THRESHOLD_PX", 1.5))
    OPTICAL_FLOW_NOISE_FLOOR_PX = float(os.getenv("OPTICAL_FLOW_NOISE_FLOOR_PX", 0.5))
    OPTICAL_FLOW_SCENE_ACTIVE_THRESHOLD_PX = float(os.getenv("OPTICAL_FLOW_SCENE_ACTIVE_THRESHOLD_PX", 1.0))
    OPTICAL_FLOW_CAMERA_ALLOWLIST = os.getenv("OPTICAL_FLOW_CAMERA_ALLOWLIST", "")

    # Flask
    FLASK_HOST = os.getenv("FLASK_HOST", "0.0.0.0")
    FLASK_PORT = int(os.getenv("FLASK_PORT", 5000))

    # CCTV
    CCTV_CAPTURE_SIZE = (1920, 1080)
    CCTV_ZOOM_PRESETS = {
        "near": 42000.0,
        "medium": 28000.0,
        "wide": 18000.0,
    }

    # Pathumwan roads for traffic index
    PATHUMWAN_ROADS = [
        "ถนนพระราม 1",
        "ถนนพระราม 4",
        "ถนนพญาไท",
        "ถนนราชดำริ",
        "ถนนเพลินจิต",
        "ถนนบรรทัดทอง",
        "ถนนเพชรบุรี",
        "ถนนจารุเมือง",
        "ถนนวิทยุ",
        "ถนนอังรีดูนังต์",
        "ถนนสารสิน",
    ]
