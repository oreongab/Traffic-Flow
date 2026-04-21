"""Live state aggregation for source-agnostic sim/real routes and AI."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import math

from sqlalchemy import func

from config import Config
from database.connection import get_session
from database.models import Camera, SignalState, TrafficDetection
from services.mapping import get_camera_catalog, get_junction_approach_map


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _vehicle_color(vehicle_class: str) -> str:
    return {
        "car": "#22c55e",
        "motorcycle": "#3b82f6",
        "bus": "#eab308",
        "truck": "#f97316",
    }.get(vehicle_class, "#94a3b8")


def _distance_and_angle(camera_lat: float, camera_lng: float, lat: float, lng: float) -> tuple[float, float]:
    lat_scale = 111_320.0
    lng_scale = 111_320.0 * max(math.cos(math.radians(camera_lat)), 1e-6)
    north_m = (lat - camera_lat) * lat_scale
    east_m = (lng - camera_lng) * lng_scale
    distance = math.hypot(east_m, north_m)
    angle = math.degrees(math.atan2(north_m, east_m))
    return distance, angle


def get_latest_signal_states() -> dict[str, dict[str, object]]:
    """Return the newest signal state per junction."""
    session = get_session()
    try:
        sub = session.query(
            SignalState.junction_id,
            func.max(SignalState.timestamp).label("max_ts"),
        ).group_by(SignalState.junction_id).subquery()

        rows = session.query(SignalState).join(
            sub,
            (SignalState.junction_id == sub.c.junction_id)
            & (SignalState.timestamp == sub.c.max_ts),
        ).all()

        result: dict[str, dict[str, object]] = {}
        for row in rows:
            junction_id = str(row.junction_id or "")
            if not junction_id:
                continue
            result[junction_id] = {
                "junction_id": junction_id,
                "current_phase": int(row.current_phase or 0),
                "phase_count": int(row.phase_count or 0),
                "phase_duration": float(row.phase_duration or 0.0),
                "next_switch_eta": float(row.next_switch_eta or 0.0),
                "timestamp": row.timestamp,
                "source": str(row.source or "sim"),
                "raw_state": row.raw_state or {},
            }
        return result
    finally:
        session.close()


def get_latest_approach_metrics(max_age_seconds: float | None = None) -> list[dict[str, object]]:
    """Return approach metrics derived from traffic_detections (replaces LiveApproachMetric)."""
    try:
        from detection.tracker_service import get_runtime_approach_metrics

        runtime_metrics = get_runtime_approach_metrics(max_age_seconds=max_age_seconds)
        if runtime_metrics:
            return runtime_metrics
    except Exception:
        pass

    # With LiveApproachMetric removed, we derive per-approach data from
    # traffic_detections grouped by camera -> road.  This is a simplified
    # version; approach-level granularity requires zone configuration which
    # is now in Camera.zones JSON.
    session = get_session()
    try:
        camera_catalog = get_camera_catalog()
        # Get latest detection per camera
        sub = session.query(
            TrafficDetection.camera_id,
            func.max(TrafficDetection.timestamp).label("max_ts"),
        ).group_by(TrafficDetection.camera_id).subquery()

        detection_rows = session.query(TrafficDetection).join(
            sub,
            (TrafficDetection.camera_id == sub.c.camera_id)
            & (TrafficDetection.timestamp == sub.c.max_ts),
        ).all()

        now = _utcnow()
        stale_limit = float(max_age_seconds if max_age_seconds is not None else Config.STALE_THRESHOLD_SECONDS)
        result: list[dict[str, object]] = []
        for det_row in detection_rows:
            camera_id = str(det_row.camera_id or "")
            if not camera_id:
                continue
            meta = camera_catalog.get(camera_id, {})
            age_seconds = max(0.0, (now - det_row.timestamp).total_seconds()) if det_row.timestamp else stale_limit
            if age_seconds > stale_limit:
                continue
            counts = det_row.vehicle_counts or {}
            total = int(counts.get("total", 0) or 0)

            result.append(
                {
                    "junction_id": str(meta.get("junction_id") or camera_id),
                    "approach_id": camera_id,  # one approach per camera when no zone config
                    "road_id": str(meta.get("road_id") or ""),
                    "camera_id": camera_id,
                    "vehicle_count": total,
                    "queue_length": 0,
                    "occupancy_ratio": 0.0,
                    "avg_speed_kmh": 0.0,
                    "flow_veh_per_min": 0.0,
                    "stopped_vehicle_count": 0,
                    "freshness_seconds": float(age_seconds),
                    "timestamp": det_row.timestamp,
                    "source": "detection-db",
                    "metadata": {"counts": counts},
                }
            )
        return result
    finally:
        session.close()


def get_live_vehicle_map_data(max_age_seconds: float | None = None) -> list[dict[str, object]]:
    """Return latest live track data suitable for map endpoints.

    With LiveVehicleTrack removed, this returns an empty list.
    In SIM mode, vehicle positions come from SUMO TraCI (routes/traffic.py).
    In REAL mode, positions would come from in-memory tracker state.
    """
    try:
        from detection.tracker_service import get_runtime_camera_tracks

        return get_runtime_camera_tracks(max_age_seconds=max_age_seconds)
    except Exception:
        return []


def get_latest_camera_vehicle_tracks(
    camera_id: str | None = None,
    max_age_seconds: float | None = None,
) -> list[dict[str, object]]:
    """Return latest per-camera track state for CCTV endpoints.

    With LiveVehicleTrack removed, this returns an empty list.
    Tracker state is now in-memory only (detection/tracker_service.py).
    """
    try:
        from detection.tracker_service import get_runtime_camera_tracks

        return get_runtime_camera_tracks(camera_id=camera_id, max_age_seconds=max_age_seconds)
    except Exception:
        return []


def get_latest_camera_state(max_age_seconds: float | None = None) -> list[dict[str, object]]:
    """Return consolidated camera state for live routes and monitoring."""
    session = get_session()
    try:
        camera_catalog = get_camera_catalog()
        approach_metrics = get_latest_approach_metrics(max_age_seconds=max_age_seconds)

        latest_detection_subquery = session.query(
            TrafficDetection.camera_id,
            func.max(TrafficDetection.timestamp).label("max_ts"),
        ).group_by(TrafficDetection.camera_id).subquery()

        detection_rows = session.query(TrafficDetection).join(
            latest_detection_subquery,
            (TrafficDetection.camera_id == latest_detection_subquery.c.camera_id)
            & (TrafficDetection.timestamp == latest_detection_subquery.c.max_ts),
        ).all()

        camera_rows = session.query(Camera).filter(Camera.status == "active").all()

        detections_by_camera: dict[str, TrafficDetection] = {
            str(row.camera_id or ""): row for row in detection_rows if str(row.camera_id or "")
        }
        cameras_by_id: dict[str, Camera] = {
            str(row.camera_id or ""): row for row in camera_rows if str(row.camera_id or "")
        }

        metrics_by_camera: dict[str, list[dict[str, object]]] = defaultdict(list)
        for metric in approach_metrics:
            camera_key = str(metric.get("camera_id") or "")
            if camera_key:
                metrics_by_camera[camera_key].append(metric)

        result: list[dict[str, object]] = []
        for camera_id, meta in camera_catalog.items():
            cam_row = cameras_by_id.get(camera_id)
            detection = detections_by_camera.get(camera_id)
            camera_metrics = metrics_by_camera.get(camera_id, [])

            metric_count = sum(int(metric.get("vehicle_count") or 0) for metric in camera_metrics)
            metric_timestamp = max(
                (metric.get("timestamp") for metric in camera_metrics if metric.get("timestamp") is not None),
                default=None,
            )

            raw_counts = (detection.vehicle_counts or {}) if detection else {}
            counts = {
                "car": int(raw_counts.get("car", 0) or 0),
                "motorcycle": int(raw_counts.get("motorcycle", 0) or 0),
                "bus": int(raw_counts.get("bus", 0) or 0),
                "truck": int(raw_counts.get("truck", 0) or 0),
            }
            counts["total"] = int(raw_counts.get("total", metric_count or sum(counts.values())) or 0)

            timestamps = [
                value
                for value in [
                    getattr(detection, "timestamp", None),
                    metric_timestamp,
                    getattr(cam_row, "last_frame_at", None) if cam_row else None,
                ]
                if value is not None
            ]
            latest_timestamp = max(timestamps) if timestamps else None
            freshness_seconds = (
                max(0.0, (_utcnow() - latest_timestamp).total_seconds()) if latest_timestamp else Config.STALE_THRESHOLD_SECONDS
            )

            result.append(
                {
                    "camera_id": camera_id,
                    "name": str(meta.get("name") or camera_id),
                    "road_id": str(meta.get("road_id") or ""),
                    "junction_id": str(meta.get("junction_id") or ""),
                    "lat": float(meta.get("lat") or 0.0),
                    "lng": float(meta.get("lng") or 0.0),
                    "counts": counts,
                    "track_count": 0,
                    "stream_status": str(getattr(cam_row, "stream_status", "offline") or "offline") if cam_row else "offline",
                    "last_frame_at": getattr(cam_row, "last_frame_at", None) if cam_row else None,
                    "timestamp": latest_timestamp,
                    "freshness_seconds": freshness_seconds,
                    "source": "detection-db",
                }
            )

        return sorted(result, key=lambda item: str(item.get("name") or item.get("camera_id") or ""))
    finally:
        session.close()


def get_latest_junction_state() -> list[dict[str, object]]:
    """Return aggregated junction state combining signals, approaches, and cameras."""
    signal_states = get_latest_signal_states()
    approach_metrics = get_latest_approach_metrics()
    camera_catalog = get_camera_catalog()
    junction_approaches = get_junction_approach_map()

    by_junction: dict[str, list[dict[str, object]]] = defaultdict(list)
    for metric in approach_metrics:
        junction_id = str(metric.get("junction_id") or "")
        if junction_id:
            by_junction[junction_id].append(metric)

    junction_ids = sorted(set(signal_states.keys()) | set(by_junction.keys()) | set(junction_approaches.keys()))
    result: list[dict[str, object]] = []
    for junction_id in junction_ids:
        approaches = by_junction.get(junction_id, [])
        signal = signal_states.get(junction_id, {})
        configured_approaches = junction_approaches.get(junction_id, [])

        if not approaches:
            for item in configured_approaches:
                approaches.append(
                    {
                        "junction_id": junction_id,
                        "approach_id": str(item.get("approach_id") or ""),
                        "road_id": str(item.get("road_id") or ""),
                        "camera_id": str(item.get("camera_id") or ""),
                        "vehicle_count": 0,
                        "queue_length": 0,
                        "occupancy_ratio": 0.0,
                        "avg_speed_kmh": 0.0,
                        "flow_veh_per_min": 0.0,
                        "stopped_vehicle_count": 0,
                        "freshness_seconds": Config.STALE_THRESHOLD_SECONDS,
                        "timestamp": None,
                        "source": Config.SYSTEM_MODE,
                        "metadata": {"has_data": False},
                    }
                )

        total_vehicle_count = sum(int(item.get("vehicle_count") or 0) for item in approaches)
        total_queue_length = sum(int(item.get("queue_length") or 0) for item in approaches)
        avg_speed_values = [float(item.get("avg_speed_kmh") or 0.0) for item in approaches if float(item.get("avg_speed_kmh") or 0.0) > 0]
        freshness_values = [float(item.get("freshness_seconds") or Config.STALE_THRESHOLD_SECONDS) for item in approaches]
        cameras = sorted(
            {
                str(item.get("camera_id") or "")
                for item in approaches
                if str(item.get("camera_id") or "") in camera_catalog
            }
        )

        result.append(
            {
                "junction_id": junction_id,
                "current_phase": int(signal.get("current_phase") or 0),
                "phase_count": int(signal.get("phase_count") or 0),
                "phase_duration": float(signal.get("phase_duration") or 0.0),
                "next_switch_eta": float(signal.get("next_switch_eta") or 0.0),
                "source": str(signal.get("source") or Config.SYSTEM_MODE),
                "camera_ids": cameras,
                "approaches": approaches,
                "total_vehicle_count": total_vehicle_count,
                "total_queue_length": total_queue_length,
                "avg_speed_kmh": sum(avg_speed_values) / len(avg_speed_values) if avg_speed_values else 0.0,
                "data_freshness_seconds": max(freshness_values) if freshness_values else Config.STALE_THRESHOLD_SECONDS,
                "controller_status": str(signal.get("source") or "unknown"),
            }
        )
    return result


def get_latest_road_state() -> list[dict[str, object]]:
    """Aggregate latest approach metrics up to road level."""
    approach_metrics = get_latest_approach_metrics()
    road_map: dict[str, dict[str, object]] = {}
    for metric in approach_metrics:
        road_id = str(metric.get("road_id") or "")
        if not road_id:
            continue
        if road_id not in road_map:
            road_map[road_id] = {
                "road_id": road_id,
                "vehicle_count": 0,
                "queue_length": 0,
                "occupancy_ratio": 0.0,
                "avg_speed_kmh": 0.0,
                "flow_veh_per_min": 0.0,
                "stopped_vehicle_count": 0,
                "freshness_seconds": 0.0,
                "sample_count": 0,
            }
        row = road_map[road_id]
        row["vehicle_count"] += int(metric.get("vehicle_count") or 0)
        row["queue_length"] += int(metric.get("queue_length") or 0)
        row["occupancy_ratio"] += float(metric.get("occupancy_ratio") or 0.0)
        row["avg_speed_kmh"] += float(metric.get("avg_speed_kmh") or 0.0)
        row["flow_veh_per_min"] += float(metric.get("flow_veh_per_min") or 0.0)
        row["stopped_vehicle_count"] += int(metric.get("stopped_vehicle_count") or 0)
        row["freshness_seconds"] = max(float(row.get("freshness_seconds") or 0.0), float(metric.get("freshness_seconds") or 0.0))
        row["sample_count"] += 1

    result: list[dict[str, object]] = []
    for row in road_map.values():
        sample_count = max(1, int(row.pop("sample_count", 1)))
        row["occupancy_ratio"] = float(row["occupancy_ratio"]) / sample_count
        row["avg_speed_kmh"] = float(row["avg_speed_kmh"]) / sample_count
        result.append(row)
    return sorted(result, key=lambda item: str(item.get("road_id") or ""))