"""CCTV camera collection.

The source of truth for live camera placement is the set of SUMO traffic-light
junctions. Existing camera definitions from pathumwan_roads.json are reused as
labels when they match a nearby traffic light; otherwise a deterministic camera
id/name is generated from the junction itself.
"""

import os
import json
import math
import hashlib
import re
from utils import sumo_xy_to_latlng
from config import Config

# Pathumwan district bounding box. Anything outside this box is a TLS the OSM
# extract included for context (roads crossing into neighbouring districts) but
# is NOT part of the monitored area — filtering these out stops ghost cameras
# like (13.7471, 100.4996) from appearing on the dashboard.
PATHUMWAN_BBOX = {
    "min_lat": 13.720,
    "max_lat": 13.760,
    "min_lng": 100.510,
    "max_lng": 100.555,
}


def is_in_pathumwan(lat: float, lng: float) -> bool:
    return (
        PATHUMWAN_BBOX["min_lat"] <= lat <= PATHUMWAN_BBOX["max_lat"]
        and PATHUMWAN_BBOX["min_lng"] <= lng <= PATHUMWAN_BBOX["max_lng"]
    )

# Load camera definitions from pathumwan_roads.json
_CAMERA_DEFS = []
try:
    _roads_path = os.path.join(Config.PROJECT_ROOT, "data", "pathumwan_roads.json")
    with open(_roads_path, encoding="utf-8") as f:
        _road_data = json.load(f)
    _CAMERA_DEFS = [
        cam for cam in _road_data.get("cameras", [])
        if str(cam.get("status", "active")).lower() == "active"
    ]
except Exception:
    pass

# Known CCTV camera locations in Pathumwan area
# (no longer needed — all camera definitions are in pathumwan_roads.json)
KNOWN_CCTV = []


def _sanitize_camera_id(raw_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9]+", "_", str(raw_id or "")).strip("_")
    if not safe:
        safe = "TLS"
    safe = safe[:32]
    suffix = hashlib.md5(str(raw_id or "").encode("utf-8")).hexdigest()[:6]
    return f"TLS_{safe}_{suffix}"


def _build_fallback_camera_label(street_names, tid, index):
    readable_names = [name for name in street_names if name]
    if len(readable_names) >= 2:
        road_label = readable_names[0]
        junction_label = f"{readable_names[0]} / {readable_names[1]}"
        return {
            "camera_id": _sanitize_camera_id(tid),
            "name": f"CCTV แยก {junction_label}",
            "road": road_label,
            "junction": junction_label,
        }
    if len(readable_names) == 1:
        road_label = readable_names[0]
        return {
            "camera_id": _sanitize_camera_id(tid),
            "name": f"CCTV {road_label}",
            "road": road_label,
            "junction": road_label,
        }
    return {
        "camera_id": _sanitize_camera_id(tid),
        "name": f"CCTV แยกสัญญาณ {index + 1}",
        "road": "",
        "junction": str(tid),
    }


def _get_tls_street_names(traci_module, tid):
    """Return up to two readable street names connected to a traffic light."""
    names = []
    seen = set()
    try:
        lane_ids = traci_module.trafficlight.getControlledLanes(tid)
    except Exception:
        lane_ids = []

    for lane_id in lane_ids:
        try:
            edge_id = traci_module.lane.getEdgeID(lane_id)
            if not edge_id or str(edge_id).startswith(":"):
                continue
            street = traci_module.edge.getStreetName(edge_id)
            if isinstance(street, (list, tuple)):
                street = street[0] if street else ""
            street = str(street).strip()
            if street and street not in seen:
                seen.add(street)
                names.append(street)
        except Exception:
            continue

    return names[:2]


def _find_matching_camera_def(lat, lng, used_ids, max_dist_km=0.18):
    best = None
    best_dist = max_dist_km
    for cam_def in _CAMERA_DEFS:
        cam_id = str(cam_def.get("id") or "")
        if not cam_id or cam_id in used_ids:
            continue
        dlat = float(cam_def.get("lat", 0) or 0) - lat
        dlng = float(cam_def.get("lng", 0) or 0) - lng
        dist = math.sqrt(dlat * dlat + dlng * dlng) * 111
        if dist < best_dist:
            best_dist = dist
            best = cam_def
    return best


def _get_tls_points(traci_module):
    """Collect traffic light centers so cameras can snap to real junctions."""
    points = []
    try:
        tls_ids = traci_module.trafficlight.getIDList()
    except Exception:
        return points

    for tid in tls_ids:
        try:
            x, y = None, None
            try:
                junctions = traci_module.trafficlight.getControlledJunctions(tid)
            except Exception:
                junctions = []

            if junctions:
                coords = []
                for jid in junctions:
                    try:
                        jx, jy = traci_module.junction.getPosition(jid)
                        coords.append((float(jx), float(jy)))
                    except Exception:
                        continue
                if coords:
                    x = sum(px for px, _ in coords) / len(coords)
                    y = sum(py for _, py in coords) / len(coords)

            if x is None or y is None:
                x, y = traci_module.junction.getPosition(tid)

            if x is None or y is None:
                continue

            points.append({
                "tid": str(tid),
                "x": float(x),
                "y": float(y),
                "street_names": _get_tls_street_names(traci_module, tid),
            })
        except Exception:
            continue
    return points


def collect_cameras(traci_module):
    """Create one camera point for every traffic light in the network."""
    cams = []
    tls_points = _get_tls_points(traci_module)
    used_defined_ids = set()

    for idx, tls in enumerate(tls_points):
        try:
            x = float(tls["x"])
            y = float(tls["y"])
            lat, lng = sumo_xy_to_latlng(x, y)
            if not is_in_pathumwan(lat, lng):
                continue
            sumo_tls_id = str(tls.get("tid") or "")
            street_names = tls.get("street_names") or []

            matched_def = _find_matching_camera_def(lat, lng, used_defined_ids)
            if matched_def is not None:
                cam_id = str(matched_def.get("id") or "").strip()
                used_defined_ids.add(cam_id)
                cam_name = matched_def.get("name", cam_id)
                cam_road = matched_def.get("road", street_names[0] if street_names else "")
                cam_junction = matched_def.get("junction", " / ".join(street_names[:2]))
                lat = float(matched_def.get("lat", lat))
                lng = float(matched_def.get("lng", lng))
            else:
                fallback = _build_fallback_camera_label(street_names, sumo_tls_id, idx)
                cam_id = fallback["camera_id"]
                cam_name = fallback["name"]
                cam_road = fallback["road"]
                cam_junction = fallback["junction"]

            cams.append({
                "id": idx + 1,
                "camera_id": cam_id,
                "name": cam_name,
                "road": cam_road,
                "x": x,
                "y": y,
                "lat": lat,
                "lng": lng,
                "junction": cam_junction,
                "street_names": street_names,
                "sumo_tls_id": sumo_tls_id,
            })
        except Exception:
            continue

    return cams
