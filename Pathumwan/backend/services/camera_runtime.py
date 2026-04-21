"""Admin/runtime helpers for camera calibration and zone configuration.

Updated: uses Camera.calibration_data (JSON) and Camera.zones (JSON)
instead of separate CameraCalibration / CameraZone tables.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any

from services.camera_sync import repair_camera_inventory_if_needed
from database.connection import get_session
from database.models import Camera
from database.reference_data import canonical_junction_id, ensure_approach, ensure_junction, ensure_road

VALID_ZONE_TYPES = {"presence", "queue", "flow", "stopline"}


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


def _isoformat_or_none(value: Any) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


def _ensure_camera_inventory() -> None:
    try:
        repair_camera_inventory_if_needed()
    except Exception:
        pass


def _camera_road_id(camera: Camera) -> str:
    return str(getattr(camera, "road_id", "") or getattr(camera, "road", "") or "")


def _camera_junction_id(camera: Camera) -> str:
    return str(getattr(camera, "junction_id", "") or getattr(camera, "sumo_tls_id", "") or getattr(camera, "junction", "") or "")


def _normalize_point_list(raw_points: object, *, minimum_points: int) -> list[list[float]]:
    if not isinstance(raw_points, list):
        return []

    normalized: list[list[float]] = []
    for item in raw_points:
        x_value = None
        y_value = None
        if isinstance(item, dict):
            x_value = item.get("x")
            y_value = item.get("y")
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            x_value, y_value = item[0], item[1]
        if x_value is None or y_value is None:
            continue
        try:
            normalized.append([float(x_value), float(y_value)])
        except (TypeError, ValueError):
            continue

    if len(normalized) < minimum_points:
        return []
    return normalized


def _serialize_calibration(cal_data: dict[str, Any] | None) -> dict[str, object] | None:
    if not isinstance(cal_data, dict):
        return None
    return {
        "image_width": _as_int(cal_data.get("image_width", 0)),
        "image_height": _as_int(cal_data.get("image_height", 0)),
        "homography_matrix": cal_data.get("homography_matrix", []) if isinstance(cal_data.get("homography_matrix", []), list) else [],
        "anchor_lat": _as_float(cal_data.get("anchor_lat", 0.0)),
        "anchor_lng": _as_float(cal_data.get("anchor_lng", 0.0)),
        "bearing_deg": _as_float(cal_data.get("bearing_deg", 0.0)),
        "pixels_per_meter": _as_float(cal_data.get("pixels_per_meter", 0.0)),
    }


def _serialize_zone(zone: dict[str, Any]) -> dict[str, object]:
    return {
        "zone_id": str(zone.get("zone_id", "") or ""),
        "junction_id": str(zone.get("junction_id", "") or ""),
        "approach_id": str(zone.get("approach_id", "") or ""),
        "road_id": str(zone.get("road_id", "") or ""),
        "zone_type": str(zone.get("zone_type", "presence") or "presence"),
        "polygon_points": zone.get("polygon_json", []) if isinstance(zone.get("polygon_json", []), list) else [],
        "line_points": zone.get("line_json", []) if isinstance(zone.get("line_json", []), list) else [],
        "enabled": bool(zone.get("enabled", True)),
    }


def _calibration_is_ready(calibration: dict[str, object] | None) -> bool:
    if calibration is None:
        return False
    image_width = _as_int(calibration.get("image_width"))
    image_height = _as_int(calibration.get("image_height"))
    pixels_per_meter = _as_float(calibration.get("pixels_per_meter"))
    homography_matrix = calibration.get("homography_matrix")
    has_anchor = bool(calibration.get("anchor_lat") or calibration.get("anchor_lng"))
    has_homography = isinstance(homography_matrix, list) and len(homography_matrix) == 3
    return image_width > 0 and image_height > 0 and ((pixels_per_meter > 0 and has_anchor) or has_homography)


def list_camera_runtime_status() -> list[dict[str, object]]:
    _ensure_camera_inventory()
    session = get_session()
    try:
        cameras = session.query(Camera).filter(Camera.status == "active").order_by(Camera.camera_id.asc()).all()

        result: list[dict[str, object]] = []
        for camera in cameras:
            camera_id = str(camera.camera_id or "")
            cal_data = getattr(camera, "calibration_data", None) or {}
            calibration = _serialize_calibration(cal_data if isinstance(cal_data, dict) else {})
            zones_raw = getattr(camera, "zones", None) or []
            zones = [_serialize_zone(z) for z in zones_raw if isinstance(z, dict)]

            result.append(
                {
                    "camera_id": camera_id,
                    "camera_name": str(getattr(camera, "name", "") or camera_id),
                    "road_id": _camera_road_id(camera),
                    "junction_id": _camera_junction_id(camera),
                    "status": str(getattr(camera, "status", "active") or "active"),
                    "calibration_ready": _calibration_is_ready(calibration),
                    "calibration": calibration,
                    "zone_count": len(zones),
                    "enabled_zone_count": sum(1 for zone in zones if zone.get("enabled")),
                    "zone_types": sorted({str(zone.get("zone_type") or "presence") for zone in zones}),
                    "tracking_ready": False,
                    "latest_metric_at": None,
                }
            )
        return result
    finally:
        session.close()


def get_camera_runtime_bundle(camera_id: str) -> dict[str, object]:
    _ensure_camera_inventory()
    session = get_session()
    try:
        camera = session.query(Camera).filter(Camera.camera_id == camera_id).first()
        if camera is None:
            raise LookupError(f"Camera '{camera_id}' not found")

        cal_data = getattr(camera, "calibration_data", None) or {}
        calibration = _serialize_calibration(cal_data if isinstance(cal_data, dict) else {})
        zones_raw = getattr(camera, "zones", None) or []
        zones = [_serialize_zone(z) for z in zones_raw if isinstance(z, dict)]

        return {
            "camera": {
                "camera_id": str(camera.camera_id or ""),
                "name": str(getattr(camera, "name", "") or camera_id),
                "road_id": _camera_road_id(camera),
                "junction_id": _camera_junction_id(camera),
                "lat": _as_float(getattr(camera, "lat", 0.0)),
                "lng": _as_float(getattr(camera, "lng", 0.0)),
                "status": str(getattr(camera, "status", "active") or "active"),
            },
            "calibration": calibration,
            "zones": zones,
            "status": {
                "calibration_ready": _calibration_is_ready(calibration),
                "zone_count": len(zones),
                "enabled_zone_count": sum(1 for zone in zones if zone.get("enabled")),
                "tracking_ready": False,
                "latest_metric_at": None,
            },
        }
    finally:
        session.close()


def upsert_camera_calibration(camera_id: str, payload: dict[str, object]) -> dict[str, object]:
    _ensure_camera_inventory()
    session = get_session()
    try:
        camera = session.query(Camera).filter(Camera.camera_id == camera_id).first()
        if camera is None:
            raise LookupError(f"Camera '{camera_id}' not found")

        homography_matrix = payload.get("homography_matrix")
        if homography_matrix is not None and homography_matrix != []:
            if not isinstance(homography_matrix, list) or len(homography_matrix) != 3:
                raise ValueError("homography_matrix ต้องเป็น list 3 แถว")

        cal_data = {
            "image_width": _as_int(payload.get("image_width")),
            "image_height": _as_int(payload.get("image_height")),
            "homography_matrix": homography_matrix if isinstance(homography_matrix, list) else [],
            "anchor_lat": _as_float(payload.get("anchor_lat")),
            "anchor_lng": _as_float(payload.get("anchor_lng")),
            "bearing_deg": _as_float(payload.get("bearing_deg")),
            "pixels_per_meter": _as_float(payload.get("pixels_per_meter")),
        }
        setattr(camera, "calibration_data", cal_data)
        session.commit()
        return _serialize_calibration(cal_data) or {}
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def replace_camera_zones(camera_id: str, zones: list[dict[str, object]]) -> list[dict[str, object]]:
    _ensure_camera_inventory()
    session = get_session()
    try:
        camera = session.query(Camera).filter(Camera.camera_id == camera_id).first()
        if camera is None:
            raise LookupError(f"Camera '{camera_id}' not found")

        camera_road_id = ensure_road(session, camera.road_id or camera.road, road_name=camera.road)
        camera_junction_id_val = canonical_junction_id(camera.junction_id, camera.sumo_tls_id, camera.junction)
        ensure_junction(
            session,
            camera_junction_id_val,
            junction_name=getattr(camera, "name", "") or getattr(camera, "junction", ""),
            sumo_tls_id=getattr(camera, "sumo_tls_id", ""),
            lat=getattr(camera, "lat", None),
            lng=getattr(camera, "lng", None),
        )
        setattr(camera, "road_id", camera_road_id)
        setattr(camera, "junction_id", camera_junction_id_val)

        normalized_zones: list[dict[str, object]] = []
        seen_zone_ids: set[str] = set()
        for raw_zone in zones:
            zone_id = str(raw_zone.get("zone_id") or "").strip()
            if not zone_id:
                raise ValueError("zone_id ห้ามว่าง")
            if zone_id in seen_zone_ids:
                raise ValueError(f"zone_id ซ้ำ: {zone_id}")
            seen_zone_ids.add(zone_id)

            zone_type = str(raw_zone.get("zone_type") or "presence").strip().lower()
            if zone_type not in VALID_ZONE_TYPES:
                raise ValueError(f"zone_type ไม่รองรับ: {zone_type}")

            polygon_points = _normalize_point_list(raw_zone.get("polygon_points"), minimum_points=3)
            line_points = _normalize_point_list(raw_zone.get("line_points"), minimum_points=2)
            if not polygon_points and not line_points:
                raise ValueError(f"zone '{zone_id}' ต้องมี polygon_points หรือ line_points อย่างน้อยหนึ่งแบบ")

            junction_id = canonical_junction_id(raw_zone.get("junction_id"), camera_junction_id_val)
            road_id = ensure_road(session, raw_zone.get("road_id") or camera_road_id or getattr(camera, "road", ""))
            approach_id = str(raw_zone.get("approach_id") or "").strip() or None
            ensure_junction(session, junction_id, junction_name=getattr(camera, "name", ""), sumo_tls_id=getattr(camera, "sumo_tls_id", ""), lat=getattr(camera, "lat", None), lng=getattr(camera, "lng", None))
            if approach_id:
                ensure_approach(
                    session,
                    junction_id=junction_id,
                    approach_id=approach_id,
                    road_id=road_id,
                    camera_id=camera_id,
                )

            normalized_zones.append(
                {
                    "zone_id": zone_id,
                    "junction_id": junction_id,
                    "approach_id": approach_id,
                    "road_id": road_id,
                    "zone_type": zone_type,
                    "polygon_json": polygon_points,
                    "line_json": line_points,
                    "enabled": bool(raw_zone.get("enabled", True)),
                }
            )

        setattr(camera, "zones", normalized_zones)
        session.commit()

        return [_serialize_zone(z) for z in normalized_zones]
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def load_runtime_seed_json(file_path: str) -> dict[str, object]:
    if not os.path.exists(file_path):
        raise FileNotFoundError(file_path)
    with open(file_path, encoding="utf-8") as file_handle:
        payload = json.load(file_handle)
    if not isinstance(payload, dict):
        raise ValueError("Seed JSON ต้องเป็น object")
    return payload


def build_runtime_seed_payload() -> dict[str, object]:
    _ensure_camera_inventory()
    session = get_session()
    try:
        cameras = session.query(Camera).filter(Camera.status == "active").order_by(Camera.camera_id.asc()).all()

        return {
            "version": 1,
            "cameras": [
                {
                    "camera_id": str(camera.camera_id or ""),
                    "camera_name": str(camera.name or camera.camera_id or ""),
                    "road_id": _camera_road_id(camera),
                    "junction_id": _camera_junction_id(camera),
                    "status": str(camera.status or "active"),
                    "calibration": _serialize_calibration(
                        getattr(camera, "calibration_data", None) if isinstance(getattr(camera, "calibration_data", None), dict) else {}
                    ),
                    "zones": [
                        _serialize_zone(z)
                        for z in (getattr(camera, "zones", None) or [])
                        if isinstance(z, dict)
                    ],
                }
                for camera in cameras
            ],
        }
    finally:
        session.close()


def write_runtime_seed_template(file_path: str) -> dict[str, object]:
    payload = build_runtime_seed_payload()
    directory = os.path.dirname(file_path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(file_path, "w", encoding="utf-8") as file_handle:
        json.dump(payload, file_handle, ensure_ascii=False, indent=2)
        file_handle.write("\n")
    return payload


def apply_runtime_seed(payload: dict[str, object], *, replace_zones: bool = False) -> dict[str, int]:
    cameras = payload.get("cameras")
    if not isinstance(cameras, list):
        raise ValueError("Seed JSON ต้องมี key 'cameras' เป็น list")

    calibration_count = 0
    zone_camera_count = 0
    valid_camera_count = 0
    for item in cameras:
        if not isinstance(item, dict):
            continue
        camera_id = str(item.get("camera_id") or "").strip()
        if not camera_id:
            continue
        valid_camera_count += 1

        calibration_payload = item.get("calibration")
        if isinstance(calibration_payload, dict):
            upsert_camera_calibration(camera_id, calibration_payload)
            calibration_count += 1

        zones_payload = item.get("zones")
        if replace_zones and isinstance(zones_payload, list):
            replace_camera_zones(camera_id, zones_payload)
            zone_camera_count += 1

    return {
        "camera_count": valid_camera_count,
        "calibration_upserts": calibration_count,
        "zone_replacements": zone_camera_count,
    }