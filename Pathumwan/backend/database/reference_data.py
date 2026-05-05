"""Helpers for canonical road/junction/approach master data."""

from __future__ import annotations

import json
import os
from functools import lru_cache
from typing import Any

from config import Config
from database.models import Approach, Junction, Road


def _clean_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


@lru_cache(maxsize=1)
def _load_defined_roads() -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    roads_path = os.path.join(Config.PROJECT_ROOT, "data", "pathumwan_roads.json")
    by_id: dict[str, dict[str, Any]] = {}
    by_name: dict[str, str] = {}

    if not os.path.exists(roads_path):
        return by_id, by_name

    try:
        with open(roads_path, encoding="utf-8") as file_handle:
            payload = json.load(file_handle)
    except Exception:
        return by_id, by_name

    for raw in payload.get("roads", []) or []:
        road_id = _clean_text(raw.get("code"))
        if not road_id:
            continue
        by_id[road_id] = raw

        aliases = set(raw.get("aliases", []) or [])
        aliases.update({
            road_id,
            road_id.upper(),
            _clean_text(raw.get("name_th")) or "",
            _clean_text(raw.get("name_en")) or "",
        })

        for alias in aliases:
            alias = _clean_text(alias)
            if alias:
                by_name[alias.casefold()] = road_id

    return by_id, by_name


def normalize_road_id(value: Any) -> str | None:
    road_id = _clean_text(value)
    if not road_id:
        return None

    by_id, by_name = _load_defined_roads()
    if road_id in by_id:
        return road_id

    upper = road_id.upper()
    if upper in by_id:
        return upper

    return by_name.get(road_id.casefold(), road_id)


def canonical_junction_id(*values: Any) -> str | None:
    for value in values:
        cleaned = _clean_text(value)
        if cleaned:
            return cleaned
    return None


def ensure_road(session, road_id: Any, road_name: Any = None, free_flow_speed_kmh: Any = None) -> str | None:
    normalized = normalize_road_id(road_id)
    if not normalized:
        return None

    by_id, _ = _load_defined_roads()
    defined = by_id.get(normalized, {})
    name = _clean_text(road_name) or _clean_text(defined.get("name_th")) or normalized
    free_flow = free_flow_speed_kmh
    if free_flow is None:
        free_flow = defined.get("free_flow_speed_kmh")

    row = session.get(Road, normalized)
    if row is None:
        row = Road(road_id=normalized, road_name=name)
        session.add(row)

    row.road_name = name or row.road_name or normalized
    if free_flow is not None:
        row.free_flow_speed_kmh = float(free_flow)
    return normalized


def ensure_junction(
    session,
    junction_id: Any,
    *,
    junction_name: Any = None,
    sumo_tls_id: Any = None,
    lat: Any = None,
    lng: Any = None,
) -> str | None:
    normalized = _clean_text(junction_id)
    if not normalized:
        return None

    row = session.get(Junction, normalized)
    if row is None:
        row = Junction(junction_id=normalized)
        session.add(row)

    name = _clean_text(junction_name)
    if name:
        row.junction_name = name
    elif not row.junction_name:
        row.junction_name = normalized

    tls_id = _clean_text(sumo_tls_id)
    if tls_id:
        row.sumo_tls_id = tls_id
    if lat not in (None, ""):
        row.lat = float(lat)
    if lng not in (None, ""):
        row.lng = float(lng)
    return normalized


def ensure_approach(
    session,
    *,
    junction_id: Any,
    approach_id: Any,
    road_id: Any = None,
    camera_id: Any = None,
    approach_name: Any = None,
) -> tuple[str | None, str | None]:
    normalized_junction = _clean_text(junction_id)
    normalized_approach = _clean_text(approach_id)
    if not normalized_junction or not normalized_approach:
        return normalized_junction, normalized_approach

    normalized_road = ensure_road(session, road_id)
    ensure_junction(session, normalized_junction)

    with session.no_autoflush:
        row = (
            session.query(Approach)
            .filter(Approach.junction_id == normalized_junction, Approach.approach_id == normalized_approach)
            .first()
        )
    if row is None:
        row = Approach(junction_id=normalized_junction, approach_id=normalized_approach)
        session.add(row)

    if normalized_road:
        row.road_id = normalized_road
    camera_value = _clean_text(camera_id)
    if camera_value:
        row.camera_id = camera_value
    name = _clean_text(approach_name)
    if name:
        row.approach_name = name
    elif not row.approach_name:
        row.approach_name = normalized_approach
    row.enabled = True
    return normalized_junction, normalized_approach