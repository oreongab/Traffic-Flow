"""
Statistics Routes — Historical data, charts, rankings
"""

from flask import Blueprint, jsonify
from sqlalchemy import func
from database.connection import get_session
from database.models import HistoricalStats, TrafficIndex, HourlyVehicleCount
from services.density import get_detection_counts_by_road, get_camera_road_map, get_road_name_map, get_live_road_type_counts
from services.live_state import get_latest_road_state
from services.traffic_index import get_index_history, get_weekly_stats

stats_bp = Blueprint("stats", __name__, url_prefix="/api/stats")


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


@stats_bp.route("/index-today")
def api_index_today():
    """Traffic index chart data for the past 24 hours."""
    history = get_index_history(hours=24)
    chart_data = []
    for item in history:
        ts = str(item.get("timestamp", ""))
        hour = 0
        try:
            hour = int(ts.split(" ")[-1].split(":")[0]) if " " in ts else 0
        except (ValueError, IndexError):
            pass
        chart_data.append({
            "hour": hour,
            "time": ts,
            "index": item.get("index_value", 0),
        })
    return jsonify({"status": "ok", "data": chart_data})


@stats_bp.route("/index-weekly")
def api_index_weekly():
    """Past 7 days traffic index (max + avg per day)."""
    weekly = get_weekly_stats()
    result = [
        {
            "date": day["_id"],
            "max_index": round(day.get("max_index", 0), 1),
            "avg_index": round(day.get("avg_index", 0), 1),
        }
        for day in weekly
    ]
    return jsonify({"status": "ok", "data": result})


@stats_bp.route("/yearly/<int:year>")
def api_yearly_stats(year):
    """Yearly ranking: top days with highest traffic index."""
    session = get_session()
    try:
        rows = session.query(HistoricalStats).filter(
            HistoricalStats.year == year
        ).order_by(HistoricalStats.peak_index.desc()).limit(20).all()

        # Fallback: derive daily peak/avg from TrafficIndex when HistoricalStats is empty
        if not rows:
            from datetime import datetime, timezone

            start = datetime(year, 1, 1, tzinfo=timezone.utc)
            end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)

            day_expr = func.date(TrafficIndex.timestamp)
            sub = session.query(
                day_expr.label("day"),
                func.max(TrafficIndex.index_value).label("peak_index"),
                func.avg(TrafficIndex.index_value).label("avg_index"),
            ).filter(
                TrafficIndex.area == "pathumwan",
                TrafficIndex.timestamp >= start,
                TrafficIndex.timestamp < end,
            ).group_by(day_expr).subquery()

            peak_rows = session.query(
                sub.c.day,
                sub.c.peak_index,
                sub.c.avg_index,
                func.min(TrafficIndex.timestamp).label("peak_ts"),
            ).join(
                TrafficIndex,
                (func.date(TrafficIndex.timestamp) == sub.c.day)
                & (TrafficIndex.index_value == sub.c.peak_index)
                & (TrafficIndex.area == "pathumwan"),
            ).group_by(
                sub.c.day,
                sub.c.peak_index,
                sub.c.avg_index,
            ).order_by(sub.c.peak_index.desc()).limit(20).all()

            data = []
            for r in peak_rows:
                peak_ts = r.peak_ts
                peak_time = peak_ts.strftime("%H:%M") if peak_ts else ""
                data.append({
                    "date": str(r.day),
                    "peak_index": float(r.peak_index or 0),
                    "peak_time": peak_time,
                    "avg_index": float(r.avg_index or 0),
                    "time": peak_time,
                    "max_index": float(r.peak_index or 0),
                })

            return jsonify({"status": "ok", "year": year, "data": data})

        data = [
            {
                "date": r.date,
                "peak_index": r.peak_index,
                "peak_time": r.peak_time,
                "avg_index": r.avg_index,
                "time": r.peak_time,
                "max_index": r.peak_index,
            }
            for r in rows
        ]
        return jsonify({"status": "ok", "year": year, "data": data})
    finally:
        session.close()


@stats_bp.route("/daily-count/<int:year>")
def api_daily_count(year):
    """Daily vehicle count for a given year."""
    session = get_session()
    try:
        rows = session.query(HistoricalStats).filter(
            HistoricalStats.year == year
        ).order_by(HistoricalStats.date.desc()).all()

        if not rows:
            # Fallback: aggregate from HourlyVehicleCount (YOLO/SUMO hourly aggregation)
            prefix = f"{year}-"
            agg = session.query(
                HourlyVehicleCount.date.label("date"),
                func.sum(HourlyVehicleCount.total).label("total_vehicles"),
            ).filter(
                HourlyVehicleCount.date.like(prefix + "%")
            ).group_by(HourlyVehicleCount.date).order_by(HourlyVehicleCount.date.desc()).all()

            data = [
                {"date": r.date, "total_vehicles": int(r.total_vehicles or 0)}
                for r in agg
            ]
            return jsonify({"status": "ok", "year": year, "data": data})

        data = [
            {"date": r.date, "total_vehicles": r.total_vehicles}
            for r in rows
        ]
        return jsonify({"status": "ok", "year": year, "data": data})
    finally:
        session.close()


@stats_bp.route("/top-roads/<int:year>")
def api_top_roads(year):
    """TOP 10 most congested roads in a given year."""
    session = get_session()
    try:
        rows = session.query(HistoricalStats).filter(
            HistoricalStats.year == year
        ).all()

        # Fallback: compute from TrafficIndex. Works well for current/active datasets.
        if not rows:
            from datetime import datetime, timezone

            start = datetime(year, 1, 1, tzinfo=timezone.utc)
            end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)

            ti_rows = session.query(TrafficIndex.timestamp, TrafficIndex.roads).filter(
                TrafficIndex.area == "pathumwan",
                TrafficIndex.timestamp >= start,
                TrafficIndex.timestamp < end,
            ).order_by(TrafficIndex.timestamp.asc()).all()

            # daily_max[road_name][date] = max_index
            daily_max = {}
            for ts, roads in ti_rows:
                date_str = ts.date().isoformat() if ts else ""
                if not date_str:
                    continue
                for rd in (roads or []):
                    name = rd.get("road_name") or rd.get("road") or rd.get("road_id") or "Unknown"
                    idx = float(rd.get("index") or 0)
                    daily_max.setdefault(name, {})
                    prev = daily_max[name].get(date_str)
                    daily_max[name][date_str] = idx if prev is None else max(prev, idx)

            road_agg = {}
            for name, day_map in daily_max.items():
                vals = list(day_map.values())
                if not vals:
                    continue
                road_agg[name] = {"sum": sum(vals), "count": len(vals)}

            sorted_roads = sorted(
                road_agg.items(),
                key=lambda x: x[1]["sum"] / max(1, x[1]["count"]),
                reverse=True,
            )[:10]

            data = [
                {
                    "rank": i + 1,
                    "road": name,
                    "road_name": name,
                    "avg_max_index": round(vals["sum"] / max(1, vals["count"]), 1),
                }
                for i, (name, vals) in enumerate(sorted_roads)
            ]
            return jsonify({"status": "ok", "year": year, "data": data})

        # Aggregate road rankings across all days in the year
        road_agg = {}
        for r in rows:
            rankings = r.road_rankings or []
            for rd in rankings:
                name = rd.get("road_name", "Unknown")
                if name not in road_agg:
                    road_agg[name] = {"sum": 0, "count": 0}
                road_agg[name]["sum"] += rd.get("max_index", 0)
                road_agg[name]["count"] += 1

        sorted_roads = sorted(
            road_agg.items(),
            key=lambda x: x[1]["sum"] / max(1, x[1]["count"]),
            reverse=True,
        )[:10]

        data = [
            {
                "rank": i + 1,
                "road": name,
                "road_name": name,
                "avg_max_index": round(vals["sum"] / max(1, vals["count"]), 1),
            }
            for i, (name, vals) in enumerate(sorted_roads)
        ]
        return jsonify({"status": "ok", "year": year, "data": data})
    finally:
        session.close()


@stats_bp.route("/available-years")
def api_available_years():
    """Get list of years with available data."""
    session = get_session()
    try:
        years = set(
            y[0]
            for y in session.query(HistoricalStats.year)
            .distinct()
            .order_by(HistoricalStats.year.desc())
            .all()
            if y and y[0]
        )

        if not years:
            # Fallback sources: TrafficIndex timestamps and HourlyVehicleCount dates
            try:
                ti_years = session.query(func.extract("year", TrafficIndex.timestamp)).distinct().all()
                for y in ti_years:
                    try:
                        years.add(int(y[0]))
                    except Exception:
                        pass
            except Exception:
                pass

            try:
                hvc_years = session.query(func.substr(HourlyVehicleCount.date, 1, 4)).distinct().all()
                for y in hvc_years:
                    try:
                        years.add(int(y[0]))
                    except Exception:
                        pass
            except Exception:
                pass

        return jsonify({"status": "ok", "years": sorted(list(years), reverse=True)})
    finally:
        session.close()


@stats_bp.route("/hourly-counts")
def api_hourly_counts():
    """Get hourly vehicle counts per road for a given date (default: today).
    Falls back to latest YOLO detections for the current hour when DB is empty.
    """
    from flask import request
    from database.models import HourlyVehicleCount
    from datetime import datetime, timezone

    date_str = request.args.get("date", datetime.now(timezone.utc).strftime("%Y-%m-%d"))

    session = get_session()
    try:
        rows = session.query(HourlyVehicleCount).filter(
            HourlyVehicleCount.date == date_str
        ).order_by(HourlyVehicleCount.hour, HourlyVehicleCount.road_id).all()

        data = []
        for r in rows:
            road_id_value = str(getattr(r, "road_id", "") or "")
            if not road_id_value or road_id_value.startswith("TFF"):
                continue
            road_name_value = str(getattr(r, "road_name", "") or road_id_value)
            data.append({
                "hour": str(getattr(r, "hour", "")),
                "road_id": road_id_value,
                "road_name": road_name_value,
                "car": int(getattr(r, "car", 0) or 0),
                "motorcycle": int(getattr(r, "motorcycle", 0) or 0),
                "bus": int(getattr(r, "bus", 0) or 0),
                "truck": int(getattr(r, "truck", 0) or 0),
                "total": max(int(getattr(r, "total", 0) or 0), int(getattr(r, "car", 0) or 0) + int(getattr(r, "motorcycle", 0) or 0) + int(getattr(r, "bus", 0) or 0) + int(getattr(r, "truck", 0) or 0)),
                "avg_speed": round(float(getattr(r, "avg_speed", 0) or 0), 1),
                "density_index": round(float(getattr(r, "density_index", 0) or 0), 1),
            })

        # Live fallback: build the current hour from live road state / detections.
        if not data:
            try:
                now = datetime.now(timezone.utc)
                hour_str = now.strftime("%H:00")
                detection_by_road = get_detection_counts_by_road()
                live_road_map = {
                    str(row.get("road_id") or ""): row
                    for row in get_latest_road_state()
                }
                road_names = get_road_name_map()

                for rid, counts in detection_by_road.items():
                    live_row = live_road_map.get(rid, {})
                    data.append({
                        "hour": hour_str,
                        "road_id": rid,
                        "road_name": road_names.get(rid, str(live_row.get("road_id") or rid)),
                        "car": counts["car"],
                        "motorcycle": counts["motorcycle"],
                        "bus": counts["bus"],
                        "truck": counts["truck"],
                        "total": counts["total"],
                        "avg_speed": round(_as_float(live_row.get("avg_speed_kmh"), 0), 1),
                        "density_index": round(_as_float(live_row.get("occupancy_ratio"), 0) * 10, 1),
                        "source": str(live_row.get("source") or "camera-detection"),
                    })
            except Exception:
                pass

        return jsonify({"status": "ok", "date": date_str, "data": data})
    finally:
        session.close()


@stats_bp.route("/road-history/<road_id>")
def api_road_history(road_id):
    """Get hourly vehicle count history for a specific road (past N days)."""
    from flask import request
    from database.models import HourlyVehicleCount
    from datetime import datetime, timezone, timedelta

    days = int(request.args.get("days", 7))
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")

    session = get_session()
    try:
        rows = session.query(HourlyVehicleCount).filter(
            HourlyVehicleCount.road_id == road_id,
            HourlyVehicleCount.date >= since,
        ).order_by(HourlyVehicleCount.date.desc(), HourlyVehicleCount.hour).all()

        data = []
        for r in rows:
            road_id_value = str(getattr(r, "road_id", "") or "")
            road_name_value = str(getattr(r, "road_name", "") or road_id_value)
            data.append({
                "date": str(getattr(r, "date", "")),
                "hour": str(getattr(r, "hour", "")),
                "road_id": road_id_value,
                "road_name": road_name_value,
                "car": int(getattr(r, "car", 0) or 0),
                "motorcycle": int(getattr(r, "motorcycle", 0) or 0),
                "bus": int(getattr(r, "bus", 0) or 0),
                "truck": int(getattr(r, "truck", 0) or 0),
                "total": max(int(getattr(r, "total", 0) or 0), int(getattr(r, "car", 0) or 0) + int(getattr(r, "motorcycle", 0) or 0) + int(getattr(r, "bus", 0) or 0) + int(getattr(r, "truck", 0) or 0)),
                "avg_speed": round(float(getattr(r, "avg_speed", 0) or 0), 1),
                "density_index": round(float(getattr(r, "density_index", 0) or 0), 1),
            })
        return jsonify({"status": "ok", "road_id": road_id, "days": days, "data": data})
    finally:
        session.close()


@stats_bp.route("/realtime-counts")
def api_realtime_counts():
    """Get deduplicated real-time vehicle counts grouped by road."""
    from database.models import TrafficDetection
    from sqlalchemy import func
    from datetime import datetime, timezone, timedelta

    cam_road_map = get_camera_road_map()
    road_names = get_road_name_map()

    sim_camera_map = {}
    try:
        import simulation
        sim_camera_map = {
            str(cam.get("camera_id", "")): cam
            for cam in getattr(simulation, "camera_points", [])
        }
    except Exception:
        sim_camera_map = {}

    session = get_session()
    try:
        result = []

        # Prefer camera detections. To avoid duplicate counting across multiple
        # cameras on the same road we take the per-road maximum instead of sum.
        try:
            sub = session.query(
                TrafficDetection.camera_id,
                func.max(TrafficDetection.timestamp).label("max_ts"),
            ).group_by(TrafficDetection.camera_id).subquery()

            rows = session.query(TrafficDetection).join(
                sub,
                (TrafficDetection.camera_id == sub.c.camera_id) &
                (TrafficDetection.timestamp == sub.c.max_ts),
            ).all()

            road_data: dict = {}
            for det in rows:
                cam_id = str(getattr(det, "camera_id", "") or "")
                sim_cam = sim_camera_map.get(cam_id, {})
                fallback_road = sim_cam.get("road") or sim_cam.get("name") or cam_id
                fallback_name = sim_cam.get("name") or sim_cam.get("road") or cam_id
                road_id = cam_road_map.get(cam_id, fallback_road)
                counts = det.vehicle_counts or {}

                if road_id not in road_data:
                    road_data[road_id] = {
                        "road_id": road_id,
                        "road_name": road_names.get(road_id, fallback_name),
                        "car": 0,
                        "motorcycle": 0,
                        "bus": 0,
                        "truck": 0,
                        "total": 0,
                        "cameras": [],
                        "last_update": "",
                        "source": "camera-detection",
                    }
                rd = road_data[road_id]
                rd["car"] = max(rd["car"], int(counts.get("car", 0) or 0))
                rd["motorcycle"] = max(rd["motorcycle"], int(counts.get("motorcycle", 0) or 0))
                rd["bus"] = max(rd["bus"], int(counts.get("bus", 0) or 0))
                rd["truck"] = max(rd["truck"], int(counts.get("truck", 0) or 0))
                rd["total"] = max(rd["total"], int(counts.get("total", 0) or 0))
                rd["cameras"].append(cam_id)
                rd["last_update"] = str(det.timestamp)

            result = sorted(road_data.values(), key=lambda row: row["total"], reverse=True)
            if not any(_as_int(row.get("total"), 0) > 0 for row in result):
                result = []
        except Exception:
            result = []

        # Fallback to live SUMO road counts when per-camera detections are unavailable.
        if not result:
            try:
                import simulation

                if simulation.sim_active:
                    traci = simulation.get_traci()
                    with simulation.sim_lock:
                        live_counts = get_live_road_type_counts(traci, simulation.road_mapping)

                    for road_id, counts in live_counts.items():
                        total_count = _as_int(counts.get("total"), 0)
                        if total_count <= 0:
                            continue
                        result.append({
                            "road_id": road_id,
                            "road_name": road_names.get(road_id, road_id),
                            "car": _as_int(counts.get("car"), 0),
                            "motorcycle": _as_int(counts.get("motorcycle"), 0),
                            "bus": _as_int(counts.get("bus"), 0),
                            "truck": _as_int(counts.get("truck"), 0),
                            "total": total_count,
                            "cameras": [],
                            "last_update": datetime.now(timezone.utc).isoformat(),
                            "source": "sumo-live",
                        })

                    result.sort(key=lambda row: row["total"], reverse=True)
            except Exception:
                result = []

        # Final fallback to aggregated live-state rows when SUMO camera counts are unavailable.
        if not result:
            try:
                from datetime import datetime, timezone

                live_roads = get_latest_road_state()
                for rd in live_roads:
                    road_id = str(rd.get("road_id") or "")
                    total_count = _as_int(rd.get("vehicle_count"), 0)
                    if not road_id:
                        continue
                    if total_count <= 0:
                        continue
                    freshness_seconds = _as_float(rd.get("freshness_seconds"), 0.0)
                    timestamp_value = datetime.now(timezone.utc).isoformat()
                    if freshness_seconds > 0:
                        timestamp_value = (datetime.now(timezone.utc) - timedelta(seconds=freshness_seconds)).isoformat()
                    result.append({
                        "road_id": road_id,
                        "road_name": road_names.get(road_id, road_id),
                        "car": 0,
                        "motorcycle": 0,
                        "bus": 0,
                        "truck": 0,
                        "total": total_count,
                        "avg_speed": round(_as_float(rd.get("avg_speed_kmh"), 0), 1),
                        "cameras": [],
                        "last_update": timestamp_value,
                        "source": str(rd.get("source") or "live-state"),
                    })
                    result.sort(key=lambda row: row["total"], reverse=True)
            except Exception:
                result = []

        return jsonify({"status": "ok", "data": result})
    finally:
        session.close()
