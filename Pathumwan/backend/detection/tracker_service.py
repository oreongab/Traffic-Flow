"""Real-mode detection/tracker loop built on top of RTSP ingest frames."""

from __future__ import annotations

from collections import defaultdict
import math
import time
from datetime import datetime, timezone

import cv2
import numpy as np

from config import Config
from database.connection import get_session
from database.models import TrafficDetection
from database.reference_data import ensure_approach, ensure_junction, ensure_road
from services.mapping import (
    get_camera_approach_map,
    get_camera_calibration_map,
    get_camera_catalog,
    get_camera_zone_map,
)
from services.optical_flow import (
    get_bbox_flow,
    get_camera_scene_flow,
    get_predicted_center,
)
from services.rtsp_ingest import get_latest_frame, set_detect_frame

_TRACKER_STATE: dict[str, dict[str, object]] = {}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_list(value: object) -> list[object]:
    return list(value) if isinstance(value, list) else []


def _as_dict(value: object) -> dict[str, object]:
    return dict(value) if isinstance(value, dict) else {}


def _snapshot_track(track: dict[str, object]) -> dict[str, object]:
    return {
        **track,
        "bbox": _as_list(track.get("bbox")),
        "zone_ids": _as_list(track.get("zone_ids")),
        "zone_types": _as_list(track.get("zone_types")),
        "approach_ids": _as_list(track.get("approach_ids")),
        "line_state": _as_dict(track.get("line_state")),
    }


def get_runtime_approach_metrics(
    camera_id: str | None = None,
    max_age_seconds: float | None = None,
) -> list[dict[str, object]]:
    now = _utcnow()
    stale_limit = float(max_age_seconds if max_age_seconds is not None else Config.STALE_THRESHOLD_SECONDS)
    metrics: list[dict[str, object]] = []

    for current_camera_id, state in _TRACKER_STATE.items():
        if camera_id and str(camera_id) != str(current_camera_id):
            continue
        updated_at = state.get("updated_at")
        if not isinstance(updated_at, datetime):
            continue
        freshness_seconds = max(0.0, (now - updated_at).total_seconds())
        if freshness_seconds > stale_limit:
            continue

        for metric in _as_list(state.get("latest_metrics")):
            if not isinstance(metric, dict):
                continue
            metric_copy = dict(metric)
            metric_copy["freshness_seconds"] = freshness_seconds
            metric_copy["timestamp"] = updated_at
            metrics.append(metric_copy)

    return metrics


def get_runtime_camera_tracks(
    camera_id: str | None = None,
    max_age_seconds: float | None = None,
) -> list[dict[str, object]]:
    now = _utcnow()
    stale_limit = float(max_age_seconds if max_age_seconds is not None else Config.STALE_THRESHOLD_SECONDS)
    tracks: list[dict[str, object]] = []

    for current_camera_id, state in _TRACKER_STATE.items():
        if camera_id and str(camera_id) != str(current_camera_id):
            continue
        updated_at = state.get("updated_at")
        if not isinstance(updated_at, datetime):
            continue
        freshness_seconds = max(0.0, (now - updated_at).total_seconds())
        if freshness_seconds > stale_limit:
            continue

        for track in _as_list(state.get("latest_tracks")):
            if not isinstance(track, dict):
                continue
            track_copy = _snapshot_track(dict(track))
            track_copy["camera_id"] = str(current_camera_id)
            track_copy["freshness_seconds"] = freshness_seconds
            tracks.append(track_copy)

    return tracks


def _decode_frame(frame_bytes: bytes):
    buffer = np.frombuffer(frame_bytes, np.uint8)
    return cv2.imdecode(buffer, cv2.IMREAD_COLOR)


def _normalize_points(raw_points: object) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    if not isinstance(raw_points, list):
        return points
    for item in raw_points:
        if isinstance(item, dict):
            x_value = item.get("x")
            y_value = item.get("y")
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            x_value, y_value = item[0], item[1]
        else:
            continue
        try:
            points.append((float(x_value), float(y_value)))
        except (TypeError, ValueError):
            continue
    return points


def _bbox_center(bbox: list[float]) -> tuple[float, float]:
    return ((bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0)


def _bbox_area(bbox: list[float]) -> float:
    return max(0.0, bbox[2] - bbox[0]) * max(0.0, bbox[3] - bbox[1])


def _polygon_area(points: list[tuple[float, float]]) -> float:
    if len(points) < 3:
        return 0.0
    area = 0.0
    for index, (x_value, y_value) in enumerate(points):
        next_x, next_y = points[(index + 1) % len(points)]
        area += (x_value * next_y) - (next_x * y_value)
    return abs(area) / 2.0


def _point_in_polygon(point: tuple[float, float], polygon: list[tuple[float, float]]) -> bool:
    if len(polygon) < 3:
        return False
    contour = np.array(polygon, dtype=np.float32)
    return bool(cv2.pointPolygonTest(contour, point, False) >= 0)


def _line_side(point: tuple[float, float], line_points: list[tuple[float, float]]) -> float:
    if len(line_points) < 2:
        return 0.0
    (x1, y1), (x2, y2) = line_points[:2]
    return ((point[0] - x1) * (y2 - y1)) - ((point[1] - y1) * (x2 - x1))


def _pixel_distance_to_meters(
    previous_center: tuple[float, float],
    current_center: tuple[float, float],
    calibration: dict[str, object],
) -> float | None:
    previous_local = _project_point_to_local(previous_center, calibration)
    current_local = _project_point_to_local(current_center, calibration)
    if previous_local and current_local:
        return math.hypot(current_local[0] - previous_local[0], current_local[1] - previous_local[1])

    pixels_per_meter = float(calibration.get("pixels_per_meter") or 0.0)
    if pixels_per_meter > 0:
        pixel_distance = math.hypot(current_center[0] - previous_center[0], current_center[1] - previous_center[1])
        return pixel_distance / pixels_per_meter
    return None


def _project_point_to_local(point: tuple[float, float], calibration: dict[str, object]) -> tuple[float, float] | None:
    matrix_value = calibration.get("homography_matrix")
    if isinstance(matrix_value, list) and len(matrix_value) == 3:
        try:
            matrix = np.array(matrix_value, dtype=float)
            pixel = np.array([point[0], point[1], 1.0], dtype=float)
            projected = matrix @ pixel
            if abs(float(projected[2])) > 1e-6:
                return float(projected[0] / projected[2]), float(projected[1] / projected[2])
        except Exception:
            pass

    pixels_per_meter = float(calibration.get("pixels_per_meter") or 0.0)
    image_width = float(calibration.get("image_width") or 0.0)
    image_height = float(calibration.get("image_height") or 0.0)
    if pixels_per_meter > 0 and image_width > 0 and image_height > 0:
        return (
            (point[0] - (image_width / 2.0)) / pixels_per_meter,
            ((image_height / 2.0) - point[1]) / pixels_per_meter,
        )

    return None


def _project_point_to_latlng(
    point: tuple[float, float],
    calibration: dict[str, object],
) -> tuple[float | None, float | None, str | None]:
    anchor_lat = float(calibration.get("anchor_lat") or 0.0)
    anchor_lng = float(calibration.get("anchor_lng") or 0.0)
    if not anchor_lat and not anchor_lng:
        return None, None, None

    local_point = _project_point_to_local(point, calibration)
    if local_point is None:
        return None, None, None

    local_x, local_y = local_point
    bearing_rad = math.radians(float(calibration.get("bearing_deg") or 0.0))
    east_m = (local_x * math.cos(bearing_rad)) - (local_y * math.sin(bearing_rad))
    north_m = (local_x * math.sin(bearing_rad)) + (local_y * math.cos(bearing_rad))

    lat = anchor_lat + (north_m / 111_320.0)
    lng_divisor = 111_320.0 * max(math.cos(math.radians(anchor_lat)), 1e-6)
    lng = anchor_lng + (east_m / lng_divisor)
    source = "homography" if calibration.get("homography_matrix") else "anchor_scale"
    return lat, lng, source


def _match_track(
    tracks: dict[str, dict[str, object]],
    detection_class: str,
    center: tuple[float, float],
    matched_ids: set[str],
    now: datetime,
    camera_id: str = "",
) -> tuple[str | None, dict[str, object] | None]:
    best_track_id: str | None = None
    best_track: dict[str, object] | None = None
    best_distance: float | None = None

    for track_id, track in tracks.items():
        if track_id in matched_ids:
            continue
        if str(track.get("class") or "") != detection_class:
            continue
        last_seen = track.get("last_seen_at")
        if not isinstance(last_seen, datetime):
            continue
        if (now - last_seen).total_seconds() > Config.LIVE_STATE_MAX_TRACK_AGE_SECONDS:
            continue
        previous_bbox = track.get("bbox") or [0.0, 0.0, 0.0, 0.0]
        previous_center = track.get("center") or center
        if not isinstance(previous_center, tuple):
            continue
        # Goal 3 (Optical Flow): predict where the previous center should be NOW
        # using accumulated flow vector. Falls back to last-seen center if flow unknown.
        predicted_center = previous_center
        if camera_id:
            dt = max(0.0, (now - last_seen).total_seconds())
            predicted = get_predicted_center(camera_id, previous_center, dt)
            if predicted is not None:
                predicted_center = predicted
        distance = math.hypot(center[0] - predicted_center[0], center[1] - predicted_center[1])
        bbox_width = max(0.0, float(previous_bbox[2]) - float(previous_bbox[0]))
        bbox_height = max(0.0, float(previous_bbox[3]) - float(previous_bbox[1]))
        threshold = max(40.0, max(bbox_width, bbox_height) * 1.5)
        if distance > threshold:
            continue
        if best_distance is None or distance < best_distance:
            best_track_id = track_id
            best_track = track
            best_distance = distance

    return best_track_id, best_track


def _get_matching_zones(
    center: tuple[float, float],
    bbox: list[float],
    camera_zones: list[dict[str, object]],
) -> list[dict[str, object]]:
    matched: list[dict[str, object]] = []
    for zone in camera_zones:
        polygon_points = _normalize_points(zone.get("polygon_points"))
        line_points = _normalize_points(zone.get("line_points"))
        if polygon_points and _point_in_polygon(center, polygon_points):
            matched.append(zone)
            continue
        if len(line_points) >= 2:
            bbox_midpoint = (center[0], bbox[3])
            line_distance = abs(_line_side(bbox_midpoint, line_points))
            if line_distance <= 800.0:
                matched.append(zone)
    return matched


def _prune_old_crossings(crossings: dict[str, list[datetime]], now: datetime) -> None:
    for approach_id, timestamps in list(crossings.items()):
        crossings[approach_id] = [
            ts for ts in timestamps if (now - ts).total_seconds() <= Config.COUNTING_WINDOW_SECONDS
        ]
        if not crossings[approach_id]:
            crossings.pop(approach_id, None)


def _prepare_tracks(
    camera_id: str,
    detections: list[dict[str, object]],
    now: datetime,
    frame_shape: tuple[int, int, int],
    camera_meta: dict[str, object],
    calibration: dict[str, object],
    camera_zones: list[dict[str, object]],
    fallback_approaches: list[dict[str, str]],
) -> list[dict[str, object]]:
    camera_state = _TRACKER_STATE.setdefault(
        camera_id,
        {"next_track_index": 1, "tracks": {}, "crossings": defaultdict(list)},
    )
    tracks = camera_state["tracks"]
    if not isinstance(tracks, dict):
        tracks = {}
        camera_state["tracks"] = tracks

    stale_cutoff = max(Config.LIVE_STATE_MAX_TRACK_AGE_SECONDS, float(Config.DETECTION_INTERVAL) * 2.0)
    for track_id, track in list(tracks.items()):
        last_seen = track.get("last_seen_at")
        if isinstance(last_seen, datetime) and (now - last_seen).total_seconds() > stale_cutoff:
            tracks.pop(track_id, None)

    crossings = camera_state.get("crossings")
    if not isinstance(crossings, defaultdict):
        crossings = defaultdict(list, dict(crossings or {}))
        camera_state["crossings"] = crossings
    _prune_old_crossings(crossings, now)

    matched_ids: set[str] = set()
    prepared_tracks: list[dict[str, object]] = []

    for detection in detections:
        confidence = float(detection.get("confidence") or 0.0)
        if confidence < Config.MIN_TRACK_CONFIDENCE:
            continue
        bbox = [float(value) for value in list(detection.get("bbox") or [0, 0, 0, 0])[:4]]
        if len(bbox) != 4:
            continue
        vehicle_class = str(detection.get("class") or "car")
        center = _bbox_center(bbox)
        track_id, previous_track = _match_track(tracks, vehicle_class, center, matched_ids, now, camera_id)
        if track_id is None:
            track_id = f"trk-{int(camera_state['next_track_index'])}"
            camera_state["next_track_index"] = int(camera_state["next_track_index"]) + 1
            previous_track = None
        matched_ids.add(track_id)

        speed_kmh = 0.0
        lk_speed_kmh: float | None = None
        lk_n_points = 0
        if previous_track is not None:
            previous_center = previous_track.get("center") or center
            previous_last_seen = previous_track.get("last_seen_at")
            if isinstance(previous_last_seen, datetime) and isinstance(previous_center, tuple):
                elapsed = max(0.001, (now - previous_last_seen).total_seconds())
                distance_m = _pixel_distance_to_meters(previous_center, center, calibration)
                if distance_m is not None:
                    speed_kmh = max(0.0, distance_m / elapsed * 3.6)
                    if previous_track.get("speed_kmh"):
                        speed_kmh = (0.6 * float(previous_track.get("speed_kmh") or 0.0)) + (0.4 * speed_kmh)

        # Goal 2 (Optical Flow): blend LK velocity into bbox-center speed estimate.
        # LK runs at 0.5s gap (motion 3-15 px) → far less noisy than 5s centroid diff.
        flow = get_bbox_flow(camera_id, bbox)
        if flow and flow["n_points"] >= 3 and flow["dt_s"] > 0:
            flow_target = (
                center[0] + float(flow["vx_px"]),
                center[1] + float(flow["vy_px"]),
            )
            flow_distance_m = _pixel_distance_to_meters(center, flow_target, calibration)
            if flow_distance_m is not None:
                lk_speed_kmh = max(0.0, (flow_distance_m / float(flow["dt_s"])) * 3.6)
                lk_n_points = int(flow["n_points"])
                if speed_kmh > 0:
                    speed_kmh = (0.7 * lk_speed_kmh) + (0.3 * speed_kmh)
                else:
                    speed_kmh = lk_speed_kmh

        lat_value, lng_value, geo_source = _project_point_to_latlng(center, calibration)
        matched_zones = _get_matching_zones(center, bbox, camera_zones)
        approach_ids = sorted({str(zone.get("approach_id") or "") for zone in matched_zones if str(zone.get("approach_id") or "")})
        if not approach_ids and fallback_approaches:
            approach_ids = [str(fallback_approaches[0].get("approach_id") or camera_id)]

        line_state = dict((previous_track or {}).get("line_state") or {})
        for zone in matched_zones:
            line_points = _normalize_points(zone.get("line_points"))
            if len(line_points) < 2:
                continue
            approach_id = str(zone.get("approach_id") or "")
            if not approach_id:
                continue
            side = _line_side(center, line_points)
            zone_id = str(zone.get("zone_id") or approach_id)
            prior_line_state = line_state.get(zone_id, {})
            previous_side = prior_line_state.get("side")
            last_crossed_at = prior_line_state.get("last_crossed_at")
            crossed = (
                isinstance(previous_side, (int, float))
                and previous_side != 0
                and side != 0
                and (previous_side * side) < 0
            )
            if crossed and (
                not isinstance(last_crossed_at, datetime)
                or (now - last_crossed_at).total_seconds() >= 1.0
            ):
                crossings[approach_id].append(now)
                line_state[zone_id] = {"side": side, "last_crossed_at": now}
            else:
                line_state[zone_id] = {
                    "side": side,
                    "last_crossed_at": last_crossed_at if isinstance(last_crossed_at, datetime) else None,
                }

        prepared_track = {
            "track_id": track_id,
            "class": vehicle_class,
            "confidence": confidence,
            "bbox": bbox,
            "center": center,
            "speed_kmh": speed_kmh,
            "lk_speed_kmh": lk_speed_kmh,
            "lk_n_points": lk_n_points,
            "lat": lat_value,
            "lng": lng_value,
            "geo_source": geo_source,
            "zone_ids": [str(zone.get("zone_id") or "") for zone in matched_zones],
            "zone_types": [str(zone.get("zone_type") or "presence") for zone in matched_zones],
            "approach_ids": approach_ids,
            "road_id": str(next((zone.get("road_id") for zone in matched_zones if zone.get("road_id")), "") or camera_meta.get("road_id") or ""),
            "junction_id": str(next((zone.get("junction_id") for zone in matched_zones if zone.get("junction_id")), "") or camera_meta.get("junction_id") or camera_id),
            "line_state": line_state,
            "first_seen_at": (previous_track or {}).get("first_seen_at") or now,
            "last_seen_at": now,
            "frame_shape": frame_shape,
        }
        tracks[track_id] = prepared_track
        prepared_tracks.append(prepared_track)

    return prepared_tracks


def _build_live_metrics(
    camera_id: str,
    tracks: list[dict[str, object]],
    counts: dict[str, int],
    confidence_avg: float,
    camera_meta: dict[str, object],
    camera_zones: list[dict[str, object]],
    fallback_approaches: list[dict[str, str]],
    frame_shape: tuple[int, int, int],
    now: datetime,
) -> list[dict[str, object]]:
    camera_state = _TRACKER_STATE.setdefault(camera_id, {"crossings": defaultdict(list)})
    crossings = camera_state.get("crossings")
    if not isinstance(crossings, defaultdict):
        crossings = defaultdict(list, dict(crossings or {}))
        camera_state["crossings"] = crossings
    _prune_old_crossings(crossings, now)

    approaches: dict[str, dict[str, object]] = {}
    for approach in fallback_approaches:
        approach_id = str(approach.get("approach_id") or "")
        if not approach_id:
            continue
        approaches[approach_id] = {
            "junction_id": str(approach.get("junction_id") or camera_meta.get("junction_id") or camera_id),
            "approach_id": approach_id,
            "road_id": str(approach.get("road_id") or camera_meta.get("road_id") or ""),
            "camera_id": camera_id,
        }
    for zone in camera_zones:
        approach_id = str(zone.get("approach_id") or "")
        if not approach_id:
            continue
        approaches.setdefault(
            approach_id,
            {
                "junction_id": str(zone.get("junction_id") or camera_meta.get("junction_id") or camera_id),
                "approach_id": approach_id,
                "road_id": str(zone.get("road_id") or camera_meta.get("road_id") or ""),
                "camera_id": camera_id,
            },
        )
    if not approaches:
        approaches[camera_id] = {
            "junction_id": str(camera_meta.get("junction_id") or camera_id),
            "approach_id": camera_id,
            "road_id": str(camera_meta.get("road_id") or ""),
            "camera_id": camera_id,
        }

    frame_area = float(max(1, frame_shape[0] * frame_shape[1]))
    metrics: list[dict[str, object]] = []
    for approach_id, base in approaches.items():
        relevant_tracks = [track for track in tracks if approach_id in list(track.get("approach_ids") or [])]
        if not relevant_tracks and len(approaches) == 1:
            relevant_tracks = list(tracks)
        relevant_zones = [zone for zone in camera_zones if str(zone.get("approach_id") or "") == approach_id]

        presence_zone_ids = {
            str(zone.get("zone_id") or "")
            for zone in relevant_zones
            if str(zone.get("zone_type") or "presence") in {"presence", "queue", "stopline"}
        }
        queue_zone_ids = {
            str(zone.get("zone_id") or "")
            for zone in relevant_zones
            if str(zone.get("zone_type") or "presence") in {"queue", "stopline"}
        }
        presence_tracks = [
            track for track in relevant_tracks if not presence_zone_ids or any(zone_id in presence_zone_ids for zone_id in list(track.get("zone_ids") or []))
        ]
        queue_tracks = [
            track for track in relevant_tracks if any(zone_id in queue_zone_ids for zone_id in list(track.get("zone_ids") or []))
        ]
        # Goal 4 (Optical Flow): augment stopped detection. A track counts as stopped if
        # speed < threshold OR the LK flow inside its bbox is below the queue magnitude
        # threshold (catches the case where speed estimate is noisy but bbox is genuinely stationary).
        stopped_tracks: list[dict[str, object]] = []
        for track in relevant_tracks:
            if float(track.get("speed_kmh") or 0.0) <= Config.STOPPED_SPEED_THRESHOLD_KMH:
                stopped_tracks.append(track)
                continue
            track_flow = get_bbox_flow(camera_id, list(track.get("bbox") or [0, 0, 0, 0]))
            if track_flow and track_flow["magnitude_px"] < Config.OPTICAL_FLOW_QUEUE_MAGNITUDE_THRESHOLD_PX:
                stopped_tracks.append(track)
        queue_track_ids = {str(track.get("track_id") or "") for track in queue_tracks}
        for track in stopped_tracks:
            queue_track_ids.add(str(track.get("track_id") or ""))

        bbox_area_sum = sum(_bbox_area(list(track.get("bbox") or [0, 0, 0, 0])) for track in presence_tracks)
        zone_area_sum = sum(
            _polygon_area(_normalize_points(zone.get("polygon_points")))
            for zone in relevant_zones
            if str(zone.get("zone_type") or "presence") in {"presence", "queue", "stopline"}
        )
        occupancy_ratio = min(1.0, bbox_area_sum / max(zone_area_sum, frame_area if zone_area_sum <= 0 else zone_area_sum))

        speed_values = [float(track.get("speed_kmh") or 0.0) for track in relevant_tracks if float(track.get("speed_kmh") or 0.0) > 0]
        class_counts: dict[str, int] = defaultdict(int)
        for track in relevant_tracks:
            class_counts[str(track.get("class") or "car")] += 1

        flow_events = crossings.get(approach_id, [])
        flow_veh_per_min = (len(flow_events) * 60.0) / max(1.0, float(Config.COUNTING_WINDOW_SECONDS))

        metrics.append(
            {
                **base,
                "vehicle_count": len(presence_tracks),
                "queue_length": len(queue_track_ids),
                "occupancy_ratio": occupancy_ratio,
                "avg_speed_kmh": (sum(speed_values) / len(speed_values)) if speed_values else 0.0,
                "flow_veh_per_min": flow_veh_per_min,
                "stopped_vehicle_count": len(stopped_tracks),
                "freshness_seconds": 0.0,
                "source": "real",
                "extra_metadata": {
                    "confidence_avg": float(confidence_avg),
                    "tracking_ready": True,
                    "partial_observation": False,
                    "has_data": bool(relevant_tracks),
                    "track_count": len(relevant_tracks),
                    "zone_count": len(relevant_zones),
                    "dominant_classes": sorted(class_counts.items(), key=lambda item: (-item[1], item[0])),
                    "geo_calibrated_tracks": sum(1 for track in relevant_tracks if track.get("lat") is not None and track.get("lng") is not None),
                    "instant_count": len(presence_tracks),
                },
            }
        )

    if not tracks and not camera_zones and len(metrics) == 1:
        metrics[0]["vehicle_count"] = int(counts.get("total", 0) or 0)
        metrics[0]["flow_veh_per_min"] = float(int(counts.get("total", 0) or 0))
        metrics[0]["extra_metadata"] = {
            "confidence_avg": float(confidence_avg),
            "tracking_ready": False,
            "partial_observation": True,
            "has_data": bool(counts.get("total", 0)),
            "track_count": 0,
            "zone_count": 0,
            "instant_count": int(counts.get("total", 0) or 0),
        }

    # Goal 1 (Optical Flow): YOLO blindness fallback. When YOLO returns no tracks but the
    # scene clearly has motion (heavy congestion, low light, rain), inject flow_active=True
    # and a lower-bound flow_veh_per_min derived from active-feature density so downstream
    # consumers don't read "vehicle_count=0" as "actually empty".
    if Config.OPTICAL_FLOW_BLINDNESS_FALLBACK_ENABLED and not tracks:
        scene = get_camera_scene_flow(camera_id)
        if scene and float(scene.get("magnitude") or 0.0) >= Config.OPTICAL_FLOW_SCENE_ACTIVE_THRESHOLD_PX:
            for metric in metrics:
                meta = dict(metric.get("extra_metadata") or {})
                meta["partial_observation"] = True
                meta["flow_active"] = True
                meta["scene_flow_magnitude"] = float(scene.get("magnitude") or 0.0)
                meta["scene_active_ratio"] = float(scene.get("active_ratio") or 0.0)
                metric["extra_metadata"] = meta
                metric["flow_veh_per_min"] = max(
                    float(metric.get("flow_veh_per_min") or 0.0),
                    round(float(scene.get("active_ratio") or 0.0) * 6.0, 2),
                )

    return metrics


def _save_camera_runtime_state(
    camera_id: str,
    counts: dict[str, int],
    confidence_avg: float,
    tracks: list[dict[str, object]],
    metrics: list[dict[str, object]],
    *,
    save_detection: bool,
) -> None:
    """Save detection counts to traffic_detections table.
    
    Track data is kept in-memory only (not persisted per-frame to DB).
    LiveVehicleTrack and LiveApproachMetric tables have been removed.
    """
    if not save_detection:
        return
    session = get_session()
    try:
        session.add(
            TrafficDetection(
                camera_id=camera_id,
                vehicle_counts=counts,
                confidence_avg=confidence_avg,
            )
        )
        session.commit()
    except Exception:
        session.rollback()
    finally:
        session.close()


def start_tracker_service_loop() -> None:
    """Run YOLO on RTSP frames and persist conservative real-mode detections."""
    if Config.CAMERA_BACKEND != "rtsp" and Config.SYSTEM_MODE != "real":
        print("✓ Tracker loop idle — real camera backend not enabled")
    else:
        print(f"✓ Tracker loop started (interval={Config.DETECTION_INTERVAL}s)")

    detector = None
    try:
        from detection.yolo_detector import get_shared_detector

        detector = get_shared_detector()
    except Exception:
        detector = None

    if detector is None:
        print("  ⚠ Real tracker loop running without YOLO model; no detections will be produced")

    last_saved_counts: dict[str, dict[str, int]] = {}

    while True:
        if Config.CAMERA_BACKEND != "rtsp" and Config.SYSTEM_MODE != "real":
            time.sleep(2)
            continue

        if detector is None:
            time.sleep(max(2, Config.DETECTION_INTERVAL))
            continue

        for camera_id in sorted(get_camera_catalog().keys()):
            frame_bytes = get_latest_frame(camera_id, detect=False)
            if not frame_bytes:
                continue
            try:
                frame = _decode_frame(frame_bytes)
                if frame is None:
                    continue
                annotated_bytes, detections = detector.detect_and_annotate(frame_bytes)
                counts = {"car": 0, "motorcycle": 0, "bus": 0, "truck": 0}
                confidence_values = []
                for detection in detections:
                    vehicle_class = str(detection.get("class") or "")
                    if vehicle_class in counts:
                        counts[vehicle_class] += 1
                    confidence_values.append(float(detection.get("confidence") or 0.0))
                normalized = {
                    "car": int(counts.get("car", 0) or 0),
                    "motorcycle": int(counts.get("motorcycle", 0) or 0),
                    "bus": int(counts.get("bus", 0) or 0),
                    "truck": int(counts.get("truck", 0) or 0),
                }
                normalized["total"] = int(counts.get("total", sum(normalized.values())) or 0)

                if annotated_bytes:
                    set_detect_frame(camera_id, annotated_bytes)

                confidence_avg = sum(confidence_values) / len(confidence_values) if confidence_values else 0.0
                camera_catalog = get_camera_catalog()
                approach_map = get_camera_approach_map()
                zone_map = get_camera_zone_map()
                calibration_map = get_camera_calibration_map()
                tracks = _prepare_tracks(
                    camera_id,
                    detections,
                    _utcnow(),
                    frame.shape,
                    camera_catalog.get(camera_id, {}),
                    calibration_map.get(camera_id, {}),
                    zone_map.get(camera_id, []),
                    approach_map.get(camera_id, []),
                )
                metrics = _build_live_metrics(
                    camera_id,
                    tracks,
                    normalized,
                    confidence_avg,
                    camera_catalog.get(camera_id, {}),
                    zone_map.get(camera_id, []),
                    approach_map.get(camera_id, []),
                    frame.shape,
                    _utcnow(),
                )
                camera_state = _TRACKER_STATE.setdefault(
                    camera_id,
                    {"next_track_index": 1, "tracks": {}, "crossings": defaultdict(list)},
                )
                camera_state["latest_counts"] = dict(normalized)
                camera_state["latest_tracks"] = [_snapshot_track(track) for track in tracks]
                camera_state["latest_metrics"] = [dict(metric) for metric in metrics]
                camera_state["confidence_avg"] = float(confidence_avg)
                camera_state["updated_at"] = _utcnow()
                _save_camera_runtime_state(
                    camera_id,
                    normalized,
                    confidence_avg,
                    tracks,
                    metrics,
                    save_detection=last_saved_counts.get(camera_id) != normalized,
                )
                last_saved_counts[camera_id] = normalized
            except Exception as exc:
                print(f"Tracker error [{camera_id}]: {exc}")
                continue

        time.sleep(max(1, Config.DETECTION_INTERVAL))