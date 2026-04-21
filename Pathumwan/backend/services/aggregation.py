"""
Hourly Vehicle Count Aggregation Service
Aggregates YOLO detection counts per road per hour and stores them for historical analysis.
"""

import time
from datetime import datetime, timezone


def start_hourly_aggregation_loop():
    """Background loop that aggregates detection data every 10 minutes."""
    print("✓ Hourly vehicle count aggregation started")

    while True:
        try:
            aggregate_current_hour()
        except Exception as e:
            print(f"  ⚠ Aggregation error: {e}")

        time.sleep(600)  # Run every 10 minutes


def aggregate_current_hour():
    """Aggregate YOLO detection counts for the current hour."""
    from database.connection import get_session
    from database.models import TrafficDetection, HourlyVehicleCount, RoadDensity
    from database.reference_data import ensure_road
    from sqlalchemy import func, and_
    from services.density import get_camera_road_map, get_road_name_map

    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d")
    hour_str = now.strftime("%H:00")

    cam_road_map = get_camera_road_map()
    road_names = get_road_name_map()

    session = get_session()
    try:
        # Get all detections from the current hour
        hour_start = now.replace(minute=0, second=0, microsecond=0)

        detections = session.query(TrafficDetection).filter(
            TrafficDetection.timestamp >= hour_start
        ).all()

        # Aggregate per road from YOLO detections
        road_counts: dict = {}
        for det in detections:
            cam_id = det.camera_id
            road_id = cam_road_map.get(cam_id, "")
            if not road_id:
                continue  # skip cameras with no road mapping (generic TFF-IDs)

            counts = det.vehicle_counts or {}
            if road_id not in road_counts:
                road_counts[road_id] = {
                    "car": 0, "motorcycle": 0, "bus": 0, "truck": 0,
                    "total": 0, "samples": 0,
                }
            rc = road_counts[road_id]
            rc["car"] = max(rc["car"], counts.get("car", 0))
            rc["motorcycle"] = max(rc["motorcycle"], counts.get("motorcycle", 0))
            rc["bus"] = max(rc["bus"], counts.get("bus", 0))
            rc["truck"] = max(rc["truck"], counts.get("truck", 0))
            rc["total"] = max(rc["total"], counts.get("total", 0))
            rc["samples"] += 1

        # Also aggregate speed/index context from persisted road density data.
        road_density_data = session.query(RoadDensity).filter(
            RoadDensity.timestamp >= hour_start
        ).all()

        road_speed: dict = {}
        road_index: dict = {}
        for rd in road_density_data:
            rid = rd.road_id
            if rid not in road_speed:
                road_speed[rid] = []
                road_index[rid] = []
            road_speed[rid].append(rd.avg_speed or 0)
            # Use vc_ratio as density index proxy
            vc = getattr(rd, "vc_ratio", None)
            if vc is not None and float(vc) > 0:
                road_index[rid].append(float(vc) * 10.0 / 1.5)  # scale to 0-10
            # Ensure road exists in road_counts from density data
            if rid not in road_counts:
                road_counts[rid] = {
                    "car": 0, "motorcycle": 0, "bus": 0, "truck": 0,
                    "total": rd.vehicle_count or 0, "samples": 0,
                }

        # Use SUMO only as a fallback for roads that have no YOLO detections yet.
        try:
            import simulation
            if simulation.sim_active and simulation.road_mapping:
                from services.density import compute_density_from_sumo, get_live_road_type_counts

                traci_mod = simulation.get_traci()
                with simulation.sim_lock:
                    sumo_roads = compute_density_from_sumo(traci_mod, simulation.road_mapping)
                    road_type_counts = get_live_road_type_counts(traci_mod, simulation.road_mapping)

                for srd in sumo_roads:
                    rid = srd.get("road_id", "")
                    if not rid:
                        continue
                    counts = road_type_counts.get(rid, {"car": 0, "motorcycle": 0, "bus": 0, "truck": 0, "total": 0})
                    if road_counts.get(rid, {}).get("samples", 0) == 0:
                        road_counts[rid] = {
                            "car": counts["car"],
                            "motorcycle": counts["motorcycle"],
                            "bus": counts["bus"],
                            "truck": counts["truck"],
                            "total": counts["total"],
                            "samples": 1,
                        }
                    road_speed.setdefault(rid, []).append(srd.get("avg_speed", 0))
        except Exception:
            pass

        # If still no road_counts at all, try live SUMO totals without type breakdown.
        if not road_counts:
            try:
                import simulation
                if simulation.sim_active:
                    from services.density import compute_density_from_sumo
                    traci_mod = simulation.get_traci()
                    with simulation.sim_lock:
                        sumo_roads = compute_density_from_sumo(traci_mod, simulation.road_mapping)
                        for srd in sumo_roads:
                            rid = srd.get("road_id", "")
                            if not rid:
                                continue
                            vc = srd.get("vehicle_count", 0)
                            road_counts[rid] = {
                                "car": vc, "motorcycle": 0, "bus": 0, "truck": 0,
                                "total": vc, "samples": 1,
                            }
                            road_speed[rid] = [srd.get("avg_speed", 0)]
            except Exception:
                pass

        # Final fallback: use RoadDensity rows from index_calculation_loop
        if not road_counts and road_density_data:
            for rd in road_density_data:
                rid = rd.road_id
                vc = rd.vehicle_count or 0
                road_counts[rid] = {
                    "car": vc, "motorcycle": 0, "bus": 0, "truck": 0,
                    "total": vc, "samples": 1,
                }

        if not road_counts:
            return

        # Upsert hourly counts
        for road_id, counts in road_counts.items():
            normalized_road_id = ensure_road(session, road_id, road_names.get(road_id, road_id))
            if not normalized_road_id:
                continue
            existing = session.query(HourlyVehicleCount).filter(
                and_(
                    HourlyVehicleCount.date == date_str,
                    HourlyVehicleCount.hour == hour_str,
                    HourlyVehicleCount.road_id == normalized_road_id,
                )
            ).first()

            avg_spd = 0.0
            if road_id in road_speed and road_speed[road_id]:
                avg_spd = sum(road_speed[road_id]) / len(road_speed[road_id])

            density_idx = 0.0
            if road_id in road_index and road_index[road_id]:
                density_idx = sum(road_index[road_id]) / len(road_index[road_id])

            if existing:
                setattr(existing, "car", max(int(getattr(existing, "car", 0) or 0), int(counts["car"])))
                setattr(existing, "motorcycle", max(int(getattr(existing, "motorcycle", 0) or 0), int(counts["motorcycle"])))
                setattr(existing, "bus", max(int(getattr(existing, "bus", 0) or 0), int(counts["bus"])))
                setattr(existing, "truck", max(int(getattr(existing, "truck", 0) or 0), int(counts["truck"])))
                setattr(existing, "total", max(int(getattr(existing, "total", 0) or 0), int(counts["total"])))
                if avg_spd > 0:
                    setattr(existing, "avg_speed", float(avg_spd))
                if density_idx > 0:
                    setattr(existing, "density_index", float(density_idx))
            else:
                row = HourlyVehicleCount(
                    date=date_str,
                    hour=hour_str,
                    road_id=normalized_road_id,
                    road_name=road_names.get(normalized_road_id, road_names.get(road_id, normalized_road_id)),
                    car=counts["car"],
                    motorcycle=counts["motorcycle"],
                    bus=counts["bus"],
                    truck=counts["truck"],
                    total=counts["total"],
                    avg_speed=avg_spd,
                    density_index=density_idx,
                    source="yolo",
                )
                session.add(row)

        session.commit()
        total_roads = len(road_counts)
        if total_roads > 0:
            print(f"  📊 Aggregated {total_roads} roads for {date_str} {hour_str}")
    except Exception as e:
        session.rollback()
        raise e
    finally:
        session.close()
