"""Canonical runtime mapping helpers for sim/real pipelines."""

from __future__ import annotations

import json
import os
import re
import threading
from collections import defaultdict
from typing import Any, Optional

from services.camera_sync import repair_camera_inventory_if_needed
from database.connection import get_session
from database.models import Camera

# Matches SUMO-auto-generated junction IDs (cluster_*, TLS_*, J1234, joinedS_*, 42_12)
_MACHINE_ID_RE = re.compile(r"^(joinedS_|cluster_|TLS_|J\d|\d+[_-]\d+)", re.IGNORECASE)

_JUNCTION_DIR_LOCK = threading.Lock()
_JUNCTION_DIR: dict[str, str] | None = None
_JUNCTION_DIR_MTIME: float | None = None


def _roads_json_path() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    # backend/services → repo root → data/
    return os.path.abspath(os.path.join(here, "..", "..", "data", "pathumwan_roads.json"))


def _load_roads_payload() -> dict[str, Any]:
    path = _roads_json_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def get_research_targets() -> list[dict[str, Any]]:
    """Return the canonical research subset used for AI/control comparisons."""
    payload = _load_roads_payload()
    targets = payload.get("research_targets") or []
    if not isinstance(targets, list):
        return []
    return [target for target in targets if isinstance(target, dict) and target.get("junction_id")]


def get_research_junction_ids() -> list[str]:
    return [str(target.get("junction_id") or "") for target in get_research_targets() if str(target.get("junction_id") or "")]


def get_research_camera_ids() -> list[str]:
    return [str(target.get("camera_id") or "") for target in get_research_targets() if str(target.get("camera_id") or "")]


def get_research_target_by_junction() -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for target in get_research_targets():
        junction_id = str(target.get("junction_id") or "").strip()
        if junction_id:
            result[junction_id] = target
    return result


def get_research_target_by_camera() -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for target in get_research_targets():
        camera_id = str(target.get("camera_id") or "").strip()
        if camera_id:
            result[camera_id] = target
    return result


def _load_junction_directory() -> dict[str, str]:
    """Load junction slug/TLS → Thai name map from pathumwan_roads.json (cached by mtime)."""
    global _JUNCTION_DIR, _JUNCTION_DIR_MTIME
    path = _roads_json_path()
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return _JUNCTION_DIR or {}

    with _JUNCTION_DIR_LOCK:
        if _JUNCTION_DIR is not None and _JUNCTION_DIR_MTIME == mtime:
            return _JUNCTION_DIR

        directory: dict[str, str] = {}
        try:
            payload = _load_roads_payload()
            for junction in payload.get("junctions") or []:
                if not isinstance(junction, dict):
                    continue
                name_th = str(junction.get("name_th") or "").strip()
                if not name_th:
                    continue
                for key_source in ("id", "sumo_tls_id"):
                    raw = str(junction.get(key_source) or "").strip()
                    if raw:
                        directory[raw.lower()] = name_th
            # Also fold in camera junction slugs → name_th by matching roads prefix
            for cam in payload.get("cameras") or []:
                if not isinstance(cam, dict):
                    continue
                slug = str(cam.get("junction") or "").strip().lower()
                if slug and slug not in directory:
                    # best-effort: strip "CCTV " prefix and take the part before " (" in name
                     label = str(cam.get("name") or "").strip()
                     m = re.match(r"^CCTV\s+([^\(\)]+?)(?:\s*\(|$)", label)
                     if m:
                         directory[slug] = m.group(1).strip()
            for target in payload.get("research_targets") or []:
                if not isinstance(target, dict):
                    continue
                label_th = str(target.get("label_th") or target.get("camera_label_th") or "").strip()
                if not label_th:
                    continue
                for key_source in ("junction_id", "junction_slug", "camera_id"):
                    raw = str(target.get(key_source) or "").strip()
                    if raw:
                        directory[raw.lower()] = label_th
        except Exception:
            pass

        _JUNCTION_DIR = directory
        _JUNCTION_DIR_MTIME = mtime
        return directory


def looks_like_machine_id(value: Any) -> bool:
    """Return True when a junction/camera ID looks auto-generated (not human-friendly)."""
    label = str(value or "").strip()
    if not label:
        return True
    if _MACHINE_ID_RE.match(label):
        return True
    # Values with underscore + digit inside are almost always SUMO-generated
    if "_" in label and any(ch.isdigit() for ch in label):
        return True
    return False


def get_junction_display_name(junction_id: Any, fallback: Any = None) -> Optional[str]:
    """Resolve a junction ID to a human-friendly Thai name, or None if unknown.

    Checks (in order):
      1. `pathumwan_roads.json::junctions[].sumo_tls_id` / `.id`
      2. `pathumwan_roads.json::cameras[].junction` slug
      3. `fallback` (only if it does not look like a machine-generated ID)
    """
    raw = str(junction_id or "").strip()
    if raw:
        directory = _load_junction_directory()
        hit = directory.get(raw.lower())
        if hit:
            return hit
    fallback_str = str(fallback or "").strip()
    if fallback_str and not looks_like_machine_id(fallback_str):
        return fallback_str
    return None


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


def _ensure_camera_inventory() -> None:
    try:
        repair_camera_inventory_if_needed()
    except Exception:
        pass


def get_camera_catalog() -> dict[str, dict[str, object]]:
    """Return active camera metadata keyed by camera_id."""
    _ensure_camera_inventory()
    session = get_session()
    try:
        rows = session.query(Camera).filter(Camera.status == "active").all()
        result: dict[str, dict[str, object]] = {}
        for row in rows:
            camera_id = str(getattr(row, "camera_id", "") or "")
            if not camera_id:
                continue
            result[camera_id] = {
                "camera_id": camera_id,
                "name": str(getattr(row, "name", "") or camera_id),
                "road_id": str(getattr(row, "road_id", "") or getattr(row, "road", "") or ""),
                "junction_id": str(getattr(row, "junction_id", "") or getattr(row, "sumo_tls_id", "") or getattr(row, "junction", "") or ""),
                "lat": _as_float(getattr(row, "lat", 0.0)),
                "lng": _as_float(getattr(row, "lng", 0.0)),
            }
        return result
    finally:
        session.close()


def _get_camera_zones(cam: Camera) -> list[dict[str, Any]]:
    """Extract zone list from Camera.zones JSON column."""
    raw = getattr(cam, "zones", None)
    if isinstance(raw, list):
        return [z for z in raw if isinstance(z, dict) and z.get("enabled", True)]
    return []


def get_camera_approach_map() -> dict[str, list[dict[str, str]]]:
    """Return camera -> approach mappings from Camera.zones JSON."""
    _ensure_camera_inventory()
    session = get_session()
    try:
        cameras = session.query(Camera).filter(Camera.status == "active").all()
        result: dict[str, list[dict[str, str]]] = defaultdict(list)
        for cam in cameras:
            camera_id = str(cam.camera_id or "")
            if not camera_id:
                continue
            for zone in _get_camera_zones(cam):
                result[camera_id].append(
                    {
                        "zone_id": str(zone.get("zone_id", "") or ""),
                        "junction_id": str(zone.get("junction_id", "") or ""),
                        "approach_id": str(zone.get("approach_id", "") or ""),
                        "road_id": str(zone.get("road_id", "") or ""),
                        "zone_type": str(zone.get("zone_type", "presence") or "presence"),
                    }
                )
        return dict(result)
    finally:
        session.close()


def get_camera_zone_map() -> dict[str, list[dict[str, object]]]:
    """Return full enabled zone geometry keyed by camera_id."""
    _ensure_camera_inventory()
    session = get_session()
    try:
        cameras = session.query(Camera).filter(Camera.status == "active").all()
        result: dict[str, list[dict[str, object]]] = defaultdict(list)
        for cam in cameras:
            camera_id = str(cam.camera_id or "")
            if not camera_id:
                continue
            for zone in _get_camera_zones(cam):
                polygon_json = zone.get("polygon_json", [])
                line_json = zone.get("line_json", [])
                result[camera_id].append(
                    {
                        "zone_id": str(zone.get("zone_id", "") or ""),
                        "junction_id": str(zone.get("junction_id", "") or ""),
                        "approach_id": str(zone.get("approach_id", "") or ""),
                        "road_id": str(zone.get("road_id", "") or ""),
                        "zone_type": str(zone.get("zone_type", "presence") or "presence"),
                        "polygon_points": polygon_json if isinstance(polygon_json, list) else [],
                        "line_points": line_json if isinstance(line_json, list) else [],
                    }
                )
        return dict(result)
    finally:
        session.close()


def get_camera_calibration_map() -> dict[str, dict[str, object]]:
    """Return calibration metadata keyed by camera_id from Camera.calibration_data."""
    _ensure_camera_inventory()
    session = get_session()
    try:
        cameras = session.query(Camera).filter(Camera.status == "active").all()
        result: dict[str, dict[str, object]] = {}
        for cam in cameras:
            camera_id = str(cam.camera_id or "")
            if not camera_id:
                continue
            cal = getattr(cam, "calibration_data", None)
            if not isinstance(cal, dict):
                cal = {}
            result[camera_id] = {
                "camera_id": camera_id,
                "image_width": _as_int(cal.get("image_width", 0)),
                "image_height": _as_int(cal.get("image_height", 0)),
                "homography_matrix": cal.get("homography_matrix", []) if isinstance(cal.get("homography_matrix", []), list) else [],
                "anchor_lat": _as_float(cal.get("anchor_lat", 0.0)),
                "anchor_lng": _as_float(cal.get("anchor_lng", 0.0)),
                "bearing_deg": _as_float(cal.get("bearing_deg", 0.0)),
                "pixels_per_meter": _as_float(cal.get("pixels_per_meter", 0.0)),
            }
        return result
    finally:
        session.close()


def get_junction_camera_map() -> dict[str, list[str]]:
    """Return junction -> camera_ids mapping using zones first, camera metadata second."""
    catalog = get_camera_catalog()
    camera_zones = get_camera_approach_map()
    result: dict[str, list[str]] = defaultdict(list)

    for camera_id, zones in camera_zones.items():
        for zone in zones:
            junction_id = str(zone.get("junction_id") or "")
            if junction_id and camera_id not in result[junction_id]:
                result[junction_id].append(camera_id)

    for camera_id, meta in catalog.items():
        junction_id = str(meta.get("junction_id") or "")
        if junction_id and camera_id not in result[junction_id]:
            result[junction_id].append(camera_id)

    return dict(result)


def get_junction_approach_map() -> dict[str, list[dict[str, str]]]:
    """Return junction -> approach mappings from Camera.zones JSON."""
    camera_zones = get_camera_approach_map()
    result: dict[str, list[dict[str, str]]] = defaultdict(list)

    for camera_id, zones in camera_zones.items():
        for zone in zones:
            junction_id = str(zone.get("junction_id") or "")
            if not junction_id:
                continue
            item = {
                "camera_id": camera_id,
                "approach_id": str(zone.get("approach_id") or ""),
                "road_id": str(zone.get("road_id") or ""),
                "zone_id": str(zone.get("zone_id") or ""),
                "zone_type": str(zone.get("zone_type") or "presence"),
            }
            if item not in result[junction_id]:
                result[junction_id].append(item)

    return dict(result)
