"""Camera DB sync utilities.

Keeps the DB camera inventory consistent with the traffic lights that exist in
the SUMO network.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import xml.etree.ElementTree as ET
from typing import Iterable, Mapping, Any

from config import Config
from utils import sumo_xy_to_latlng
from database.reference_data import canonical_junction_id, ensure_approach, ensure_junction, ensure_road

_REPAIR_LOCK = threading.Lock()
_REPAIR_INTERVAL_SECONDS = 15.0
_LAST_REPAIR_AT = 0.0
_LAST_REPAIR_RESULT: dict[str, int] = {"active": 0, "inactive": 0}


def _load_defined_camera_meta() -> tuple[dict[str, dict[str, Any]], set[str]]:
    """Load optional naming metadata from pathumwan_roads.json."""

    roads_path = os.path.join(Config.PROJECT_ROOT, "data", "pathumwan_roads.json")
    if not os.path.exists(roads_path):
        return {}, set()

    try:
        with open(roads_path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {}, set()

    defined = [c for c in (data.get("cameras", []) or []) if c.get("id")]
    return {str(c["id"]): c for c in defined}, {str(c["id"]) for c in defined}


def _load_research_targets_by_junction() -> dict[str, dict[str, Any]]:
    """Load canonical research camera aliases keyed by SUMO TLS/junction id."""

    roads_path = os.path.join(Config.PROJECT_ROOT, "data", "pathumwan_roads.json")
    if not os.path.exists(roads_path):
        return {}

    try:
        with open(roads_path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {}

    result: dict[str, dict[str, Any]] = {}
    for target in data.get("research_targets", []) or []:
        if not isinstance(target, dict):
            continue
        junction_id = str(target.get("junction_id") or "").strip()
        camera_id = str(target.get("camera_id") or "").strip()
        if junction_id and camera_id:
            result[junction_id] = target
    return result


def sync_live_camera_inventory(camera_points: Iterable[Mapping[str, Any]]) -> None:
    """Upsert the current traffic-light camera inventory and deactivate stale rows."""

    defined_map, _ = _load_defined_camera_meta()
    live_points = [c for c in camera_points if c.get("camera_id")]
    live_ids: set[str] = set()
    live_tls_ids = {str(c.get("sumo_tls_id") or "") for c in live_points if c.get("sumo_tls_id")}

    try:
        from database.connection import get_session
        from database.models import Camera as CameraModel
    except Exception:
        return

    session = get_session()
    try:
        for cam in live_points:
            cam_id = str(cam.get("camera_id") or "")
            tls_id = str(cam.get("sumo_tls_id") or "")
            meta = defined_map.get(cam_id, {})
            road_value = meta.get("road") or cam.get("road", "")
            road_id = ensure_road(session, road_value)
            junction_id = canonical_junction_id(tls_id, meta.get("junction"), cam.get("junction", ""))
            ensure_junction(
                session,
                junction_id,
                junction_name=meta.get("name") or cam.get("name", cam_id),
                sumo_tls_id=tls_id,
                lat=cam.get("lat"),
                lng=cam.get("lng"),
            )

            row = session.query(CameraModel).filter(CameraModel.camera_id == cam_id).first()
            if row is None and tls_id:
                row = session.query(CameraModel).filter(CameraModel.sumo_tls_id == tls_id).first()
            resolved_camera_id = cam_id
            if row is not None:
                existing_camera_id = str(getattr(row, "camera_id", "") or "")
                if existing_camera_id:
                    resolved_camera_id = existing_camera_id
            if row is None:
                row = CameraModel(
                    camera_id=resolved_camera_id,
                    name=meta.get("name") or cam.get("name", cam_id),
                    road=road_value,
                    road_id=road_id,
                    lat=float(cam.get("lat", 0) or 0),
                    lng=float(cam.get("lng", 0) or 0),
                    junction=meta.get("junction") or cam.get("junction", ""),
                    junction_id=junction_id,
                    status="active",
                )
                session.add(row)

            live_ids.add(resolved_camera_id)
            setattr(row, "camera_id", resolved_camera_id)
            setattr(row, "name", str(meta.get("name") or cam.get("name", row.name) or resolved_camera_id))
            setattr(row, "road", str(road_value or row.road or ""))
            setattr(row, "road_id", road_id)
            setattr(row, "junction", str(meta.get("junction") or cam.get("junction", row.junction) or ""))
            setattr(row, "junction_id", junction_id)
            lat_value = float(cam.get("lat", 0) or 0.0)
            lng_value = float(cam.get("lng", 0) or 0.0)
            setattr(row, "lat", lat_value)
            setattr(row, "lng", lng_value)
            setattr(row, "sumo_tls_id", tls_id)
            setattr(row, "status", "active")
            ensure_approach(
                session,
                junction_id=junction_id,
                approach_id=resolved_camera_id,
                road_id=road_id,
                camera_id=resolved_camera_id,
                approach_name=f"{resolved_camera_id}→{junction_id}",
            )

        active_rows = session.query(CameraModel).filter(CameraModel.status == "active").all()
        for row in active_rows:
            row_id = str(row.camera_id or "")
            row_tls_id = str(row.sumo_tls_id or "")
            if row_id not in live_ids and row_tls_id not in live_tls_ids:
                setattr(row, "status", "inactive")

        session.commit()
    except Exception:
        session.rollback()
    finally:
        session.close()



def _parse_network_tls_points() -> list[dict[str, Any]]:
    """Parse traffic-light junctions directly from osm.net.xml for offline DB sync."""

    net_path = Config.SUMO_NET_FILE
    if not os.path.exists(net_path):
        return []

    tree = ET.parse(net_path)
    root = tree.getroot()

    edge_name_map: dict[str, str] = {}
    for edge in root.findall("edge"):
        edge_id = edge.get("id")
        if not edge_id or edge_id.startswith(":"):
            continue
        edge_name_map[edge_id] = str(edge.get("name") or "").strip()

    from cctv import is_in_pathumwan

    tls_points: list[dict[str, Any]] = []
    for junction in root.findall("junction"):
        if junction.get("type") != "traffic_light":
            continue
        tid = str(junction.get("id") or "")
        x = float(junction.get("x") or 0)
        y = float(junction.get("y") or 0)
        lat, lng = sumo_xy_to_latlng(x, y)
        if not is_in_pathumwan(lat, lng):
            continue
        street_names: list[str] = []
        seen = set()
        for lane_id in str(junction.get("incLanes") or "").split():
            edge_id = lane_id.rsplit("_", 1)[0]
            edge_name = edge_name_map.get(edge_id, "")
            if edge_name and edge_name not in seen:
                seen.add(edge_name)
                street_names.append(edge_name)
        tls_points.append({
            "tid": tid,
            "x": x,
            "y": y,
            "lat": lat,
            "lng": lng,
            "street_names": street_names[:2],
        })
    return tls_points


def _build_network_camera_inventory() -> list[dict[str, Any]]:
    """Build one camera row for each traffic-light junction in the net file."""

    tls_points = _parse_network_tls_points()
    if not tls_points:
        return []

    from cctv import _build_fallback_camera_label, _find_matching_camera_def

    research_by_junction = _load_research_targets_by_junction()
    used_defined_ids: set[str] = set()
    cameras: list[dict[str, Any]] = []
    for index, tls in enumerate(tls_points):
        tls_id = str(tls["tid"])
        research_target = research_by_junction.get(tls_id)
        if research_target is not None:
            cam_id = str(research_target.get("camera_id") or "")
            used_defined_ids.add(cam_id)
            cameras.append({
                "camera_id": cam_id,
                "name": (
                    research_target.get("camera_label_th")
                    or research_target.get("label_th")
                    or cam_id
                ),
                "road": research_target.get("road", tls["street_names"][0] if tls["street_names"] else ""),
                "junction": (
                    research_target.get("junction_slug")
                    or research_target.get("label_th")
                    or " / ".join(tls["street_names"])
                ),
                "lat": float(research_target.get("lat", tls["lat"]) or tls["lat"]),
                "lng": float(research_target.get("lng", tls["lng"]) or tls["lng"]),
                "sumo_tls_id": tls_id,
            })
            continue

        matched = _find_matching_camera_def(float(tls["lat"]), float(tls["lng"]), used_defined_ids)
        if matched is not None:
            cam_id = str(matched.get("id") or "")
            used_defined_ids.add(cam_id)
            cameras.append({
                "camera_id": cam_id,
                "name": matched.get("name", cam_id),
                "road": matched.get("road", tls["street_names"][0] if tls["street_names"] else ""),
                "junction": matched.get("junction", " / ".join(tls["street_names"])),
                "lat": float(matched.get("lat", tls["lat"])),
                "lng": float(matched.get("lng", tls["lng"])),
                "sumo_tls_id": tls_id,
            })
        else:
            fallback = _build_fallback_camera_label(tls["street_names"], tls_id, index)
            cameras.append({
                "camera_id": fallback["camera_id"],
                "name": fallback["name"],
                "road": fallback["road"],
                "junction": fallback["junction"],
                "lat": float(tls["lat"]),
                "lng": float(tls["lng"]),
                "sumo_tls_id": tls_id,
            })

    return cameras


def sync_cameras_from_network() -> dict[str, int]:
    """Offline sync camera rows from all traffic lights in the net file."""

    cameras = _build_network_camera_inventory()
    if not cameras:
        return {"active": 0, "inactive": 0}

    live_ids: set[str] = set()
    live_tls_ids = {cam["sumo_tls_id"] for cam in cameras}

    if not str(Config.DATABASE_URI or "").startswith("sqlite:///"):
        try:
            from database.connection import get_session
            from database.models import Camera as CameraModel
        except Exception:
            return {"active": 0, "inactive": 0}

        session = get_session()
        inactive = 0
        try:
            for cam in cameras:
                road_id = ensure_road(session, cam["road"])
                junction_id = canonical_junction_id(cam.get("sumo_tls_id"), cam.get("junction"))
                ensure_junction(
                    session,
                    junction_id,
                    junction_name=cam.get("name"),
                    sumo_tls_id=cam.get("sumo_tls_id"),
                    lat=cam.get("lat"),
                    lng=cam.get("lng"),
                )
                row = session.query(CameraModel).filter(CameraModel.camera_id == cam["camera_id"]).first()
                if row is None:
                    row = session.query(CameraModel).filter(CameraModel.sumo_tls_id == cam["sumo_tls_id"]).first()
                resolved_camera_id = str(cam["camera_id"])
                if row is not None:
                    existing_camera_id = str(getattr(row, "camera_id", "") or "")
                    if existing_camera_id:
                        resolved_camera_id = existing_camera_id
                if row is None:
                    row = CameraModel(
                        camera_id=resolved_camera_id,
                        name=cam["name"],
                        road=cam["road"],
                        road_id=road_id,
                        lat=cam["lat"],
                        lng=cam["lng"],
                        junction=cam["junction"],
                        junction_id=junction_id,
                        sumo_tls_id=cam["sumo_tls_id"],
                        status="active",
                    )
                    session.add(row)

                live_ids.add(resolved_camera_id)
                setattr(row, "camera_id", resolved_camera_id)
                setattr(row, "name", str(cam["name"]))
                setattr(row, "road", str(cam["road"]))
                setattr(row, "road_id", road_id)
                setattr(row, "lat", float(cam["lat"]))
                setattr(row, "lng", float(cam["lng"]))
                setattr(row, "junction", str(cam["junction"]))
                setattr(row, "junction_id", junction_id)
                setattr(row, "sumo_tls_id", str(cam["sumo_tls_id"]))
                setattr(row, "status", "active")
                ensure_approach(
                    session,
                    junction_id=junction_id,
                    approach_id=resolved_camera_id,
                    road_id=road_id,
                    camera_id=resolved_camera_id,
                    approach_name=f"{resolved_camera_id}→{junction_id}",
                )

            active_rows = session.query(CameraModel).filter(CameraModel.status == "active").all()
            for row in active_rows:
                row_id = str(row.camera_id or "")
                row_tls_id = str(row.sumo_tls_id or "")
                if row_id not in live_ids and row_tls_id not in live_tls_ids:
                    setattr(row, "status", "inactive")
                    inactive += 1

            session.commit()
            return {"active": len(cameras), "inactive": inactive}
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    return sync_sqlite_cameras_from_network()


def sync_sqlite_cameras_from_network() -> dict[str, int]:
    """Backward-compatible wrapper for offline camera sync."""

    if not str(Config.DATABASE_URI or "").startswith("sqlite:///"):
        return sync_cameras_from_network()

    cameras = _build_network_camera_inventory()
    if not cameras:
        return {"active": 0, "inactive": 0}

    db_path = Config.DATABASE_URI.replace("sqlite:///", "")
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        now_expr = "CURRENT_TIMESTAMP"
        live_ids: set[str] = set()
        live_tls_ids = {cam["sumo_tls_id"] for cam in cameras}

        for cam in cameras:
            row = cur.execute(
                "SELECT id, camera_id FROM cameras WHERE camera_id = ? OR sumo_tls_id = ? LIMIT 1",
                (cam["camera_id"], cam["sumo_tls_id"]),
            ).fetchone()
            resolved_camera_id = str(cam["camera_id"])
            if row and len(row) > 1 and str(row[1] or ""):
                resolved_camera_id = str(row[1])
            live_ids.add(resolved_camera_id)
            if row:
                cur.execute(
                    """
                    UPDATE cameras
                    SET camera_id = ?, name = ?, road = ?, lat = ?, lng = ?, junction = ?, sumo_tls_id = ?, status = 'active'
                    WHERE id = ?
                    """,
                    (resolved_camera_id, cam["name"], cam["road"], cam["lat"], cam["lng"], cam["junction"], cam["sumo_tls_id"], row[0]),
                )
            else:
                cur.execute(
                    f"""
                    INSERT INTO cameras (camera_id, name, road, lat, lng, junction, sumo_tls_id, status, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'active', {now_expr})
                    """,
                    (resolved_camera_id, cam["name"], cam["road"], cam["lat"], cam["lng"], cam["junction"], cam["sumo_tls_id"]),
                )

        rows = cur.execute("SELECT id, camera_id, sumo_tls_id FROM cameras WHERE status = 'active'").fetchall()
        inactive = 0
        for row_id, camera_id, sumo_tls_id in rows:
            if str(camera_id or "") not in live_ids and str(sumo_tls_id or "") not in live_tls_ids:
                cur.execute("UPDATE cameras SET status = 'inactive' WHERE id = ?", (row_id,))
                inactive += 1

        conn.commit()
        return {"active": len(cameras), "inactive": inactive}
    finally:
        conn.close()


def get_offline_camera_inventory() -> list[dict[str, Any]]:
    """Return the canonical camera inventory derived from the SUMO net file."""

    return _build_network_camera_inventory()


def _read_db_camera_counts() -> dict[str, int]:
    """Read current DB camera totals without mutating inventory."""

    try:
        from sqlalchemy import func
        from database.connection import get_session
        from database.models import Camera as CameraModel

        session = get_session()
        try:
            return {
                "total": int(session.query(func.count(CameraModel.id)).scalar() or 0),
                "active": int(
                    session.query(func.count(CameraModel.id))
                    .filter(CameraModel.status == "active")
                    .scalar()
                    or 0
                ),
                "inactive": int(
                    session.query(func.count(CameraModel.id))
                    .filter(CameraModel.status == "inactive")
                    .scalar()
                    or 0
                ),
            }
        finally:
            session.close()
    except Exception:
        pass

    if not str(Config.DATABASE_URI or "").startswith("sqlite:///"):
        return {"total": 0, "active": 0, "inactive": 0}

    db_path = Config.DATABASE_URI.replace("sqlite:///", "")
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        return {
            "total": int(cur.execute("SELECT COUNT(*) FROM cameras").fetchone()[0] or 0),
            "active": int(cur.execute("SELECT COUNT(*) FROM cameras WHERE status = 'active'").fetchone()[0] or 0),
            "inactive": int(cur.execute("SELECT COUNT(*) FROM cameras WHERE status = 'inactive'").fetchone()[0] or 0),
        }
    finally:
        conn.close()


def _purge_out_of_bbox_cameras() -> int:
    """Mark any DB cameras whose coordinates fall outside the Pathumwan bbox as inactive.

    Previously synced rows (e.g. ghost cameras around 13.7471/100.4996 that live in
    the OSM extract but not in the monitored district) should not keep showing up
    on the dashboard after we introduce the bbox filter.
    """
    from cctv import is_in_pathumwan

    removed = 0
    try:
        from database.connection import get_session
        from database.models import Camera as CameraModel

        session = get_session()
        try:
            rows = session.query(CameraModel).all()
            for row in rows:
                try:
                    lat = float(getattr(row, "lat", 0) or 0)
                    lng = float(getattr(row, "lng", 0) or 0)
                except Exception:
                    continue
                if lat == 0 and lng == 0:
                    continue
                if not is_in_pathumwan(lat, lng):
                    if str(getattr(row, "status", "") or "") != "inactive":
                        setattr(row, "status", "inactive")
                        removed += 1
            if removed:
                session.commit()
        except Exception:
            session.rollback()
        finally:
            session.close()
    except Exception:
        pass
    return removed


def repair_camera_inventory_if_needed(*, force: bool = False) -> dict[str, int]:
    """Repair the DB camera inventory when it has collapsed below the SUMO TLS inventory.

    This keeps API/admin pages from falling back to a stale JSON-only subset when
    the backend starts before SUMO is active or the DB was previously synced from
    an incomplete camera list. Also drops any legacy rows outside the Pathumwan
    bbox (ghost cameras from the wider OSM extract).
    """

    global _LAST_REPAIR_AT, _LAST_REPAIR_RESULT

    now = time.monotonic()
    if not force and (now - _LAST_REPAIR_AT) < _REPAIR_INTERVAL_SECONDS:
        return dict(_LAST_REPAIR_RESULT)

    with _REPAIR_LOCK:
        now = time.monotonic()
        if not force and (now - _LAST_REPAIR_AT) < _REPAIR_INTERVAL_SECONDS:
            return dict(_LAST_REPAIR_RESULT)

        _purge_out_of_bbox_cameras()

        inventory = _build_network_camera_inventory()
        expected_total = len(inventory)
        if expected_total <= 0:
            _LAST_REPAIR_AT = now
            _LAST_REPAIR_RESULT = {"active": 0, "inactive": 0}
            return dict(_LAST_REPAIR_RESULT)

        counts = _read_db_camera_counts()
        needs_sync = counts.get("total", 0) < expected_total or counts.get("active", 0) < expected_total
        result = sync_cameras_from_network() if needs_sync else {
            "active": counts.get("active", 0),
            "inactive": counts.get("inactive", 0),
        }

        _LAST_REPAIR_AT = time.monotonic()
        _LAST_REPAIR_RESULT = {
            "active": int(result.get("active", 0) or 0),
            "inactive": int(result.get("inactive", 0) or 0),
        }
        return dict(_LAST_REPAIR_RESULT)
