"""Backfill statistics tables using YOLO detections as the primary source.

Rebuilds:
- hourly_vehicle_counts
- traffic_index
- historical_stats

The script uses conservative per-road maxima from traffic_detections to avoid
double counting when multiple cameras cover the same road. Existing road_density
rows are used only as speed/context when available.
"""

from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import Config
from database.connection import get_session
from database.models import Camera, HistoricalStats, HourlyVehicleCount, RoadDensity, TrafficDetection, TrafficIndex
from database.reference_data import ensure_road
from services.traffic_index import calculate_area_index


COUNT_KEYS = ("car", "motorcycle", "bus", "truck", "total")


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _as_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _ensure_aware(ts: datetime | None) -> datetime | None:
    if ts is None:
        return None
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def _load_maps() -> tuple[dict[str, str], dict[str, float], dict[str, str], dict[str, str]]:
    road_names: dict[str, str] = {}
    free_flow: dict[str, float] = {}
    camera_road: dict[str, str] = {}
    road_name_to_code: dict[str, str] = {}

    roads_path = os.path.join(Config.PROJECT_ROOT, "data", "pathumwan_roads.json")
    try:
        with open(roads_path, encoding="utf-8") as roads_file:
            data = json.load(roads_file)
        for road in data.get("roads", []):
            code = str(road.get("code") or "")
            name_th = str(road.get("name_th") or code)
            if not code:
                continue
            road_names[code] = name_th
            free_flow[code] = float(road.get("free_flow_speed_kmh") or 50)
            road_name_to_code[name_th] = code
        for camera in data.get("cameras", []):
            camera_id = str(camera.get("id") or "")
            road = str(camera.get("road") or "")
            if camera_id and road:
                camera_road[camera_id] = road
    except Exception:
        pass

    return road_names, free_flow, camera_road, road_name_to_code


def _normalize_road_id(value: str, road_name_to_code: dict[str, str]) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    return road_name_to_code.get(raw, raw)


def _hour_bucket(ts: datetime) -> tuple[str, str, datetime]:
    ts = _ensure_aware(ts) or datetime.now(timezone.utc)
    hour_ts = ts.replace(minute=0, second=0, microsecond=0)
    return hour_ts.strftime("%Y-%m-%d"), hour_ts.strftime("%H:00"), hour_ts


def main() -> None:
    road_names, free_flow, camera_road, road_name_to_code = _load_maps()
    session = get_session()
    try:
        db_cameras = session.query(Camera).all()
        for camera in db_cameras:
            camera_id = str(getattr(camera, "camera_id", "") or "")
            road = _normalize_road_id(str(getattr(camera, "road", "") or ""), road_name_to_code)
            if camera_id and road:
                camera_road[camera_id] = road

        detections = session.query(TrafficDetection).order_by(TrafficDetection.timestamp.asc()).all()
        if not detections:
            print("no_yolo_detections_found")
            return

        hourly_counts: dict[tuple[str, str, str], dict[str, object]] = {}
        covered_dates: set[str] = set()
        min_hour_ts: datetime | None = None
        max_hour_ts: datetime | None = None

        for detection in detections:
            raw_timestamp = getattr(detection, "timestamp", None)
            timestamp = _ensure_aware(raw_timestamp if isinstance(raw_timestamp, datetime) else None)
            if timestamp is None:
                continue
            detection_camera_id = str(getattr(detection, "camera_id", "") or "")
            road_id = _normalize_road_id(camera_road.get(detection_camera_id, ""), road_name_to_code)
            if not road_id:
                continue
            date_str, hour_str, hour_ts = _hour_bucket(timestamp)
            covered_dates.add(date_str)
            min_hour_ts = hour_ts if min_hour_ts is None else min(min_hour_ts, hour_ts)
            max_hour_ts = hour_ts if max_hour_ts is None else max(max_hour_ts, hour_ts)

            key = (date_str, hour_str, road_id)
            row = hourly_counts.setdefault(
                key,
                {
                    "date": date_str,
                    "hour": hour_str,
                    "road_id": road_id,
                    "road_name": road_names.get(road_id, road_id),
                    "car": 0,
                    "motorcycle": 0,
                    "bus": 0,
                    "truck": 0,
                    "total": 0,
                },
            )
            counts = getattr(detection, "vehicle_counts", {}) or {}
            for count_key in COUNT_KEYS:
                row[count_key] = max(_as_int(row.get(count_key)), _as_int(counts.get(count_key, 0)))

        if not hourly_counts or min_hour_ts is None or max_hour_ts is None:
            print("no_mapped_yolo_detections_found")
            return

        road_density_rows = session.query(RoadDensity).filter(
            RoadDensity.timestamp >= min_hour_ts,
            RoadDensity.timestamp < max_hour_ts.replace(minute=0, second=0, microsecond=0).replace(tzinfo=timezone.utc) + __import__('datetime').timedelta(hours=1),
        ).all()

        context_by_bucket: dict[tuple[str, str, str], dict[str, float]] = defaultdict(
            lambda: {"speed_sum": 0.0, "travel_sum": 0.0, "vc_sum": 0.0, "samples": 0.0}
        )
        for row in road_density_rows:
            raw_timestamp = getattr(row, "timestamp", None)
            timestamp = _ensure_aware(raw_timestamp if isinstance(raw_timestamp, datetime) else None)
            if timestamp is None:
                continue
            date_str, hour_str, _ = _hour_bucket(timestamp)
            road_id = _normalize_road_id(str(getattr(row, "road_id", "") or ""), road_name_to_code)
            if not road_id:
                continue
            bucket = context_by_bucket[(date_str, hour_str, road_id)]
            bucket["speed_sum"] += _as_float(getattr(row, "avg_speed", 0.0))
            bucket["travel_sum"] += _as_float(getattr(row, "travel_time", 0.0))
            bucket["vc_sum"] += _as_float(getattr(row, "vc_ratio", 0.0))
            bucket["samples"] += 1.0

        session.query(HourlyVehicleCount).filter(HourlyVehicleCount.date.in_(sorted(covered_dates))).delete(synchronize_session=False)
        session.query(HistoricalStats).filter(HistoricalStats.date.in_(sorted(covered_dates))).delete(synchronize_session=False)
        session.query(TrafficIndex).filter(
            TrafficIndex.area == "pathumwan",
            TrafficIndex.timestamp >= min_hour_ts,
            TrafficIndex.timestamp < max_hour_ts + __import__('datetime').timedelta(hours=1),
        ).delete(synchronize_session=False)
        session.commit()

        grouped_for_index: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)
        for key in sorted(hourly_counts.keys()):
            date_str, hour_str, road_id = key
            normalized_road_id = ensure_road(session, road_id, hourly_counts[key].get("road_name"))
            if not normalized_road_id:
                continue
            counts = hourly_counts[key]
            context = context_by_bucket.get((date_str, hour_str, road_id), {})
            samples = _as_float(context.get("samples", 0.0))
            avg_speed = _as_float(context.get("speed_sum", 0.0)) / samples if samples else _as_float(free_flow.get(normalized_road_id, free_flow.get(road_id, 50)))
            vc_ratio = _as_float(context.get("vc_sum", 0.0)) / samples if samples else min(1.5, _as_int(counts.get("total")) / 20.0)
            travel_time = _as_float(context.get("travel_sum", 0.0)) / samples if samples else 0.0

            session.add(HourlyVehicleCount(
                date=date_str,
                hour=hour_str,
                road_id=normalized_road_id,
                road_name=str(counts["road_name"]),
                car=_as_int(counts.get("car")),
                motorcycle=_as_int(counts.get("motorcycle")),
                bus=_as_int(counts.get("bus")),
                truck=_as_int(counts.get("truck")),
                total=_as_int(counts.get("total")),
                avg_speed=avg_speed,
                density_index=0.0,
                source="yolo",
            ))

            grouped_for_index[(date_str, hour_str)].append({
                "road_name": str(counts["road_name"]),
                "road_id": normalized_road_id,
                "vehicle_count": _as_int(counts.get("total")),
                "avg_speed": avg_speed,
                "free_flow_speed": _as_float(free_flow.get(normalized_road_id, free_flow.get(road_id, 50))),
                "vc_ratio": vc_ratio,
                "travel_time": travel_time,
            })

        inserted_index_rows = 0
        daily_index_rows: dict[str, list[TrafficIndex]] = defaultdict(list)
        for (date_str, hour_str), road_data in sorted(grouped_for_index.items()):
            area_index, road_results = calculate_area_index(road_data)
            hour_ts = datetime.strptime(f"{date_str} {hour_str}", "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
            traffic_index_row = TrafficIndex(
                timestamp=hour_ts,
                area="pathumwan",
                index_value=round(area_index, 1),
                roads=road_results,
            )
            session.add(traffic_index_row)
            daily_index_rows[date_str].append(traffic_index_row)
            inserted_index_rows += 1

        session.flush()

        for date_str in sorted(covered_dates):
            day_rows = daily_index_rows.get(date_str, [])
            peak_index = max((_as_float(getattr(row, "index_value", 0.0)) for row in day_rows), default=0.0)
            avg_index = round(
                sum(_as_float(getattr(row, "index_value", 0.0)) for row in day_rows) / max(1, len(day_rows)),
                1,
            ) if day_rows else 0.0

            peak_time = ""
            if day_rows:
                peak_row = min(
                    (row for row in day_rows if _as_float(getattr(row, "index_value", 0.0)) == peak_index),
                    key=lambda row: _ensure_aware(getattr(row, "timestamp", None) if isinstance(getattr(row, "timestamp", None), datetime) else None) or datetime.max.replace(tzinfo=timezone.utc),
                    default=None,
                )
                peak_timestamp = getattr(peak_row, "timestamp", None) if peak_row is not None else None
                peak_dt = _ensure_aware(peak_timestamp if isinstance(peak_timestamp, datetime) else None)
                if peak_dt is not None:
                    peak_time = peak_dt.strftime("%H:%M")

            day_hourly_rows = [row for (d, _h, _r), row in hourly_counts.items() if d == date_str]
            total_vehicles = sum(_as_int(row.get("total")) for row in day_hourly_rows)

            road_max: dict[str, float] = {}
            for row in day_rows:
                for road in (row.roads or []):
                    road_name = str(road.get("road_name") or road.get("road_id") or "Unknown")
                    road_index = float(road.get("index") or 0.0)
                    road_max[road_name] = max(road_max.get(road_name, 0.0), road_index)

            road_rankings = sorted(
                [{"road_name": name, "max_index": round(value, 1)} for name, value in road_max.items()],
                key=lambda item: item["max_index"],
                reverse=True,
            )

            session.add(HistoricalStats(
                date=date_str,
                year=int(date_str[:4]),
                total_vehicles=total_vehicles,
                peak_index=peak_index,
                peak_time=peak_time,
                avg_index=avg_index,
                road_rankings=road_rankings,
            ))

        session.commit()
        print(
            f"backfilled_dates={len(covered_dates)} hourly_rows={len(hourly_counts)} "
            f"traffic_index_rows={inserted_index_rows}"
        )
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


if __name__ == "__main__":
    main()