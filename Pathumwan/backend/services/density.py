"""
Density Calculation Service
Computes road density and congestion levels from SUMO + detection data.
"""

import json
import os
from sqlalchemy import func
from database.connection import get_session
from database.models import TrafficDetection, RoadDensity, Camera
from config import Config

# Load free-flow speeds and Thai names from pathumwan_roads.json
_ffs_map = {}
_name_map = {}
_camera_road_map = {}
try:
    _roads_path = os.path.join(Config.PROJECT_ROOT, "data", "pathumwan_roads.json")
    with open(_roads_path, encoding="utf-8") as f:
        _road_data = json.load(f)
    for rd in _road_data.get("roads", []):
        _ffs_map[rd["code"]] = rd.get("free_flow_speed_kmh", 50)
        _name_map[rd["code"]] = rd.get("name_th", rd["code"])
    for cam in _road_data.get("cameras", []):
        if cam.get("id") and str(cam.get("status", "active")).lower() == "active":
            _camera_road_map[str(cam["id"])] = cam.get("road", "")
except Exception:
    pass


def get_camera_road_map():
    """Return the latest camera-to-road mapping, preferring database rows."""
    mapping = dict(_camera_road_map)
    session = get_session()
    try:
        rows = session.query(Camera).filter(Camera.status == "active").all()
        for row in rows:
            camera_id = str(row.camera_id or "")
            road_id = str(row.road or "")
            if camera_id and road_id:
                mapping[camera_id] = road_id
    except Exception:
        pass
    finally:
        session.close()
    return mapping


def get_road_name_map():
    """Expose the configured Thai road names to routes/services."""
    return dict(_name_map)


def _classify_vehicle_type(vtype_id: str) -> str:
    vt = str(vtype_id or "").lower()
    if "motorcycle" in vt or "moto" in vt:
        return "motorcycle"
    if "bus" in vt:
        return "bus"
    if "truck" in vt:
        return "truck"
    return "car"


def compute_density_from_sumo(traci_module, road_edges_map):
    """
    Compute density for each road group from SUMO TraCI.
    road_edges_map: {"RAMA1": ["edge1", "edge2", ...], ...}
    Returns list of road data dicts.
    """
    road_data = []

    for road_code, edges in road_edges_map.items():
        if not edges:
            continue

        total_vehicles = 0
        total_speed = 0
        total_length = 0
        total_lanes = 0
        halted_vehicles = 0
        speed_samples = 0

        for eid in edges:
            try:
                count = traci_module.edge.getLastStepVehicleNumber(eid)
                mean_speed = traci_module.edge.getLastStepMeanSpeed(eid) * 3.6
                length = traci_module.edge.getLength(eid)
                lanes = traci_module.edge.getLaneNumber(eid)
                halted = traci_module.edge.getLastStepHaltingNumber(eid)

                total_vehicles += count
                total_length += length
                total_lanes += max(1, lanes)
                halted_vehicles += halted
                if count > 0:
                    total_speed += mean_speed * count
                    speed_samples += count
            except Exception:
                continue

        # Use per-road free-flow speed from config.
        # IMPORTANT: do NOT fabricate avg_speed = ffs when speed_samples == 0.
        # Marking it as "no data" lets downstream (traffic_index + UI) distinguish
        # "genuinely empty road" from "actually flowing at free-flow speed".
        ffs = _ffs_map.get(road_code, 50)
        has_speed_sample = speed_samples > 0
        avg_speed = (total_speed / speed_samples) if has_speed_sample else 0.0

        # Estimate volume/capacity using actual mapped road length and lane count.
        lane_factor = max(1, round(total_lanes / max(1, len(edges))))
        capacity_per_km = 120 * lane_factor
        density = total_vehicles / max(0.1, total_length / 1000)
        vc_ratio = density / capacity_per_km if capacity_per_km > 0 else 0

        # Travel time = road_length / avg_speed (minutes). With no data we leave it 0.
        road_length_km = total_length / 1000.0
        if avg_speed > 0:
            travel_time = (road_length_km / avg_speed) * 60.0
        else:
            travel_time = 0.0

        road_data.append({
            "road_name": _name_map.get(road_code, road_code),
            "road_id": road_code,
            "avg_speed": avg_speed,
            "free_flow_speed": ffs,
            "vehicle_count": total_vehicles,
            "halted_count": halted_vehicles,
            "lane_factor": lane_factor,
            "vc_ratio": min(vc_ratio, 2.0),
            "road_length_km": round(road_length_km, 2),
            "travel_time": round(travel_time, 1),
            "has_speed_data": has_speed_sample,
        })

    return road_data


def compute_density_from_detection(camera_id=None):
    """Compute density from YOLO detection results stored in DB."""
    session = get_session()
    try:
        sub = session.query(
            TrafficDetection.camera_id,
            func.max(TrafficDetection.timestamp).label("max_ts"),
        )
        if camera_id:
            sub = sub.filter(TrafficDetection.camera_id == camera_id)
        sub = sub.group_by(TrafficDetection.camera_id).subquery()

        rows = session.query(TrafficDetection).join(
            sub,
            (TrafficDetection.camera_id == sub.c.camera_id) &
            (TrafficDetection.timestamp == sub.c.max_ts),
        ).all()

        result = []
        for det in rows:
            counts = det.vehicle_counts or {}
            result.append({
                "camera_id": det.camera_id,
                "vehicle_count": counts.get("total", 0),
                "car": counts.get("car", 0),
                "truck": counts.get("truck", 0),
                "bus": counts.get("bus", 0),
                "motorcycle": counts.get("motorcycle", 0),
                "timestamp": str(det.timestamp),
            })
        return result
    finally:
        session.close()


def get_detection_counts_by_road():
    """Group the latest YOLO detections by road using conservative max counts.

    Summing multiple cameras on the same road can double count the same vehicle,
    so we keep the maximum detected volume per road.
    """
    detection_rows = compute_density_from_detection()
    camera_road_map = get_camera_road_map()
    detection_by_road = {}

    for det in detection_rows:
        road_id = camera_road_map.get(str(det.get("camera_id", "")), "")
        if not road_id:
            continue

        current = detection_by_road.setdefault(
            road_id,
            {"car": 0, "motorcycle": 0, "bus": 0, "truck": 0, "total": 0},
        )
        for key in ("car", "motorcycle", "bus", "truck", "total"):
            current[key] = max(current[key], int(det.get(key, 0) or 0))

    return detection_by_road


def merge_detection_floor(road_data, prefer_detection=False):
    """Merge road-level YOLO detections into computed road data.

    When ``prefer_detection`` is false, detections act as a conservative floor.
    When true, YOLO becomes the primary source for ``vehicle_count`` and SUMO is
    kept only as fallback/context for metrics like speed and travel time.
    """
    detection_by_road = get_detection_counts_by_road()

    for rd in road_data:
        road_id = rd.get("road_id", "")
        det = detection_by_road.get(road_id)
        if not det:
            continue
        rd["detected_vehicle_count"] = det["total"]
        if prefer_detection:
            rd["vehicle_count"] = det["total"]
            rd["count_source"] = "camera-detection"
        else:
            rd["vehicle_count"] = max(int(rd.get("vehicle_count", 0) or 0), det["total"])

        # Dynamically calculate speed using Greenshields traffic model from video analytics counts
        # Camera radius is ~14m (28m total view = 0.028 km)
        camera_fov_km = 0.028
        capacity_per_km = 120 * max(1, rd.get("lane_factor", 1))
        density_per_km = float(rd["vehicle_count"]) / camera_fov_km
        vc_ratio = density_per_km / capacity_per_km if capacity_per_km > 0 else 0

        speed_factor = max(0.05, 1.0 - (vc_ratio / 1.5))
        ffs = float(rd.get("free_flow_speed", 50))

        has_speed = bool(rd.get("has_speed_data", False))
        current_speed = float(rd.get("avg_speed", 0.0) or 0.0)
        if prefer_detection or not has_speed or current_speed >= ffs * 0.99:
            # Overwrite speed with YOLO-based calculation when we don't trust the SUMO reading
            # (no SUMO sample yet, or SUMO only reported free-flow).
            new_speed = ffs * speed_factor
            rd["avg_speed"] = round(new_speed, 1)
            rd["vc_ratio"] = min(vc_ratio, 2.0)
            rd["has_speed_data"] = True
            if new_speed > 0:
                rd["travel_time"] = round((rd.get("road_length_km", 0.1) / new_speed) * 60.0, 1)

    return road_data


def get_live_road_type_counts(traci_module, road_edges_map):
    """Count unique live vehicles per road and type.

    Each vehicle is counted once per road using its SUMO vehicle id, which avoids
    duplicate counting across multiple camera views on the same road.
    """
    road_counts = {}

    for road_code, edges in (road_edges_map or {}).items():
        vehicle_ids = set()
        for edge_id in edges:
            try:
                vehicle_ids.update(str(vid) for vid in traci_module.edge.getLastStepVehicleIDs(edge_id))
            except Exception:
                continue

        counts = {"car": 0, "motorcycle": 0, "bus": 0, "truck": 0, "total": 0}
        for vehicle_id in vehicle_ids:
            try:
                vehicle_class = _classify_vehicle_type(traci_module.vehicle.getTypeID(vehicle_id))
                counts[vehicle_class] += 1
            except Exception:
                counts["car"] += 1
        counts["total"] = sum(counts.values())
        road_counts[road_code] = counts

    return road_counts


def get_road_stats():
    """Get aggregated road statistics for the dashboard."""
    session = get_session()
    try:
        sub = session.query(
            RoadDensity.road_id,
            func.max(RoadDensity.timestamp).label("max_ts"),
        ).group_by(RoadDensity.road_id).subquery()

        rows = session.query(RoadDensity).join(
            sub,
            (RoadDensity.road_id == sub.c.road_id) &
            (RoadDensity.timestamp == sub.c.max_ts),
        ).all()

        return [
            {
                "road_id": r.road_id,
                "road_name": r.road_name,
                "vehicle_count": r.vehicle_count,
                "density_level": r.density_level,
                "avg_speed": r.avg_speed,
                "timestamp": str(r.timestamp),
            }
            for r in rows
        ]
    finally:
        session.close()
