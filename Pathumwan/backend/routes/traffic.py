"""
Traffic Routes — Vehicles, Traffic Index, Road Density, Traffic Lights
"""

import json
import os
import threading
from datetime import datetime, timezone
from flask import Blueprint, jsonify
from utils import sumo_xy_to_latlng
from services.traffic_index import (
    get_current_index, get_index_history, get_road_density_list,
    get_congestion_level, get_congestion_color, calculate_road_index,
    calculate_area_index,
)
from services.density import merge_detection_floor, get_detection_counts_by_road, get_road_name_map
from services.live_state import (
    get_latest_junction_state,
    get_latest_road_state,
    get_live_vehicle_map_data,
)
from services.mapping import get_camera_catalog
from services.signal_controller import get_runtime_backends
from config import Config

traffic_bp = Blueprint("traffic", __name__, url_prefix="/api")

_sim = None

_road_geom_cache = None
_road_geom_lock = threading.Lock()

# Load free-flow speeds from roads config
_ffs_map = {}
try:
    _roads_path = os.path.join(Config.PROJECT_ROOT, "data", "pathumwan_roads.json")
    with open(_roads_path, encoding="utf-8") as f:
        _roads_data = json.load(f)
    for rd in _roads_data.get("roads", []):
        _ffs_map[rd["code"]] = rd.get("free_flow_speed_kmh", 50)
except Exception:
    pass


def init_traffic_routes(simulation_module):
    global _sim
    _sim = simulation_module


def _live_mode_enabled() -> bool:
    return Config.SYSTEM_MODE == "real" or Config.CAMERA_BACKEND == "rtsp"


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


def _as_dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _as_timestamp_string(value) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value or "")


def _freshness_seconds(ts_value) -> float:
    """Seconds elapsed since an ISO/datetime timestamp. Returns 0.0 on parse failure."""
    if ts_value is None:
        return 0.0
    try:
        if isinstance(ts_value, datetime):
            dt = ts_value
        else:
            text = str(ts_value).strip()
            if not text:
                return 0.0
            # Python 3.10 can't parse trailing 'Z'; swap to +00:00
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds())
    except Exception:
        return 0.0


def _build_detection_road_data() -> list[dict[str, object]]:
    name_map = get_road_name_map()
    detected_roads = []
    for road_id, counts in get_detection_counts_by_road().items():
        vehicle_count = _as_int(counts.get("total"))
        if vehicle_count <= 0:
            continue
        free_flow_speed = _ffs_map.get(road_id, 50)
        vc_ratio = min(vehicle_count / 80.0, 1.5)
        avg_speed = max(5.0, free_flow_speed * (1.0 - min(vehicle_count / 80.0, 0.92)))
        detected_roads.append(
            {
                "road_name": name_map.get(road_id, road_id),
                "road_id": road_id,
                "avg_speed": round(avg_speed, 1),
                "free_flow_speed": free_flow_speed,
                "vehicle_count": vehicle_count,
                "travel_time": 0.0,
                "vc_ratio": vc_ratio,
                "detected_vehicle_count": vehicle_count,
                "has_speed_data": True,
            }
        )
    return detected_roads


def _estimate_detection_metrics(road_id: str, vehicle_count: int) -> tuple[float, float]:
    """Infer speed/occupancy when live state only has counts from YOLO."""
    free_flow_speed = float(_ffs_map.get(road_id, 50) or 50)
    camera_fov_km = 0.028
    capacity_per_km = 120.0
    density_per_km = float(max(vehicle_count, 0)) / camera_fov_km if camera_fov_km > 0 else 0.0
    vc_ratio = density_per_km / capacity_per_km if capacity_per_km > 0 else 0.0
    speed_factor = max(0.05, 1.0 - (vc_ratio / 1.5))
    return round(free_flow_speed * speed_factor, 1), min(vc_ratio, 2.0)


def _normalize_live_road_state_rows(raw_rows: list[dict[str, object]]) -> list[dict[str, object]]:
    """Patch partial runtime rows with detection-derived metrics.

    Real-mode tracker fallback can produce road rows that only contain
    ``vehicle_count`` while speed/occupancy remain zero. Treat those as partial
    observations and infer the missing metrics from the latest YOLO counts so
    realtime pages don't render misleading all-zero traffic conditions.
    """
    detection_counts = get_detection_counts_by_road()
    normalized: list[dict[str, object]] = []

    for original in raw_rows:
        row = dict(original)
        road_id = str(row.get("road_id") or "")
        if not road_id:
            continue

        detected_total = _as_int((detection_counts.get(road_id) or {}).get("total"), 0)
        vehicle_count = max(_as_int(row.get("vehicle_count"), 0), detected_total)
        speed = _as_float(row.get("avg_speed_kmh"), 0)
        occupancy_ratio = _as_float(row.get("occupancy_ratio"), 0)

        row["vehicle_count"] = vehicle_count
        if vehicle_count > 0 and speed <= 0 and occupancy_ratio <= 0:
            estimated_speed, estimated_ratio = _estimate_detection_metrics(road_id, vehicle_count)
            row["avg_speed_kmh"] = estimated_speed
            row["occupancy_ratio"] = estimated_ratio
            row["source"] = str(row.get("source") or "live-state")
            row["metric_source"] = "detection-estimate"

        normalized.append(row)

    return normalized


def _build_live_index_response():
    road_data = _normalize_live_road_state_rows(get_latest_road_state())
    if not road_data:
        detected = _build_detection_road_data()
        if not detected:
            return None
        area_idx, road_results = calculate_area_index(detected)
        return {
            "status": "ok",
            "index": area_idx,
            "level": get_congestion_level(area_idx),
            "color": get_congestion_color(area_idx),
            "roads": [
                {
                    "road": rr["road_name"],
                    "index": rr["index"],
                    "speed": rr["avg_speed"],
                    "free_flow_speed": rr["free_flow_speed"],
                    "vehicle_count": rr["vehicle_count"],
                    "detected_vehicle_count": rr.get("vehicle_count", 0),
                    "level": rr.get("level"),
                    "has_data": rr.get("has_data", False),
                }
                for rr in road_results
            ],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "source": "detection-fallback",
        }

    name_map = get_road_name_map()
    normalized = []
    for row in road_data:
        road_id = str(row.get("road_id") or "")
        avg_speed = _as_float(row.get("avg_speed_kmh"))
        vehicle_count = _as_int(row.get("vehicle_count"))
        normalized.append(
            {
                "road_name": name_map.get(road_id, road_id),
                "road_id": road_id,
                "avg_speed": avg_speed,
                "free_flow_speed": _ffs_map.get(road_id, 50),
                "vehicle_count": vehicle_count,
                "travel_time": 0.0,
                "vc_ratio": _as_float(row.get("occupancy_ratio")),
                "detected_vehicle_count": vehicle_count,
                "has_speed_data": avg_speed > 0,
            }
        )

    area_idx, road_results = calculate_area_index(normalized)
    mapped_roads = [
        {
            "road": rr["road_name"],
            "index": rr["index"],
            "speed": rr["avg_speed"],
            "free_flow_speed": rr["free_flow_speed"],
            "vehicle_count": rr["vehicle_count"],
            "level": rr.get("level"),
            "has_data": rr.get("has_data", False),
            "detected_vehicle_count": rr.get("vehicle_count", 0),
        }
        for rr in road_results
    ]
    return {
        "status": "ok",
        "index": area_idx,
        "level": get_congestion_level(area_idx),
        "color": get_congestion_color(area_idx),
        "roads": mapped_roads,
        "timestamp": max((_as_timestamp_string(row.get("timestamp")) for row in road_data), default=datetime.now(timezone.utc).isoformat()),
        "source": "live-state",
    }


def _build_live_density_response():
    raw = _normalize_live_road_state_rows(get_latest_road_state())
    if not raw:
        detected = _build_detection_road_data()
        if not detected:
            return None
        area_idx, road_results = calculate_area_index(detected)
        return {
            "status": "ok",
            "roads": [
                {
                    "road": rr["road_name"],
                    "road_id": rr["road_id"],
                    "density": rr["vehicle_count"],
                    "vehicle_count": rr["vehicle_count"],
                    "speed": rr["avg_speed"],
                    "free_flow_speed": rr["free_flow_speed"],
                    "index": rr["index"],
                    "level": rr["level"],
                    "has_data": rr.get("has_data", False),
                    "detected_vehicle_count": rr["vehicle_count"],
                    "travel_time": "-",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "source": "detection-fallback",
                }
                for rr in road_results
            ],
            "area_index": area_idx,
        }

    name_map = get_road_name_map()
    roads = []
    for row in raw:
        road_id = str(row.get("road_id") or "")
        speed = _as_float(row.get("avg_speed_kmh"))
        vehicle_count = _as_int(row.get("vehicle_count"))
        ffs = _ffs_map.get(road_id, 50)
        has_speed = speed > 0
        idx = calculate_road_index(
            speed,
            ffs,
            _as_float(row.get("occupancy_ratio")),
            vehicle_count=vehicle_count,
            has_speed_data=has_speed,
        )
        level = get_congestion_level(idx)
        roads.append(
            {
                "road": name_map.get(road_id, road_id),
                "road_id": road_id,
                "density": vehicle_count,
                "vehicle_count": vehicle_count,
                "speed": round(speed, 1),
                "free_flow_speed": ffs,
                "index": idx if idx is not None else 0.0,
                "level": level,
                "has_data": idx is not None,
                "detected_vehicle_count": vehicle_count,
                "travel_time": "-",
                "timestamp": _as_timestamp_string(row.get("timestamp")),
                "source": row.get("source", "live-state"),
            }
        )
    return {"status": "ok", "roads": roads}


def _dominant_light_state(raw_state: object, current_phase: int) -> str:
    if isinstance(raw_state, dict):
        display_state = str(raw_state.get("display_state") or raw_state.get("state") or "").lower()
        if display_state in {"green", "yellow", "red"}:
            return display_state
        signal_state = str(raw_state.get("current_state") or "").lower()
        if "g" in signal_state:
            return "green"
        if "y" in signal_state:
            return "yellow"
        if "r" in signal_state:
            return "red"
    return "green" if current_phase > 0 else "red"


@traffic_bp.route("/vehicles")
def api_vehicles():
    """Get all vehicle positions from live state or SUMO simulation."""
    if _live_mode_enabled():
        vehicles = get_live_vehicle_map_data()
        if vehicles:
            return jsonify({"status": "ok", "vehicles": vehicles, "step": 0, "count": len(vehicles)})

    if _sim is None or not _sim.sim_active:
        return jsonify({"status": "waiting", "vehicles": [], "step": 0, "count": 0})

    vehs = []
    try:
        traci = _sim.get_traci()
        with _sim.sim_lock:
            for vid in traci.vehicle.getIDList():
                try:
                    x, y = traci.vehicle.getPosition(vid)
                    spd = traci.vehicle.getSpeed(vid)
                    col = traci.vehicle.getColor(vid)
                    vtype = traci.vehicle.getTypeID(vid)
                    lat, lng = sumo_xy_to_latlng(x, y)
                    vehs.append({
                        "id": vid, "lat": lat, "lng": lng,
                        "speed": round(spd * 3.6, 1),
                        "color": f"rgb({col[0]},{col[1]},{col[2]})",
                        "type": vtype,
                    })
                except Exception:
                    continue
    except Exception:
        pass

    return jsonify({
        "status": "ok", "vehicles": vehs,
        "step": _sim.step, "count": len(vehs),
    })


@traffic_bp.route("/traffic-index")
def api_traffic_index():
    """Get current traffic congestion index (0-10). Uses live SUMO when active."""
    if _live_mode_enabled():
        live_response = _build_live_index_response()
        if live_response is not None:
            return jsonify(live_response)

    # ── Live path: query SUMO directly ──
    if _sim and _sim.sim_active and _sim.road_mapping:
        try:
            from services.density import compute_density_from_sumo
            import json, os
            from config import Config

            # Load Thai names
            name_map = {}
            try:
                rp = os.path.join(Config.PROJECT_ROOT, "data", "pathumwan_roads.json")
                with open(rp, encoding="utf-8") as f:
                    for rd in json.load(f).get("roads", []):
                        name_map[rd["code"]] = rd.get("name_th", rd["code"])
            except Exception:
                pass

            traci = _sim.get_traci()
            with _sim.sim_lock:
                road_data = compute_density_from_sumo(traci, _sim.road_mapping)
            road_data = merge_detection_floor(road_data)
            for rd in road_data:
                rd["road_name"] = name_map.get(rd.get("road_id", ""), rd.get("road_name", rd.get("road_id", "")))

            area_idx, road_results = calculate_area_index(road_data)
            mapped_roads = [
                {
                    "road": rr["road_name"],
                    "index": rr["index"],
                    "speed": rr["avg_speed"],
                    "free_flow_speed": rr["free_flow_speed"],
                    "vehicle_count": rr["vehicle_count"],
                    "detected_vehicle_count": rr.get("detected_vehicle_count", 0),
                    "level": rr.get("level"),
                    "has_data": rr.get("has_data", False),
                }
                for rr in road_results
            ]
            return jsonify({
                "status": "ok",
                "index": area_idx,
                "level": get_congestion_level(area_idx),
                "color": get_congestion_color(area_idx),
                "roads": mapped_roads,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "source": "sumo-live",
                "freshness_seconds": 0.0,
                # True when at least one road actually has live data; lets the
                # frontend distinguish "0 รถ" (genuinely empty) from
                # "ไม่มีข้อมูล" (sim not yet producing numbers).
                "data_available": any(bool(r.get("has_data")) for r in mapped_roads),
            })
        except Exception:
            pass

    # ── Fallback: read from DB ──
    data = get_current_index()
    idx = data.get("index_value", 0.0)
    raw_roads = data.get("roads", [])
    if not raw_roads:
        detected = _build_detection_road_data()
        if detected:
            idx, raw_roads = calculate_area_index(detected)
        else:
            density_rows = get_road_density_list()
            normalized = []
            for row in density_rows:
                road_id = str(row.get("road_id") or "")
                speed_val = _as_float(row.get("avg_speed"), 0)
                normalized.append(
                    {
                        "road_name": str(row.get("road_name") or road_id),
                        "road_id": road_id,
                        "avg_speed": speed_val,
                        "free_flow_speed": _ffs_map.get(road_id, 50),
                        "vehicle_count": _as_int(row.get("vehicle_count")),
                        "travel_time": 0.0,
                        "vc_ratio": _as_float(row.get("vc_ratio")),
                        "has_speed_data": speed_val > 0,
                    }
                )
            idx, raw_roads = calculate_area_index(normalized)
    mapped_roads = [
        {
            "road": rd.get("road_name", rd.get("road_id", "")),
            "index": rd.get("index", 0),
            "speed": rd.get("avg_speed", 0),
            "free_flow_speed": _ffs_map.get(rd.get("road_id", ""), 50),
            "vehicle_count": rd.get("vehicle_count", 0),
            "level": rd.get("level"),
            "has_data": rd.get("has_data", False),
        }
        for rd in raw_roads
    ]
    db_timestamp = str(data.get("timestamp", ""))
    return jsonify({
        "status": "ok",
        "index": idx,
        "level": get_congestion_level(idx),
        "color": get_congestion_color(idx),
        "roads": mapped_roads,
        "timestamp": db_timestamp,
        "source": "db-cache",
        "freshness_seconds": round(_freshness_seconds(db_timestamp), 1),
        "data_available": any(bool(r.get("has_data")) for r in mapped_roads),
    })


@traffic_bp.route("/road-density")
def api_road_density():
    """Get current density for all monitored roads. Uses live SUMO when active."""
    if _live_mode_enabled():
        live_response = _build_live_density_response()
        if live_response is not None:
            return jsonify(live_response)

    # ── Live path: query SUMO directly ──
    if _sim and _sim.sim_active and _sim.road_mapping:
        try:
            from services.density import compute_density_from_sumo
            import json, os
            from config import Config

            name_map = {}
            try:
                rp = os.path.join(Config.PROJECT_ROOT, "data", "pathumwan_roads.json")
                with open(rp, encoding="utf-8") as f:
                    for rd in json.load(f).get("roads", []):
                        name_map[rd["code"]] = rd.get("name_th", rd["code"])
            except Exception:
                pass

            traci = _sim.get_traci()
            with _sim.sim_lock:
                road_data = compute_density_from_sumo(traci, _sim.road_mapping)

            road_data = merge_detection_floor(road_data)
            now_iso = datetime.now(timezone.utc).isoformat()
            roads = []
            for r in road_data:
                spd = float(r.get("avg_speed", 0) or 0)
                road_id = r.get("road_id", "")
                ffs = _ffs_map.get(road_id, 50)
                vc = r.get("vc_ratio", None)
                vcount = int(r.get("vehicle_count", 0) or 0)
                has_speed = bool(r.get("has_speed_data", spd > 0))
                idx = calculate_road_index(spd, ffs, vc, vehicle_count=vcount, has_speed_data=has_speed)
                level = get_congestion_level(idx)
                roads.append({
                    "road": name_map.get(road_id, r.get("road_name", road_id)),
                    "road_id": road_id,
                    "density": vcount,
                    "vehicle_count": vcount,
                    "speed": round(spd, 1),
                    "free_flow_speed": ffs,
                    "index": idx if idx is not None else 0.0,
                    "level": level,
                    "detected_vehicle_count": r.get("detected_vehicle_count", 0),
                    "travel_time": "-",
                    "timestamp": now_iso,
                    "source": "sumo-live",
                    "freshness_seconds": 0.0,
                    "has_data": idx is not None,
                })
            return jsonify({
                "status": "ok",
                "roads": roads,
                "data_available": any(bool(r.get("has_data")) for r in roads),
            })
        except Exception:
            pass

    # ── Fallback: read from DB ──
    detected = _build_detection_road_data()
    if detected:
        area_idx, road_results = calculate_area_index(detected)
        roads_payload = [
            {
                "road": rr["road_name"],
                "road_id": rr["road_id"],
                "density": rr["vehicle_count"],
                "vehicle_count": rr["vehicle_count"],
                "speed": rr["avg_speed"],
                "free_flow_speed": rr["free_flow_speed"],
                "index": rr["index"],
                "level": rr["level"],
                "has_data": rr.get("has_data", False),
                "travel_time": "-",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "source": "detection-fallback",
            }
            for rr in road_results
        ]
        return jsonify({
            "status": "ok",
            "roads": roads_payload,
            "area_index": area_idx,
            "data_available": any(bool(r.get("has_data")) for r in roads_payload),
        })

    raw = get_road_density_list()
    roads = []
    for r in raw:
        spd = float(r.get("avg_speed", 0) or 0)
        road_id = r.get("road_id", "")
        ffs = _ffs_map.get(road_id, 50)
        vc = r.get("vc_ratio", None)
        vcount = int(r.get("vehicle_count", 0) or 0)
        has_speed = spd > 0
        idx = calculate_road_index(spd, ffs, vc, vehicle_count=vcount, has_speed_data=has_speed)
        level = get_congestion_level(idx)
        ts = r.get("timestamp", "")
        roads.append({
            "road": r.get("road_name", road_id),
            "road_id": road_id,
            "density": vcount,
            "vehicle_count": vcount,
            "speed": round(spd, 1),
            "free_flow_speed": ffs,
            "index": idx if idx is not None else 0.0,
            "level": level,
            "travel_time": "-",
            "timestamp": ts,
            "source": "db-cache",
            "freshness_seconds": round(_freshness_seconds(ts), 1),
            "has_data": idx is not None,
        })
    return jsonify({
        "status": "ok",
        "roads": roads,
        "data_available": any(bool(r.get("has_data")) for r in roads),
    })


@traffic_bp.route("/traffic-lights")
def api_traffic_lights():
    """Get current traffic light states."""
    if _live_mode_enabled():
        junctions = get_latest_junction_state()
        camera_catalog = get_camera_catalog()
        lights = []
        for item in junctions:
            raw_camera_ids = item.get("camera_ids")
            camera_ids = raw_camera_ids if isinstance(raw_camera_ids, list) else []
            if not camera_ids:
                continue
            first_camera_id = str(camera_ids[0]) if camera_ids else ""
            camera = camera_catalog.get(first_camera_id, {})
            if not camera:
                continue
            raw_state = _as_dict(item.get("raw_state"))
            raw_state = {
                "display_state": item.get("controller_status", ""),
                **raw_state,
            }
            lights.append(
                {
                    "id": item.get("junction_id", ""),
                    "lat": camera.get("lat", 0),
                    "lng": camera.get("lng", 0),
                    "state": _dominant_light_state(raw_state, _as_int(item.get("current_phase"))),
                    "junction_name": camera.get("name", item.get("junction_id", "")),
                    "raw": str(raw_state.get("current_state") or raw_state.get("display_state") or "")[:20],
                }
            )
        if lights:
            return jsonify({"status": "ok", "lights": lights})

    if _sim is None or not _sim.sim_active:
        return jsonify({"status": "waiting", "lights": []})

    lights = []
    try:
        traci = _sim.get_traci()
        with _sim.sim_lock:
            for tid in traci.trafficlight.getIDList():
                try:
                    state = traci.trafficlight.getRedYellowGreenState(tid)
                    g = state.lower().count("g")
                    y = state.lower().count("y")
                    total = max(1, len(state))
                    dom = "green" if g / total > 0.3 else ("yellow" if y / total > 0.2 else "red")
                    cam = next(
                        (c for c in _sim.camera_points if str(c.get("sumo_tls_id", "")) == str(tid)),
                        None,
                    )
                    if cam is None:
                        cam = next((c for c in _sim.camera_points if c.get("junction") == tid), None)
                    if cam:
                        lights.append({
                            "id": tid, "lat": cam["lat"], "lng": cam["lng"],
                            "state": dom, "junction_name": cam.get("name", tid),
                            "raw": state[:20],
                        })
                except Exception:
                    continue
    except Exception:
        pass

    return jsonify({"status": "ok", "lights": lights})


@traffic_bp.route("/status")
def api_status():
    """Get simulation status."""
    if _sim is None:
        return jsonify({
            "step": 0,
            "active": False,
            "error": "Simulation not loaded",
            "camera_count": 0,
            "backends": get_runtime_backends(),
        })
    return jsonify({
        "step": _sim.step, "active": _sim.sim_active,
        "error": _sim.sim_error, "camera_count": len(_sim.camera_points),
        "backends": get_runtime_backends(),
    })


def _compute_road_geometry():
    """Compute road geometry (lat/lng polylines) for monitored road codes."""
    roads_path = os.path.join(Config.PROJECT_ROOT, "data", "pathumwan_roads.json")
    try:
        with open(roads_path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = {"roads": []}

    roads = data.get("roads", []) or []
    name_map = {r.get("code"): r.get("name_th", r.get("code")) for r in roads if r.get("code")}
    road_codes = [r.get("code") for r in roads if r.get("code")]

    mapping = {}
    if _sim is not None:
        mapping = getattr(_sim, "road_mapping", {}) or {}
        if not mapping and hasattr(_sim, "_build_road_mapping"):
            try:
                mapping = _sim._build_road_mapping()  # type: ignore[attr-defined]
            except Exception:
                mapping = {}

    net_file = os.path.join(Config.PROJECT_ROOT, "osm.net.xml")
    try:
        import sumolib
        net = sumolib.net.readNet(net_file, withInternal=False)
    except Exception:
        net = None

    # Long roads (พระราม 4, เพชรบุรี …) extend outside Pathumwan in the OSM
    # extract. Clipping each segment to the district bbox keeps the density
    # page's "click-to-zoom" focused on the monitored portion of the road
    # instead of flying the map outside the area.
    from cctv import is_in_pathumwan

    out = []
    for code in road_codes:
        edge_ids = mapping.get(code, []) if mapping else []
        segments = []
        min_lat = min_lng = max_lat = max_lng = None

        if net and edge_ids:
            for eid in edge_ids:
                try:
                    edge = net.getEdge(eid)
                    shape = edge.getShape() or []
                except Exception:
                    continue
                if len(shape) < 2:
                    continue
                # Walk the shape, splitting into separate sub-segments wherever
                # the polyline leaves the Pathumwan bbox so a single edge that
                # crosses the boundary contributes only its in-district parts.
                current: list[list[float]] = []
                for x, y in shape:
                    lat, lng = sumo_xy_to_latlng(x, y)
                    if is_in_pathumwan(lat, lng):
                        current.append([lat, lng])
                        min_lat = lat if min_lat is None else min(min_lat, lat)
                        max_lat = lat if max_lat is None else max(max_lat, lat)
                        min_lng = lng if min_lng is None else min(min_lng, lng)
                        max_lng = lng if max_lng is None else max(max_lng, lng)
                    else:
                        if len(current) >= 2:
                            segments.append(current)
                        current = []
                if len(current) >= 2:
                    segments.append(current)

        bbox = None
        if min_lat is not None:
            bbox = {
                "min_lat": min_lat,
                "min_lng": min_lng,
                "max_lat": max_lat,
                "max_lng": max_lng,
            }

        out.append({
            "road_id": code,
            "road": name_map.get(code, code),
            "segments": segments,
            "bbox": bbox,
        })

    return out


@traffic_bp.route("/roads/geometry")
def api_roads_geometry():
    """Get SUMO-derived road polylines for rendering on the density map."""
    global _road_geom_cache

    with _road_geom_lock:
        if _road_geom_cache is None:
            _road_geom_cache = _compute_road_geometry()

    return jsonify({"status": "ok", "roads": _road_geom_cache})
