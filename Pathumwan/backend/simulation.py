"""
SUMO Traffic Simulation Engine — Pathumwan District
====================================================
Loads calibrated route files (from data_cleaning.py) and manages TraCI connection.
Vehicles come from pre-generated .rou.xml files based on real BMA traffic data.
Traffic lights use realistic Pathumwan signal programs.

This version runs SUMO **headless** (no GUI window) and generates CCTV frames
via the cctv_renderer module instead of fragile SUMO-GUI screenshots.
"""

import os
import sys
import json
import time
import random
import threading
import math
import filecmp
import glob
import gzip
import shutil
import xml.etree.ElementTree as ET

# --- SUMO ---
if "SUMO_HOME" in os.environ:
    sys.path.append(os.path.join(os.environ["SUMO_HOME"], "tools"))
import traci

from config import Config
from utils import find_sumo_binary
from cctv import collect_cameras
from cctv_renderer import (
    render_cctv_frame,
    render_placeholder,
    get_vehicles_near_camera,
    count_vehicles_near_camera,
    DEFAULT_RADIUS,
)

# ═══ Paths ═══
PROJECT_ROOT = Config.PROJECT_ROOT
NET_FILE = Config.SUMO_NET_FILE
SUMO_CFG = Config.SUMO_CFG_FILE
PROFILE_FILE = os.path.join(PROJECT_ROOT, "data", "pathumwan_traffic_profile.json")

# ═══ Vehicle colors for visualization (used by SUMO-GUI if ever opened) ═══
VEH_COLORS = {
    "veh_passenger": [
        (255, 80, 80, 255), (80, 200, 255, 255), (255, 220, 50, 255),
        (150, 255, 100, 255), (255, 150, 50, 255), (255, 255, 255, 255),
        (180, 130, 255, 255), (220, 220, 220, 255),
    ],
    "moto_motorcycle": [
        (255, 50, 50, 255), (50, 255, 50, 255), (255, 200, 0, 255),
    ],
    "truck_truck": [
        (80, 80, 80, 255), (255, 180, 0, 255), (0, 100, 200, 255),
    ],
    "bus_bus": [
        (255, 100, 0, 255), (0, 150, 255, 255), (255, 50, 50, 255),
    ],
}

# ═══ Global State ═══
sim_lock = threading.Lock()
sim_active = False
sim_error = ""
step = 0
camera_points = []
tls_ids = []
traffic_profile = None
road_mapping = {}  # {"RAMA1": ["edge1", "edge2", ...], ...}

# ═══ Frame Cache — pre-rendered CCTV frames ═══
_frame_cache = {}            # camera_id -> JPEG bytes (raw)
_frame_cache_lock = threading.Lock()

# Cached frames with detection overlays — generated only for cameras currently
# being viewed in detect mode to keep multi-camera grids responsive.
_detect_frame_cache = {}     # camera_id -> JPEG bytes (detect)
_detect_frame_cache_lock = threading.Lock()
# camera_id -> last requested timestamp (seconds)
_detect_stream_requested: dict[str, float] = {}
_detect_stream_requested_lock = threading.Lock()
_DETECT_REQUEST_TTL_SECONDS = 20.0

# Speed reference from real data (km/h avg per hour — fallback defaults)
DEFAULT_SPEED_PROFILE = {
    5: 42, 6: 30, 7: 28, 8: 27, 9: 24, 10: 24, 11: 25,
    12: 24, 13: 24, 14: 23, 15: 24, 16: 26, 17: 24, 18: 20,
}


# ─── Helpers ───

def _find_camera(camera_id):
    requested = str(camera_id or "").strip()
    if not requested:
        return None
    for camera in camera_points:
        aliases = {
            str(camera.get("camera_id") or "").strip(),
            str(camera.get("id") or "").strip(),
            str(camera.get("sumo_tls_id") or "").strip(),
        }
        if requested in aliases:
            return camera
    return None


def capture_cctv_frame(
    camera_id,
    zoom_level="near",
    show_detection=False,
    show_hud=True,
    show_camera_marker=True,
    show_detection_labels=True,
    show_vehicle_labels=True,
):
    """Capture a virtual CCTV frame by rendering from SUMO data (no GUI needed)."""
    if not sim_active:
        return render_placeholder("SUMO is still starting")

    camera = _find_camera(camera_id)
    if camera is None:
        return render_placeholder("Camera not found")

    try:
        radius_map = {
            "near": DEFAULT_RADIUS,
            "medium": int(DEFAULT_RADIUS * 1.35),
            "wide": int(DEFAULT_RADIUS * 1.75),
        }
        radius = radius_map.get(str(zoom_level or "near").lower(), DEFAULT_RADIUS)

        # Synthetic CCTV already knows vehicle positions from TraCI, so the
        # renderer can draw YOLO-style boxes without running the heavy model on
        # every MJPEG frame. Loading real YOLO here made camera pages stall.
        detector = None

        with sim_lock:
            frame_bytes = render_cctv_frame(
                NET_FILE, traci, camera,
                radius=radius,
                show_detection=show_detection,
                detector=detector,
                show_hud=show_hud,
                show_camera_marker=show_camera_marker,
                show_detection_labels=show_detection_labels,
                show_vehicle_labels=show_vehicle_labels,
            )
        return frame_bytes if frame_bytes else render_placeholder("Render failed")
    except Exception as e:
        return render_placeholder(f"Error: {str(e)[:30]}")


def get_camera_vehicles(camera_id, radius=DEFAULT_RADIUS):
    """Get vehicles near a specific camera. Returns list of vehicle dicts."""
    if not sim_active:
        return []
    camera = _find_camera(camera_id)
    if camera is None:
        return []
    try:
        with sim_lock:
            return get_vehicles_near_camera(traci, camera, radius)
    except Exception:
        return []


def get_camera_counts(camera_id, radius=DEFAULT_RADIUS):
    """Get vehicle counts near a specific camera."""
    if not sim_active:
        return {"car": 0, "motorcycle": 0, "bus": 0, "truck": 0, "total": 0}
    camera = _find_camera(camera_id)
    if camera is None:
        return {"car": 0, "motorcycle": 0, "bus": 0, "truck": 0, "total": 0}
    try:
        with sim_lock:
            return count_vehicles_near_camera(traci, camera, radius)
    except Exception:
        return {"car": 0, "motorcycle": 0, "bus": 0, "truck": 0, "total": 0}


# ─── Frame Cache Loop ───

def camera_capture_loop():
    """Background thread that cycles through all cameras and pre-caches rendered frames.

    Each camera's CCTV view is rendered once per cycle.  MJPEG stream endpoints
    serve the cached frame instead of rendering on-the-fly, which keeps latency
    low for multiple simultaneous viewers.

    Detect (overlay) frames are rendered only for cameras currently being viewed
    in detect mode (see mark_detect_stream_camera) to avoid stalling multi-feed
    CCTV grids.
    """
    while True:
        if not sim_active or not camera_points:
            time.sleep(2)
            continue

        detect_targets = _detect_targets_snapshot()

        for cam in camera_points:
            if not sim_active:
                break
            cam_id = str(cam.get("camera_id", cam.get("id", "")))
            if not cam_id:
                continue
            try:
                frame = capture_cctv_frame(cam_id, show_detection=False)
                if frame:
                    with _frame_cache_lock:
                        _frame_cache[cam_id] = frame

                if cam_id in detect_targets:
                    detect_frame = capture_cctv_frame(cam_id, show_detection=True)
                    if detect_frame:
                        with _detect_frame_cache_lock:
                            _detect_frame_cache[cam_id] = detect_frame
            except Exception:
                pass

            # Tight loop — sim_lock already serializes TraCI reads, so extra
            # sleep here only adds latency to the slowest camera's refresh.
            time.sleep(0.01)

        # Minimal breather between cycles so we don't spin at 100% CPU when all
        # cameras render quickly.
        time.sleep(0.03)


def get_cached_frame(camera_id):
    """Return the latest pre-cached JPEG bytes for *camera_id*, or None."""
    with _frame_cache_lock:
        return _frame_cache.get(str(camera_id))


def mark_detect_stream_camera(camera_id: str) -> None:
    cam_id = str(camera_id or "").strip()
    if not cam_id:
        return
    now = time.time()
    with _detect_stream_requested_lock:
        _detect_stream_requested[cam_id] = now


def _detect_targets_snapshot() -> set[str]:
    now = time.time()
    with _detect_stream_requested_lock:
        expired = [cid for cid, ts in _detect_stream_requested.items() if (now - ts) > _DETECT_REQUEST_TTL_SECONDS]
        for cid in expired:
            _detect_stream_requested.pop(cid, None)
        return set(_detect_stream_requested.keys())


def get_cached_detect_frame(camera_id):
    """Return the latest cached detect (overlay) JPEG bytes for *camera_id*, or None."""
    with _detect_frame_cache_lock:
        return _detect_frame_cache.get(str(camera_id))


# ─── SUMO Setup ───

def get_sumo_command():
    """Build sumo command using the calibrated .sumocfg.
    Defaults to headless sumo. Set env SUMO_GUI=1 to launch sumo-gui instead.
    """
    from config import Config

    if Config.SUMO_GUI:
        from utils import find_sumo_gui
        binary = find_sumo_gui()
    else:
        binary = find_sumo_binary()

    return [
        binary,
        "-c", SUMO_CFG,       # Load the FULL config with route files
        "--start",
        "--no-warnings",
        "--delay", "50",
        "--quit-on-end", "false",
    ]


def _sumo_config_route_files() -> list[str]:
    """Return absolute route files referenced by the active SUMO config."""

    try:
        root = ET.parse(SUMO_CFG).getroot()
    except Exception:
        return []

    cfg_dir = os.path.dirname(os.path.abspath(SUMO_CFG))
    route_files: list[str] = []
    for node in root.findall(".//route-files"):
        for raw in str(node.get("value") or "").split(","):
            value = raw.strip()
            if not value:
                continue
            route_files.append(value if os.path.isabs(value) else os.path.join(cfg_dir, value))
    return route_files


def _validate_route_file(path: str) -> None:
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    if path.endswith(".gz"):
        with gzip.open(path, "rb") as fh:
            while fh.read(1024 * 1024):
                pass


def _matching_dataset_pack() -> str | None:
    current_dataset = os.path.join(PROJECT_ROOT, "data", "Dataset.csv")
    packs_root = os.path.abspath(os.path.join(PROJECT_ROOT, "..", "dataset_packs"))
    if not os.path.exists(current_dataset) or not os.path.isdir(packs_root):
        return None

    for pack_path in sorted(glob.glob(os.path.join(packs_root, "*", "Pathumwan"))):
        candidate_dataset = os.path.join(pack_path, "data", "Dataset.csv")
        if not os.path.exists(candidate_dataset):
            continue
        try:
            if filecmp.cmp(current_dataset, candidate_dataset, shallow=False):
                return pack_path
        except OSError:
            continue
    return None


def _repair_dataset_route_file(route_path: str) -> bool:
    pack_path = _matching_dataset_pack()
    if not pack_path:
        return False

    repaired = False
    for relative_path in (
        "osm.dataset.rou.xml.gz",
        "osm.dataset.trips.xml",
        os.path.join("data", "dataset_route_mapping.generated.json"),
    ):
        src = os.path.join(pack_path, relative_path)
        dst = os.path.join(PROJECT_ROOT, relative_path)
        if os.path.exists(src):
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
            if os.path.abspath(dst) == os.path.abspath(route_path):
                repaired = True

    if repaired:
        _validate_route_file(route_path)
    return repaired


def _ensure_sumo_route_inputs() -> None:
    """Validate SUMO route files before TraCI starts.

    A truncated gzip lets Flask keep serving HTTP while SUMO dies, which makes
    the frontend look mapped but disconnected from runtime data. Repair from
    the matching dataset pack when possible, otherwise fail loudly.
    """

    for route_path in _sumo_config_route_files():
        try:
            _validate_route_file(route_path)
        except Exception as exc:
            print(f"  ⚠ SUMO route invalid: {route_path} ({exc})")
            if _repair_dataset_route_file(route_path):
                print(f"  ✓ Repaired SUMO route from matching dataset pack: {route_path}")
                continue
            raise RuntimeError(
                f"SUMO route file is invalid and could not be repaired: {route_path}"
            ) from exc


def _build_sequential4_phases(tid):
    """Build an 8-phase "เขียวทีละทิศ" program for a 4-arm TLS.

    Returns a list of ``traci.trafficlight.Phase`` (4 green + 4 yellow) or
    ``None`` if we can't infer ≥4 distinct lane directions (e.g. T-junctions
    or sumolib unavailable). Caller falls back to the legacy program in that
    case.

    The order of greens is N → E → S → W. Each green is 25 s, each yellow 3 s
    so the full cycle is 112 s (within Bangkok's 90-120 s norm).
    """
    try:
        import sumolib
        import math as _math
    except Exception:
        return None
    try:
        net = sumolib.net.readNet(NET_FILE, withInternal=False)
    except Exception:
        return None

    try:
        controlled = list(traci.trafficlight.getControlledLanes(tid))
    except Exception:
        return None
    L = len(controlled)
    if L == 0:
        return None

    def _heading_bucket(lane_id):
        try:
            lane = net.getLane(lane_id)
            edge = lane.getEdge()
            fx, fy = edge.getFromNode().getCoord()
            tx, ty = edge.getToNode().getCoord()
            heading = (_math.degrees(_math.atan2(ty - fy, tx - fx)) + 360.0) % 360.0
        except Exception:
            return None
        if 45.0 <= heading < 135.0:
            return "n"
        if 135.0 <= heading < 225.0:
            return "w"
        if 225.0 <= heading < 315.0:
            return "s"
        return "e"

    buckets = [_heading_bucket(lane) for lane in controlled]
    distinct = {b for b in buckets if b is not None}
    if len(distinct) < 4:
        # 3-arm or heading inference incomplete — keep legacy program.
        return None

    def _state_for(direction):
        chars = []
        for b in buckets:
            if b == direction:
                chars.append("G")
            else:
                chars.append("r")
        return "".join(chars)

    def _yellow_for(direction):
        chars = []
        for b in buckets:
            if b == direction:
                chars.append("y")
            else:
                chars.append("r")
        return "".join(chars)

    order = ["n", "e", "s", "w"]
    phases = []
    for direction in order:
        phases.append(traci.trafficlight.Phase(
            duration=25.0, state=_state_for(direction), minDur=15.0, maxDur=35.0,
        ))
        phases.append(traci.trafficlight.Phase(
            duration=3.0, state=_yellow_for(direction), minDur=3.0, maxDur=3.0,
        ))
    return phases


def _configure_traffic_lights():
    """
    Configure traffic lights with realistic Pathumwan signal programs.
    Bangkok inner city typically uses 90-120 second cycles.

    Two modes (selected by ``Config.SIGNAL_PROGRAM_MODE``):
      • "pair" (default)  — keep SUMO's NS/EW paired phases; rebalance to 90 s.
      • "sequential4"     — overwrite each 4-arm TLS with an 8-phase logic
        that turns each direction green by itself (Issue 7). Falls back to
        "pair" per-junction when the heading-inference fails.
    """
    from config import Config as _Cfg
    global tls_ids
    try:
        tls_ids = list(traci.trafficlight.getIDList())
    except Exception:
        tls_ids = []
        return

    use_sequential4 = (_Cfg.SIGNAL_PROGRAM_MODE == "sequential4")
    sequential_count = 0
    pair_count = 0

    for tid in tls_ids:
        try:
            programs = traci.trafficlight.getAllProgramLogics(tid)
            if not programs:
                continue

            logic = programs[0]
            n_phases = len(logic.phases)
            if n_phases == 0:
                continue

            new_phases = None
            program_id = "pathumwan_90s"
            if use_sequential4:
                seq_phases = _build_sequential4_phases(tid)
                if seq_phases is not None:
                    new_phases = seq_phases
                    program_id = "pathumwan_seq4"
                    sequential_count += 1

            if new_phases is None:
                # Legacy paired-phase rebalance (current behaviour).
                green_phases = [i for i, p in enumerate(logic.phases) if "G" in p.state or "g" in p.state]
                yellow_phases = [i for i, p in enumerate(logic.phases) if "y" in p.state]
                red_phases = [i for i, p in enumerate(logic.phases) if all(c in "r" for c in p.state)]

                CYCLE = 90
                new_phases = []
                for i, phase in enumerate(logic.phases):
                    if i in green_phases:
                        green_time = max(15, (CYCLE - len(yellow_phases) * 3 - len(red_phases) * 2) // max(1, len(green_phases)))
                        new_phases.append(traci.trafficlight.Phase(
                            duration=float(green_time),
                            state=phase.state,
                            minDur=float(green_time * 0.6),
                            maxDur=float(green_time * 1.4),
                        ))
                    elif i in yellow_phases:
                        new_phases.append(traci.trafficlight.Phase(
                            duration=3.0, state=phase.state, minDur=3.0, maxDur=3.0,
                        ))
                    else:
                        new_phases.append(traci.trafficlight.Phase(
                            duration=2.0, state=phase.state, minDur=2.0, maxDur=2.0,
                        ))
                pair_count += 1

            new_logic = traci.trafficlight.Logic(
                programID=program_id,
                type=0,
                currentPhaseIndex=0,
                phases=new_phases,
            )
            traci.trafficlight.setProgramLogic(tid, new_logic)
            traci.trafficlight.setProgram(tid, program_id)
        except Exception as exc:
            print(f"  ⚠ TLS configure failed [{tid}]: {exc}")
            continue

    if use_sequential4:
        print(f"  Configured {len(tls_ids)} traffic lights (sequential4: {sequential_count}, pair-fallback: {pair_count})")
    else:
        print(f"  Configured {len(tls_ids)} traffic lights (90s pair cycle)")


def _colorize_vehicles():
    """Assign random colors to vehicles for visualization."""
    try:
        for vid in traci.vehicle.getIDList():
            try:
                vtype = str(traci.vehicle.getTypeID(vid) or "")
                colors = VEH_COLORS.get(vtype, VEH_COLORS.get("veh_passenger"))
                if colors:
                    color = random.choice(colors)
                    traci.vehicle.setColor(vid, color)
            except Exception:
                pass
    except Exception:
        pass


def _load_traffic_profile():
    """Load the calibrated traffic profile from data_cleaning output."""
    global traffic_profile
    if os.path.exists(PROFILE_FILE):
        with open(PROFILE_FILE, "r", encoding="utf-8") as f:
            traffic_profile = json.load(f)
        print(f"  Loaded traffic profile: {len(traffic_profile.get('stations', {}))} stations")
    else:
        print("  WARNING: No traffic profile found. Run data/data_cleaning.py first.")


def _build_road_mapping():
    """
    Build a mapping from road codes (RAMA1, PHAYATHAI, etc.) to SUMO edge IDs.
    Uses both road name keywords and camera proximity so each road maps to
    the relevant portion of the network instead of the full network.
    """
    roads_path = os.path.join(PROJECT_ROOT, "data", "pathumwan_roads.json")
    mapping = {}

    def _point_segment_distance(px, py, ax, ay, bx, by):
        dx = bx - ax
        dy = by - ay
        if dx == 0 and dy == 0:
            return math.hypot(px - ax, py - ay)
        t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
        t = max(0.0, min(1.0, t))
        nx = ax + t * dx
        ny = ay + t * dy
        return math.hypot(px - nx, py - ny)

    def _point_shape_distance(px, py, shape):
        if not shape:
            return float("inf")
        if len(shape) == 1:
            sx, sy = shape[0]
            return math.hypot(px - sx, py - sy)
        return min(
            _point_segment_distance(px, py, ax, ay, bx, by)
            for (ax, ay), (bx, by) in zip(shape[:-1], shape[1:])
        )

    try:
        with open(roads_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = {"roads": [], "cameras": []}

    road_codes = [r["code"] for r in data.get("roads", [])]
    camera_defs = data.get("cameras", [])

    # Keywords for matching SUMO edge street names to road codes
    road_keywords = {
        "RAMA1": ["rama i", "rama1", "พระรามที่ 1", "พระราม 1", "rama_i"],
        "RAMA4": ["rama iv", "rama4", "พระรามที่ 4", "พระราม 4", "rama_iv"],
        "PHAYATHAI": ["phayathai", "phaya thai", "พญาไท"],
        "RATCHADAMRI": ["ratchadamri", "ราชดำริ"],
        "PLOENCHIT": ["ploenchit", "เพลินจิต"],
        "PHETCHABURI": ["phetchaburi", "เพชรบุรี"],
        "BANTHATTHONG": ["banthat thong", "banthatthong", "บรรทัดทอง"],
        "CHARUMUEANG": ["charu mueang", "charumueang", "จารุเมือง"],
        "WITTHAYU": ["witthayu", "wireless", "วิทยุ"],
        "HENRIDUNANT": ["henri dunant", "henridunant", "อังรีดูนังต์"],
        "SARASIN": ["sarasin", "สารสิน"],
    }

    # Initialize empty lists
    for code in road_codes:
        mapping[code] = []

    edge_cache = []
    try:
        import sumolib
        from utils import latlng_to_sumo_xy

        net = sumolib.net.readNet(NET_FILE, withInternal=False)
        for edge in net.getEdges():
            eid = edge.getID()
            if eid.startswith(":"):
                continue
            edge_cache.append({
                "id": eid,
                "street": (edge.getName() or "").lower(),
                "shape": edge.getShape(),
            })

        for code in road_codes:
            matched = set()
            keywords = road_keywords.get(code, [])
            road_cameras = [cam for cam in camera_defs if cam.get("road") == code]

            for edge in edge_cache:
                if edge["street"] and any(kw in edge["street"] for kw in keywords):
                    matched.add(edge["id"])

            for cam in road_cameras:
                cx, cy = latlng_to_sumo_xy(cam["lat"], cam["lng"])
                for edge in edge_cache:
                    if _point_shape_distance(cx, cy, edge["shape"]) <= 140:
                        matched.add(edge["id"])

            mapping[code] = sorted(matched)
    except Exception:
        for eid in [e for e in traci.edge.getIDList() if not e.startswith(":")]:
            try:
                street = str(traci.edge.getStreetName(eid) or "").lower()
            except Exception:
                street = ""
            for code, keywords in road_keywords.items():
                if code in mapping and street and any(kw in street for kw in keywords):
                    mapping[code].append(eid)
                    break

    total_mapped = sum(len(v) for v in mapping.values())
    unmapped = [code for code, edges in mapping.items() if not edges]
    print(f"  Road mapping: {total_mapped} edges mapped to {len(mapping)} roads")
    if unmapped:
        print(f"  Unmapped roads: {', '.join(unmapped)}")
    return mapping


def setup_simulation():
    """Initialize SUMO with calibrated config and collect cameras."""
    global camera_points, road_mapping

    print("Starting SUMO (headless) with calibrated config...")
    _ensure_sumo_route_inputs()
    traci.start(get_sumo_command())
    time.sleep(2)

    # Configure traffic lights
    _configure_traffic_lights()

    # Load traffic profile for reference
    _load_traffic_profile()

    # Build road-to-edge mapping from pathumwan_roads.json
    road_mapping = _build_road_mapping()

    # Collect camera positions
    camera_points = collect_cameras(traci)

    # Sync live traffic-light camera inventory to DB (best-effort)
    try:
        from services.camera_sync import sync_live_camera_inventory
        sync_live_camera_inventory(camera_points)
    except Exception:
        pass

    edge_count = len([e for e in traci.edge.getIDList() if not e.startswith(":")])
    veh_count = traci.vehicle.getIDList()
    print(f"  Network: {edge_count} edges, {len(tls_ids)} TLS, {len(camera_points)} cameras")
    print(f"  Initial vehicles: {len(veh_count)}")


def simulation_loop():
    """Main simulation loop — runs in a background thread."""
    global step, sim_active, sim_error

    try:
        setup_simulation()
        sim_active = True
        sim_error = ""
        print("SUMO simulation started — Pathumwan District (headless)")
    except Exception as e:
        sim_active = False
        sim_error = f"SUMO start failed: {e}"
        print(sim_error)
        return

    colorize_interval = 50   # Colorize new vehicles every 50 steps
    last_colorize = 0

    while True:
        try:
            with sim_lock:
                traci.simulationStep()

                # Colorize new vehicles periodically
                if step - last_colorize >= colorize_interval:
                    _colorize_vehicles()
                    last_colorize = step

                step += 1

            time.sleep(0.05)  # ~20 sim steps/sec
        except Exception as ex:
            # TraCI may raise a FatalTraCIError on disconnect; avoid relying on
            # missing type stubs by checking the exception name.
            if ex.__class__.__name__ == "FatalTraCIError":
                print("SUMO connection lost")
                sim_active = False
                break
            print(f"Sim error at step {step}: {ex}")
            time.sleep(0.1)


# ─── Public API ───

def get_traci():
    """Return the traci module for external use."""
    return traci


def get_vehicle_count():
    """Get current number of vehicles in simulation."""
    if not sim_active:
        return 0
    try:
        with sim_lock:
            return len(traci.vehicle.getIDList())
    except Exception:
        return 0


def get_avg_speed():
    """Get average speed (km/h) of all vehicles."""
    if not sim_active:
        return 0.0
    try:
        with sim_lock:
            vehicles = traci.vehicle.getIDList()
            if not vehicles:
                return 0.0
            speeds = []
            for v in vehicles:
                try:
                    sp = traci.vehicle.getSpeed(v)
                    speeds.append(float(sp) if isinstance(sp, (int, float)) else 0.0)
                except Exception:
                    speeds.append(0.0)
            return round(sum(speeds) / len(speeds) * 3.6, 1)  # m/s → km/h
    except Exception:
        return 0.0


def get_tls_states():
    """Get current traffic light states for all junctions."""
    if not sim_active:
        return {}
    result = {}
    try:
        with sim_lock:
            for tid in tls_ids:
                try:
                    state = traci.trafficlight.getRedYellowGreenState(tid)
                    phase = traci.trafficlight.getPhase(tid)
                    result[tid] = {"state": state, "phase": phase}
                except Exception:
                    pass
    except Exception:
        pass
    return result


def get_simulation_stats():
    """Get current simulation statistics."""
    if not sim_active:
        return {"active": False, "step": 0}
    try:
        with sim_lock:
            vehicles = traci.vehicle.getIDList()
            speeds = []
            if vehicles:
                for v in vehicles:
                    try:
                        sp = traci.vehicle.getSpeed(v)
                        speeds.append(float(sp) if isinstance(sp, (int, float)) else 0.0)
                    except Exception:
                        speeds.append(0.0)
            return {
                "active": True,
                "step": step,
                "time_seconds": step,
                "vehicle_count": len(vehicles),
                "avg_speed_kmh": round(sum(speeds) / max(1, len(speeds)) * 3.6, 1),
                "min_speed_kmh": round(min(speeds) * 3.6, 1) if speeds else 0,
                "max_speed_kmh": round(max(speeds) * 3.6, 1) if speeds else 0,
                "tls_count": len(tls_ids),
                "camera_count": len(camera_points),
            }
    except Exception:
        return {"active": sim_active, "step": step}
