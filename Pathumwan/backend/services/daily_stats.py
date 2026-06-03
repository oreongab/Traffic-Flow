"""
Daily Historical Stats Aggregation + AI Decision Retention
==========================================================
Background loop that:
1. Aggregates daily stats into `historical_stats` for fast queries.
2. Purges stale raw data (TrafficDetection, RoadDensity, TrafficIndex,
   AIDecision) older than the configured retention window.
"""

import time
from datetime import datetime, timezone, timedelta

# How often the loop runs (seconds). Once per hour is fine.
LOOP_INTERVAL = 3600
# Per-table retention (days). traffic_detections grows the fastest (5s × 55 cameras),
# so keep a much shorter window than the derived tables.
RETENTION_DAYS = {
    "traffic_detections": 7,
    "traffic_index": 30,
    "road_density": 30,
    "signal_states": 30,
    "ai_decisions": 30,
}
# Legacy alias — old callers still reference this.
RAW_RETENTION_DAYS = 30


def start_daily_stats_loop():
    """Background loop — runs once per hour."""
    print("✓ Daily stats aggregation + retention loop started")
    # On first run, backfill any missing days
    try:
        backfill_missing_days()
    except Exception as e:
        print(f"  ⚠ Backfill error: {e}")
    while True:
        try:
            aggregate_yesterday()
            purge_old_raw_data()
        except Exception as e:
            print(f"  ⚠ Daily stats error: {e}")
        time.sleep(LOOP_INTERVAL)


# ── 1. Aggregate yesterday → historical_stats ──

def aggregate_yesterday():
    """Build one `HistoricalStats` row for yesterday (idempotent upsert)."""
    from database.connection import get_session
    from database.models import (
        HistoricalStats, TrafficIndex, HourlyVehicleCount,
    )
    from sqlalchemy import func, and_

    now = datetime.now(timezone.utc)
    yesterday = (now - timedelta(days=1)).date()
    date_str = yesterday.isoformat()          # "2026-04-13"
    year = yesterday.year

    session = get_session()
    try:
        # Already aggregated?
        existing = session.query(HistoricalStats).filter(
            HistoricalStats.date == date_str,
        ).first()
        if existing:
            return  # skip — already done

        day_start = datetime(yesterday.year, yesterday.month, yesterday.day,
                             tzinfo=timezone.utc)
        day_end = day_start + timedelta(days=1)

        # --- Peak / avg index from TrafficIndex ---
        idx_rows = session.query(
            func.max(TrafficIndex.index_value).label("peak"),
            func.avg(TrafficIndex.index_value).label("avg"),
        ).filter(
            TrafficIndex.area == "pathumwan",
            TrafficIndex.timestamp >= day_start,
            TrafficIndex.timestamp < day_end,
        ).first()

        peak_index = float(idx_rows.peak or 0)
        avg_index = round(float(idx_rows.avg or 0), 1)

        # Peak time — first row that hits the peak value
        peak_time = ""
        if peak_index > 0:
            peak_row = session.query(TrafficIndex.timestamp).filter(
                TrafficIndex.area == "pathumwan",
                TrafficIndex.timestamp >= day_start,
                TrafficIndex.timestamp < day_end,
                TrafficIndex.index_value == peak_index,
            ).order_by(TrafficIndex.timestamp.asc()).first()
            if peak_row and peak_row[0]:
                peak_time = peak_row[0].strftime("%H:%M")

        # --- Total vehicles from HourlyVehicleCount ---
        total_veh_row = session.query(
            func.sum(HourlyVehicleCount.total),
        ).filter(HourlyVehicleCount.date == date_str).first()
        total_vehicles = int(total_veh_row[0] or 0) if total_veh_row else 0

        # --- Road rankings: daily max index per road ---
        ti_rows = session.query(TrafficIndex.roads).filter(
            TrafficIndex.area == "pathumwan",
            TrafficIndex.timestamp >= day_start,
            TrafficIndex.timestamp < day_end,
        ).all()

        road_max: dict = {}
        for (roads_json,) in ti_rows:
            for rd in (roads_json or []):
                name = rd.get("road_name") or rd.get("road_id") or "Unknown"
                idx = float(rd.get("index") or 0)
                road_max[name] = max(road_max.get(name, 0), idx)

        road_rankings = sorted(
            [{"road_name": n, "max_index": round(v, 1)} for n, v in road_max.items()],
            key=lambda x: x["max_index"],
            reverse=True,
        )

        # --- Insert ---
        row = HistoricalStats(
            date=date_str,
            year=year,
            total_vehicles=total_vehicles,
            peak_index=peak_index,
            peak_time=peak_time,
            avg_index=avg_index,
            road_rankings=road_rankings,
        )
        session.add(row)
        session.commit()
        print(f"  📅 Historical stats saved for {date_str} "
              f"(peak={peak_index}, vehicles={total_vehicles})")
    except Exception as e:
        session.rollback()
        raise e
    finally:
        session.close()


def backfill_missing_days():
    """Backfill historical_stats for any past dates that have TrafficIndex data
    but no corresponding historical_stats row."""
    from database.connection import get_session
    from database.models import TrafficIndex, HistoricalStats
    from sqlalchemy import func

    session = get_session()
    try:
        # Find distinct dates that have TrafficIndex data
        dates_with_data = session.query(
            func.date(TrafficIndex.timestamp).label("day"),
        ).filter(
            TrafficIndex.area == "pathumwan",
            TrafficIndex.index_value > 0,
        ).group_by(func.date(TrafficIndex.timestamp)).all()

        today = datetime.now(timezone.utc).date()
        backfilled = 0
        for (day,) in dates_with_data:
            if day is None:
                continue
            # Convert to date if it's a string
            if isinstance(day, str):
                from datetime import date as date_type
                day = date_type.fromisoformat(day)
            # Skip today (not yet complete)
            if day >= today:
                continue
            date_str = day.isoformat()
            existing = session.query(HistoricalStats).filter(
                HistoricalStats.date == date_str,
            ).first()
            if existing:
                continue
            # Use aggregate_yesterday logic but for this specific date
            try:
                _aggregate_for_date(session, day)
                backfilled += 1
            except Exception as e:
                print(f"  ⚠ Backfill failed for {date_str}: {e}")
                session.rollback()

        if backfilled > 0:
            print(f"  📅 Backfilled {backfilled} missing historical_stats days")
    finally:
        session.close()


def _aggregate_for_date(session, target_date):
    """Aggregate stats for a specific date and insert into historical_stats."""
    from database.models import HistoricalStats, TrafficIndex, HourlyVehicleCount
    from sqlalchemy import func

    date_str = target_date.isoformat()
    year = target_date.year

    day_start = datetime(target_date.year, target_date.month, target_date.day,
                         tzinfo=timezone.utc)
    day_end = day_start + timedelta(days=1)

    idx_rows = session.query(
        func.max(TrafficIndex.index_value).label("peak"),
        func.avg(TrafficIndex.index_value).label("avg"),
    ).filter(
        TrafficIndex.area == "pathumwan",
        TrafficIndex.timestamp >= day_start,
        TrafficIndex.timestamp < day_end,
    ).first()

    peak_index = float(idx_rows.peak or 0)
    avg_index = round(float(idx_rows.avg or 0), 1)

    peak_time = ""
    if peak_index > 0:
        peak_row = session.query(TrafficIndex.timestamp).filter(
            TrafficIndex.area == "pathumwan",
            TrafficIndex.timestamp >= day_start,
            TrafficIndex.timestamp < day_end,
            TrafficIndex.index_value == peak_index,
        ).order_by(TrafficIndex.timestamp.asc()).first()
        if peak_row and peak_row[0]:
            peak_time = peak_row[0].strftime("%H:%M")

    total_veh_row = session.query(
        func.sum(HourlyVehicleCount.total),
    ).filter(HourlyVehicleCount.date == date_str).first()
    total_vehicles = int(total_veh_row[0] or 0) if total_veh_row else 0

    ti_rows = session.query(TrafficIndex.roads).filter(
        TrafficIndex.area == "pathumwan",
        TrafficIndex.timestamp >= day_start,
        TrafficIndex.timestamp < day_end,
    ).all()

    road_max: dict = {}
    for (roads_json,) in ti_rows:
        for rd in (roads_json or []):
            name = rd.get("road_name") or rd.get("road_id") or "Unknown"
            idx = float(rd.get("index") or 0)
            road_max[name] = max(road_max.get(name, 0), idx)

    road_rankings = sorted(
        [{"road_name": n, "max_index": round(v, 1)} for n, v in road_max.items()],
        key=lambda x: x["max_index"],
        reverse=True,
    )

    row = HistoricalStats(
        date=date_str,
        year=year,
        total_vehicles=total_vehicles,
        peak_index=peak_index,
        peak_time=peak_time,
        avg_index=avg_index,
        road_rankings=road_rankings,
    )
    session.add(row)
    session.commit()
    print(f"  📅 Backfilled historical stats for {date_str} (peak={peak_index})")


# ── 2. Purge stale raw data ──

def purge_old_raw_data():
    """Delete raw rows older than each table's configured retention window.

    See RETENTION_DAYS at the top of this module for per-table TTLs.
    traffic_detections is pruned aggressively (7 days) because it's written
    once per camera every DETECTION_INTERVAL seconds and would otherwise
    dominate DB size. Derived/audit tables keep 30 days for historical
    troubleshooting.
    """
    from database.connection import get_session
    from database.models import (
        TrafficDetection, RoadDensity, TrafficIndex, AIDecision,
        SignalState,
    )

    now = datetime.now(timezone.utc)
    session = get_session()
    try:
        tables = [
            ("traffic_detections", TrafficDetection, TrafficDetection.timestamp),
            ("road_density", RoadDensity, RoadDensity.timestamp),
            ("traffic_index", TrafficIndex, TrafficIndex.timestamp),
            ("ai_decisions", AIDecision, AIDecision.timestamp),
            ("signal_states", SignalState, SignalState.timestamp),
        ]
        for name, model, ts_col in tables:
            days = RETENTION_DAYS.get(name, RAW_RETENTION_DAYS)
            cutoff = now - timedelta(days=days)
            deleted = session.query(model).filter(ts_col < cutoff).delete(
                synchronize_session=False,
            )
            if deleted:
                print(f"  🗑 Purged {deleted} old rows from {name} (>{days} days)")
        session.commit()
    except Exception as e:
        session.rollback()
        print(f"  ⚠ Purge error: {e}")
    finally:
        session.close()
