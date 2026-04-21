"""
Traffic Index Calculation Service
Calculates the traffic congestion index (0-10) for Pathumwan district.
"""

import json
import os
from datetime import datetime, timezone, timedelta

from sqlalchemy import func
from database.connection import get_session
from database.models import TrafficIndex, RoadDensity
from database.reference_data import ensure_road
from config import Config

# Load road definitions
_ROADS: dict = {"roads": [], "cameras": []}
_roads_path = os.path.join(Config.PROJECT_ROOT, "data", "pathumwan_roads.json")
try:
    with open(_roads_path, encoding="utf-8") as f:
        raw = f.read().strip()
        if raw:
            loaded = json.loads(raw)
            if isinstance(loaded, dict):
                _ROADS = loaded
except FileNotFoundError:
    pass
except (json.JSONDecodeError, OSError):
    # Keep defaults when the file is empty/invalid/unreadable.
    pass

# Build free-flow speed map
_ffs_map = {}
for _rd in _ROADS.get("roads", []):
    code = _rd.get("code")
    if code:
        _ffs_map[code] = _rd.get("free_flow_speed_kmh", 50)


NO_DATA_LEVEL = "ไม่มีข้อมูล"
NO_DATA_COLOR = "#9E9E9E"


def calculate_road_index(avg_speed, free_flow_speed, vc_ratio=None, density=None, vehicle_count=0, has_speed_data=None):
    """Calculate traffic index (0-10) for a single road.

    Returns ``None`` when there is no usable observation so the caller can tag
    the road as "ไม่มีข้อมูล" instead of falsely showing "คล่องตัว".
    """
    if free_flow_speed <= 0:
        free_flow_speed = 50

    if has_speed_data is None:
        has_speed_data = bool(avg_speed and avg_speed > 0)

    if has_speed_data and avg_speed and avg_speed > 0:
        speed_ratio = max(0, min(1, avg_speed / free_flow_speed))
        speed_index = (1 - speed_ratio) * 10

        if vc_ratio is not None and vc_ratio > 0:
            vc_index = min(vc_ratio, 1.5) / 1.5 * 10
            index = 0.5 * speed_index + 0.5 * vc_index
        else:
            index = speed_index

        return round(max(0, min(10, index)), 1)

    if vehicle_count > 0 and vc_ratio is not None and vc_ratio > 0:
        vc_index = min(vc_ratio, 1.5) / 1.5 * 10
        return round(max(0, min(10, vc_index)), 1)

    # No speed sample and no vehicles → genuinely no data.
    return None


def get_congestion_level(index_value):
    if index_value is None:
        return NO_DATA_LEVEL
    if index_value <= 3:
        return "คล่องตัว"
    elif index_value <= 5:
        return "หนาแน่น"
    elif index_value <= 7:
        return "ติดขัด"
    else:
        return "ติดมาก"


def get_congestion_color(index_value):
    if index_value is None:
        return NO_DATA_COLOR
    if index_value <= 3:
        return "#4CAF50"
    elif index_value <= 5:
        return "#FFC107"
    elif index_value <= 7:
        return "#FF9800"
    else:
        return "#F44336"


def calculate_area_index(road_data_list):
    """Calculate weighted area index from multiple roads.

    Roads tagged "ไม่มีข้อมูล" are excluded from the weighted average so the
    area summary reflects only roads with a real observation.
    """
    if not road_data_list:
        return 0.0, []

    total_weight = 0
    weighted_sum = 0
    road_results = []

    for rd in road_data_list:
        ffs = rd.get("free_flow_speed", _ffs_map.get(rd.get("road_id", ""), 50))
        avg_spd = rd.get("avg_speed", 0)
        vc = rd.get("vc_ratio")
        count = int(rd.get("vehicle_count", 0) or 0)
        has_speed = rd.get("has_speed_data")
        if has_speed is None:
            has_speed = bool(avg_spd and float(avg_spd) > 0)

        idx = calculate_road_index(avg_spd, ffs, vc, vehicle_count=count, has_speed_data=has_speed)
        level = get_congestion_level(idx)

        if idx is not None:
            weight = max(1, count)
            weighted_sum += idx * weight
            total_weight += weight

        road_results.append({
            "road_name": rd.get("road_name", "Unknown"),
            "road_id": rd.get("road_id", ""),
            "index": idx if idx is not None else 0.0,
            "has_data": idx is not None,
            "vehicle_count": count,
            "avg_speed": round(float(avg_spd or 0), 1),
            "free_flow_speed": ffs,
            "vc_ratio": round(vc, 3) if vc is not None else 0,
            "travel_time": round(rd.get("travel_time", 0.0), 1),
            "level": level,
            "color": get_congestion_color(idx),
        })

    area_index = round(weighted_sum / max(1, total_weight), 1) if total_weight > 0 else 0.0
    return area_index, road_results


def save_traffic_index(area_index, road_results):
    """Save calculated traffic index to PostgreSQL."""
    session = get_session()
    try:
        row = TrafficIndex(
            area="pathumwan",
            index_value=round(area_index, 1),
            roads=road_results,
        )
        session.add(row)
        session.commit()
        return row
    except Exception as e:
        session.rollback()
        print(f"  ⚠ TrafficIndex save failed: {type(e).__name__}: {e}")
        return None
    finally:
        session.close()


def save_road_density(road_result):
    """Save individual road density to PostgreSQL."""
    session = get_session()
    try:
        road_id = ensure_road(session, road_result.get("road_id"), road_result.get("road_name"))
        if not road_id:
            session.rollback()
            print(f"  ⚠ RoadDensity skipped — road not found: {road_result.get('road_id')}")
            return
        row = RoadDensity(
            road_name=road_result["road_name"],
            road_id=road_id,
            vehicle_count=road_result["vehicle_count"],
            density_level=road_result["level"],
            avg_speed=road_result["avg_speed"],
            travel_time=road_result.get("travel_time", 0.0),
            vc_ratio=road_result.get("vc_ratio", 0),
        )
        session.add(row)
        session.commit()
    except Exception as e:
        session.rollback()
        print(f"  ⚠ RoadDensity save failed [{road_result.get('road_id')}]: {type(e).__name__}: {e}")
    finally:
        session.close()


def get_current_index():
    """Get the latest traffic index from DB."""
    session = get_session()
    try:
        latest = session.query(TrafficIndex).filter(
            TrafficIndex.area == "pathumwan"
        ).order_by(TrafficIndex.timestamp.desc()).first()

        if latest:
            return {
                "id": latest.id,
                "area": latest.area,
                "index_value": latest.index_value,
                "roads": latest.roads or [],
                "timestamp": str(latest.timestamp),
            }
        return {"area": "pathumwan", "index_value": 0.0, "roads": []}
    finally:
        session.close()


def get_index_history(hours=24):
    """Get traffic index history for the past N hours."""
    session = get_session()
    try:
        since = datetime.now(timezone.utc) - timedelta(hours=hours)
        rows = session.query(TrafficIndex).filter(
            TrafficIndex.area == "pathumwan",
            TrafficIndex.timestamp >= since,
        ).order_by(TrafficIndex.timestamp.asc()).all()

        return [
            {
                "id": r.id,
                "area": r.area,
                "index_value": r.index_value,
                "roads": r.roads or [],
                "timestamp": str(r.timestamp),
            }
            for r in rows
        ]
    finally:
        session.close()


def get_weekly_stats():
    """Get past 7 days of traffic index stats."""
    session = get_session()
    try:
        since = datetime.now(timezone.utc) - timedelta(days=7)
        rows = session.query(
            func.date(TrafficIndex.timestamp).label("day"),
            func.max(TrafficIndex.index_value).label("max_index"),
            func.avg(TrafficIndex.index_value).label("avg_index"),
            func.count(TrafficIndex.id).label("count"),
        ).filter(
            TrafficIndex.area == "pathumwan",
            TrafficIndex.timestamp >= since,
        ).group_by(
            func.date(TrafficIndex.timestamp)
        ).order_by(
            func.date(TrafficIndex.timestamp).desc()
        ).all()

        return [
            {
                "_id": str(r.day),
                "max_index": float(r.max_index or 0),
                "avg_index": float(r.avg_index or 0),
                "count": r.count,
            }
            for r in rows
        ]
    finally:
        session.close()


def get_road_density_list():
    """Get latest density for each road."""
    session = get_session()
    try:
        roads = _ROADS.get("roads", [])
        result = []
        for road in roads:
            code = road.get("code", "")
            latest = session.query(RoadDensity).filter(
                RoadDensity.road_id == code
            ).order_by(RoadDensity.timestamp.desc()).first()

            if latest:
                result.append({
                    "road_name": latest.road_name,
                    "road_id": latest.road_id,
                    "vehicle_count": latest.vehicle_count,
                    "density_level": latest.density_level,
                    "avg_speed": latest.avg_speed,
                    "vc_ratio": latest.vc_ratio,
                    "timestamp": str(latest.timestamp),
                })
            else:
                result.append({
                    "road_name": road.get("name_th", code),
                    "road_id": code,
                    "vehicle_count": 0,
                    "density_level": "ไม่มีข้อมูล",
                    "avg_speed": 0,
                    "vc_ratio": 0,
                })
        return result
    finally:
        session.close()
