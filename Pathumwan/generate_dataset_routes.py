"""Generate SUMO trip flows from data/Dataset.csv instead of random trips."""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from collections import deque
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_DATASET = PROJECT_ROOT / "data" / "Dataset.csv"
DEFAULT_NET = PROJECT_ROOT / "osm.net.xml"
DEFAULT_TRIPS = PROJECT_ROOT / "osm.dataset.trips.xml"
DEFAULT_MAPPING = PROJECT_ROOT / "data" / "dataset_route_mapping.generated.json"

DRIVABLE_CLASSES = {
    "passenger",
    "motorcycle",
    "truck",
    "bus",
    "taxi",
    "bicycle",
    "delivery",
    "hov",
    "evehicle",
}

CLASS_COMPATIBILITY = {
    "passenger": {"passenger", "taxi", "delivery", "hov", "evehicle"},
    "taxi": {"taxi", "passenger", "delivery", "hov", "evehicle"},
    "motorcycle": {"motorcycle"},
    "bus": {"bus"},
    "truck": {"truck", "delivery"},
    "bicycle": {"bicycle"},
}

ROAD_NAME_ALIASES = {
    "RAMA1": ["ถนนพระราม1", "ถนนพระราม 1", "ถนนพระรามที่ 1", "พระราม1", "พระราม 1"],
    "RAMA4": ["ถนนพระราม4", "ถนนพระราม 4", "ถนนพระรามที่ 4", "พระราม4", "พระราม 4"],
    "PHAYATHAI": ["ถนนพญาไท", "พญาไท"],
    "RATCHADAMRI": ["ถนนราชดำริ", "ราชดำริ"],
    "PLOENCHIT": ["ถนนเพลินจิต", "เพลินจิต"],
    "PHETCHABURI": ["ถนนเพชรบุรี", "เพชรบุรี"],
    "BANTHATTHONG": ["ถนนบรรทัดทอง", "บรรทัดทอง"],
    "CHARUMUEANG": ["ถนนจารุเมือง", "จารุเมือง", "ถนนจรัสเมือง", "จรัสเมือง"],
    "WITTHAYU": ["ถนนวิทยุ", "วิทยุ", "wireless"],
    "HENRIDUNANT": ["ถนนอังรีดูนังต์", "อังรีดูนังต์", "henri dunant"],
    "SARASIN": ["ถนนสารสิน", "สารสิน"],
}


@dataclass(frozen=True)
class AliasMeta:
    edge_alias: str
    junction_id: str
    road_code: str
    lat: float
    lng: float
    label: str
    approximate: bool = False


@dataclass(frozen=True)
class VehicleSpec:
    type_id: str
    preferred_class: str
    fallback_classes: tuple[str, ...]
    attrs: dict[str, str]


@dataclass(frozen=True)
class EdgeRecord:
    edge_id: str
    name: str
    normalized_name: str
    shape: tuple[tuple[float, float], ...]
    start: tuple[float, float]
    end: tuple[float, float]
    center: tuple[float, float]
    allowed: frozenset[str]
    road_codes: frozenset[str]


ALIAS_METADATA = {
    "edge_phayathai_in": AliasMeta("edge_phayathai_in", "pathumwan", "PHAYATHAI", 13.7466, 100.5291, "แยกปทุมวัน ขาเข้า ถ.พญาไท"),
    "edge_rama1_in": AliasMeta("edge_rama1_in", "pathumwan", "RAMA1", 13.7466, 100.5291, "แยกปทุมวัน ขาเข้า ถ.พระราม1"),
    "edge_ratchadamri_in": AliasMeta("edge_ratchadamri_in", "ratchaprasong", "RATCHADAMRI", 13.7461, 100.5399, "แยกราชประสงค์ ขาเข้า ถ.ราชดำริ"),
    "edge_chalermphao_in": AliasMeta("edge_chalermphao_in", "chalermphao", "RAMA1", 13.7446968, 100.5355829, "แยกเฉลิมเผ่า ขาเข้า ถ.พระราม1"),
    "edge_rama1_pongphraram_in": AliasMeta("edge_rama1_pongphraram_in", "siam", "RAMA1", 13.7454, 100.5344, "แยกพงษ์พระราม ขาเข้า ถ.พระราม1", True),
    "edge_banthatthong_in": AliasMeta("edge_banthatthong_in", "charoenphon", "BANTHATTHONG", 13.7462592, 100.5235185, "แยกเจริญผล ขาเข้า ถ.บรรทัดทอง"),
    "edge_samyan_phayathai_in": AliasMeta("edge_samyan_phayathai_in", "samyan", "PHAYATHAI", 13.7332, 100.5291, "แยกสามย่าน ขาเข้า ถ.พญาไท"),
    "edge_ram4_saladaeng_in": AliasMeta("edge_ram4_saladaeng_in", "saladaeng", "RAMA4", 13.7285677, 100.5343416, "แยกศาลาแดง ขาเข้า ถ.พระราม4"),
    "edge_ram4_witthayu_in": AliasMeta("edge_ram4_witthayu_in", "rama4_witthayu", "RAMA4", 13.7280872, 100.5447008, "แยกวิทยุ ขาเข้า ถ.พระราม4", True),
    "edge_phloenchit_in": AliasMeta("edge_phloenchit_in", "ploenchit_witthayu", "PLOENCHIT", 13.7439, 100.5470, "แยกเพลินจิต ขาเข้า ถ.เพลินจิต"),
    "edge_chitlom_in": AliasMeta("edge_chitlom_in", "chidlom", "PLOENCHIT", 13.7445, 100.5430, "แยกชิดลม ขาเข้า ถ.เพลินจิต"),
    "edge_sarasin_ratchadamri_in": AliasMeta("edge_sarasin_ratchadamri_in", "sarasin", "RATCHADAMRI", 13.7411, 100.5395, "แยกสารสิน ขาเข้า ถ.ราชดำริ"),
    "edge_ram4_henri_in": AliasMeta("edge_ram4_henri_in", "henri_rama4", "RAMA4", 13.7342472, 100.5339512, "แยกอังรีดูนังต์ ขาเข้า ถ.พระราม4", True),
    "edge_ram4_saphanluang_in": AliasMeta("edge_ram4_saphanluang_in", "saphanluang", "RAMA4", 13.7344851, 100.5237133, "แยกสะพานเหลือง ขาเข้า ถ.พระราม4"),
    "edge_phetchaburi_uruphong_in": AliasMeta("edge_phetchaburi_uruphong_in", "uruphong", "PHETCHABURI", 13.7473025, 100.5208542, "แยกอุรุพงษ์ ขาเข้า ถ.เพชรบุรี"),
    "edge_ratchadamri_pratunam_in": AliasMeta("edge_ratchadamri_pratunam_in", "pratunam", "RATCHADAMRI", 13.7493577, 100.5414931, "แยกประตูน้ำ ขาเข้า ถ.ราชดำริ"),
    "edge_banthatthong_charat_in": AliasMeta("edge_banthatthong_charat_in", "charatmuang", "BANTHATTHONG", 13.7423138, 100.5228087, "แยกจรัสเมือง ขาเข้า ถ.บรรทัดทอง"),
}


VEHICLE_SPECS = {
    "รถจักรยาน": VehicleSpec("bike_bicycle", "bicycle", ("motorcycle", "passenger"), {"vClass": "bicycle", "length": "1.8", "maxSpeed": "7.5", "color": "60,180,75"}),
    "รถจักรยานยนต์": VehicleSpec("moto_motorcycle", "motorcycle", (), {"vClass": "motorcycle", "length": "2.2", "maxSpeed": "18", "color": "230,25,75"}),
    "รถตุ๊กตุ๊ก_สามล้อ": VehicleSpec("tuktuk_passenger", "passenger", (), {"vClass": "passenger", "length": "3.2", "maxSpeed": "12", "color": "245,130,48"}),
    "รถยนต์นั่งส่วนบุคคล": VehicleSpec("car_passenger", "passenger", (), {"vClass": "passenger", "length": "4.7", "maxSpeed": "16", "color": "0,130,200"}),
    "รถแท็กซี่": VehicleSpec("taxi_passenger", "taxi", ("passenger",), {"vClass": "taxi", "length": "4.8", "maxSpeed": "16", "color": "255,225,25"}),
    "รถตู้": VehicleSpec("van_passenger", "passenger", (), {"vClass": "passenger", "length": "5.6", "maxSpeed": "15", "color": "67,99,216"}),
    "รถโดยสารขนาดเล็ก": VehicleSpec("minibus_bus", "bus", (), {"vClass": "bus", "length": "7.5", "maxSpeed": "13", "color": "145,30,180"}),
    "รถโดยสารขนาดใหญ่": VehicleSpec("bus_bus", "bus", (), {"vClass": "bus", "length": "12.0", "maxSpeed": "12", "color": "240,50,230"}),
    "รถบรรทุก4ล้อ": VehicleSpec("truck4_truck", "truck", (), {"vClass": "truck", "length": "6.5", "maxSpeed": "12", "color": "128,128,128"}),
    "รถบรรทุก6ล้อ": VehicleSpec("truck6_truck", "truck", (), {"vClass": "truck", "length": "8.5", "maxSpeed": "11", "color": "128,128,128"}),
    "รถบรรทุก10ล้อขึ้นไป": VehicleSpec("truck10_truck", "truck", (), {"vClass": "truck", "length": "11.5", "maxSpeed": "10", "color": "128,128,128"}),
    "รถพ่วง_รถกึ่งพ่วง": VehicleSpec("trailer_truck", "truck", (), {"vClass": "truck", "length": "14.5", "maxSpeed": "9", "color": "90,90,90"}),
}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate SUMO trips from Dataset.csv")
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET))
    parser.add_argument("--net", default=str(DEFAULT_NET))
    parser.add_argument("--output-trips", default=str(DEFAULT_TRIPS))
    parser.add_argument("--output-mapping", default=str(DEFAULT_MAPPING))
    parser.add_argument("--seed", type=int, default=42)
    return parser.parse_args()


def _normalize_text(value: str) -> str:
    text = str(value or "").strip().lower()
    text = text.replace("ถ.", "ถนน")
    text = text.replace("ที่", "")
    text = re.sub(r"\s+", "", text)
    return text


NORMALIZED_ROAD_ALIASES = {
    code: {_normalize_text(alias) for alias in aliases}
    for code, aliases in ROAD_NAME_ALIASES.items()
}


def _distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _point_segment_distance(point: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
    dx = b[0] - a[0]
    dy = b[1] - a[1]
    if dx == 0 and dy == 0:
        return _distance(point, a)
    t = ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    nearest = (a[0] + t * dx, a[1] + t * dy)
    return _distance(point, nearest)


def _point_shape_distance(point: tuple[float, float], shape: tuple[tuple[float, float], ...]) -> float:
    if not shape:
        return float("inf")
    if len(shape) == 1:
        return _distance(point, shape[0])
    return min(_point_segment_distance(point, a, b) for a, b in zip(shape[:-1], shape[1:]))


def _road_matches(edge: EdgeRecord, road_code: str) -> bool:
    return road_code in edge.road_codes


def _edge_allows(edge: EdgeRecord, vclass: str) -> bool:
    compatible = CLASS_COMPATIBILITY.get(vclass, {vclass})
    return bool(edge.allowed.intersection(compatible))


def _latlng_to_xy(lat: float, lng: float, location: dict[str, tuple[float, float, float, float]]) -> tuple[float, float]:
    min_x, min_y, max_x, max_y = location["conv"]
    min_lng, min_lat, max_lng, max_lat = location["orig"]
    x = min_x + ((lng - min_lng) / (max_lng - min_lng)) * (max_x - min_x)
    y = min_y + ((lat - min_lat) / (max_lat - min_lat)) * (max_y - min_y)
    return x, y


def _load_network(net_path: Path) -> tuple[list[EdgeRecord], dict[str, tuple[float, float, float, float]], dict[str, set[str]]]:
    location = None
    edges: list[EdgeRecord] = []
    outgoing_by_edge: dict[str, set[str]] = defaultdict(set)
    for _, elem in ET.iterparse(net_path, events=("end",)):
        if elem.tag == "location":
            location = {
                "conv": tuple(float(value) for value in elem.get("convBoundary", "").split(",")),
                "orig": tuple(float(value) for value in elem.get("origBoundary", "").split(",")),
            }
            elem.clear()
            continue

        if elem.tag == "connection":
            from_edge = elem.get("from") or ""
            to_edge = elem.get("to") or ""
            if from_edge and to_edge and not from_edge.startswith(":") and not to_edge.startswith(":"):
                outgoing_by_edge[from_edge].add(to_edge)
            elem.clear()
            continue

        if elem.tag != "edge":
            continue

        edge_id = elem.get("id", "")
        if not edge_id or edge_id.startswith(":") or elem.get("function"):
            elem.clear()
            continue

        shape_text = elem.get("shape")
        if not shape_text:
            first_lane = elem.find("lane")
            shape_text = first_lane.get("shape") if first_lane is not None else None
        if not shape_text:
            elem.clear()
            continue

        shape = tuple(tuple(float(value) for value in point.split(",")) for point in shape_text.split())
        if len(shape) < 2:
            elem.clear()
            continue

        allowed: set[str] = set()
        lanes = elem.findall("lane")
        if not lanes:
            allowed = set(DRIVABLE_CLASSES)
        else:
            for lane in lanes:
                allow = set((lane.get("allow") or "").split())
                disallow = set((lane.get("disallow") or "").split())
                if allow:
                    allowed.update(allow)
                elif disallow:
                    allowed.update(DRIVABLE_CLASSES.difference(disallow))
                else:
                    allowed.update(DRIVABLE_CLASSES)

        edge_name = elem.get("name") or ""
        normalized_name = _normalize_text(edge_name)
        road_codes = frozenset(
            code for code, aliases in NORMALIZED_ROAD_ALIASES.items() if normalized_name in aliases
        )

        xs = [point[0] for point in shape]
        ys = [point[1] for point in shape]
        edges.append(
            EdgeRecord(
                edge_id=edge_id,
                name=edge_name,
                normalized_name=normalized_name,
                shape=shape,
                start=shape[0],
                end=shape[-1],
                center=(sum(xs) / len(xs), sum(ys) / len(ys)),
                allowed=frozenset(allowed),
                road_codes=road_codes,
            )
        )
        elem.clear()

    if location is None:
        raise RuntimeError("osm.net.xml is missing <location> metadata")

    return edges, location, outgoing_by_edge


def _collect_reachable_edges(source_edge_id: str, outgoing_by_edge: dict[str, set[str]]) -> set[str]:
    reachable = {source_edge_id}
    queue = deque([source_edge_id])

    while queue:
        edge_id = queue.popleft()
        for next_edge_id in outgoing_by_edge.get(edge_id, ()):
            if next_edge_id in reachable:
                continue
            reachable.add(next_edge_id)
            queue.append(next_edge_id)

    return reachable


def _pick_source_edge(meta: AliasMeta, road_edges: list[EdgeRecord], vclass: str, location) -> EdgeRecord:
    target_xy = _latlng_to_xy(meta.lat, meta.lng, location)
    candidates = []
    relaxed = []
    for edge in road_edges:
        if not _edge_allows(edge, vclass):
            continue
        shape_distance = _point_shape_distance(target_xy, edge.shape)
        end_distance = _distance(target_xy, edge.end)
        start_distance = _distance(target_xy, edge.start)
        relaxed.append((shape_distance + end_distance * 0.25, end_distance, edge))
        if end_distance + 5 < start_distance:
            candidates.append((shape_distance + end_distance * 0.25, end_distance, edge))

    ranked = candidates or relaxed
    if not ranked:
        raise RuntimeError(f"No source edge found for {meta.edge_alias} ({meta.road_code}) with vClass={vclass}")

    ranked.sort(key=lambda item: (item[0], item[1], item[2].edge_id))
    return ranked[0][2]


def _destination_variant_count(rate: float) -> int:
    if rate < 120:
        return 1
    if rate < 800:
        return 2
    return 3


def _choose_destination_edges(meta: AliasMeta, source_edge: EdgeRecord, major_edges: list[EdgeRecord], all_edges: list[EdgeRecord], vclass: str, location, rate: float, seed: int, reachable_edge_ids: set[str]) -> list[tuple[EdgeRecord, float]]:
    target_xy = _latlng_to_xy(meta.lat, meta.lng, location)
    candidates_by_road: dict[str, list[tuple[float, EdgeRecord]]] = defaultdict(list)
    for edge in major_edges:
        if edge.edge_id == source_edge.edge_id:
            continue
        if edge.edge_id not in reachable_edge_ids:
            continue
        if not _edge_allows(edge, vclass):
            continue
        center_distance = _distance(target_xy, edge.center)
        if center_distance < 500:
            continue
        candidates_by_road[edge.name].append((center_distance, edge))

    if not candidates_by_road:
        for edge in major_edges:
            if edge.edge_id == source_edge.edge_id:
                continue
            if edge.edge_id not in reachable_edge_ids:
                continue
            if not _edge_allows(edge, vclass):
                continue
            center_distance = _distance(target_xy, edge.center)
            candidates_by_road[edge.name].append((center_distance, edge))

    if not candidates_by_road:
        for edge in all_edges:
            if edge.edge_id == source_edge.edge_id:
                continue
            if edge.edge_id not in reachable_edge_ids:
                continue
            if not _edge_allows(edge, vclass):
                continue
            center_distance = _distance(target_xy, edge.center)
            if center_distance < 150:
                continue
            group_key = edge.name or edge.edge_id
            candidates_by_road[group_key].append((center_distance, edge))

    preferred: list[EdgeRecord] = []
    rng = random.Random(f"{meta.edge_alias}:{vclass}:{seed}")
    for road_name, items in sorted(candidates_by_road.items(), key=lambda item: (-max(v[0] for v in item[1]), item[0])):
        items.sort(key=lambda item: (-item[0], item[1].edge_id))
        top = items[: max(1, min(5, len(items)))]
        preferred.append(rng.choice(top)[1])

    if not preferred:
        if len(reachable_edge_ids) == 1:
            preferred = [source_edge]
        else:
            raise RuntimeError(f"No destination edge found for {meta.edge_alias} with vClass={vclass}")

    preferred = [edge for edge in preferred if edge.name != source_edge.name] or preferred
    variant_count = _destination_variant_count(rate)
    chosen = preferred[:variant_count]
    weights = [1.0]
    if len(chosen) == 2:
        weights = [0.7, 0.3]
    elif len(chosen) == 3:
        weights = [0.6, 0.25, 0.15]
    return list(zip(chosen, weights))


def _resolve_vehicle_class(meta: AliasMeta, spec: VehicleSpec, road_edges: list[EdgeRecord], location) -> tuple[str, EdgeRecord]:
    classes = (spec.preferred_class,) + tuple(spec.fallback_classes)
    last_error = None
    for vclass in classes:
        try:
            return vclass, _pick_source_edge(meta, road_edges, vclass, location)
        except RuntimeError as exc:
            last_error = exc
    raise RuntimeError(str(last_error) if last_error else f"No routeable class for {meta.edge_alias}")


def _load_dataset(dataset_path: Path) -> list[dict[str, str]]:
    with dataset_path.open(encoding="utf-8-sig", newline="") as file_handle:
        return list(csv.DictReader(file_handle))


def _write_trips(rows: list[ET.Element], output_path: Path):
    routes = ET.Element(
        "routes",
        {
            "xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
            "xsi:noNamespaceSchemaLocation": "http://sumo.dlr.de/xsd/routes_file.xsd",
        },
    )

    for spec in VEHICLE_SPECS.values():
        ET.SubElement(routes, "vType", {"id": spec.type_id, **spec.attrs})

    sorted_rows = sorted(
        rows,
        key=lambda row: (
            float(row.get("begin", "0") or 0),
            float(row.get("end", "0") or 0),
            row.get("id", ""),
        ),
    )

    for row in sorted_rows:
        routes.append(row)

    tree = ET.ElementTree(routes)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tree.write(output_path, encoding="utf-8", xml_declaration=True)


def _sanitize_token(value: str) -> str:
    token = re.sub(r"[^a-zA-Z0-9_]+", "_", value.strip())
    return token.strip("_") or "item"


def main() -> int:
    args = _parse_args()
    dataset_path = Path(args.dataset)
    net_path = Path(args.net)
    output_trips = Path(args.output_trips)
    output_mapping = Path(args.output_mapping)

    dataset_rows = _load_dataset(dataset_path)
    edges, location, outgoing_by_edge = _load_network(net_path)
    edges_by_road_code: dict[str, list[EdgeRecord]] = defaultdict(list)
    major_edges: list[EdgeRecord] = []
    for edge in edges:
        if edge.road_codes:
            major_edges.append(edge)
        for road_code in edge.road_codes:
            edges_by_road_code[road_code].append(edge)

    print(f"Loaded {len(edges)} drivable edges and {len(major_edges)} major-road edges")

    flow_rows: list[ET.Element] = []
    mapping_summary = {
        "dataset": str(dataset_path),
        "net": str(net_path),
        "seed": args.seed,
        "aliases": {},
        "generated_flows": 0,
    }

    source_cache: dict[tuple[str, str], tuple[str, EdgeRecord]] = {}
    reachable_cache: dict[str, set[str]] = {}
    destination_cache: dict[tuple[str, str, str, int], list[tuple[EdgeRecord, float]]] = {}

    for row in dataset_rows:
        edge_alias = row["Edge_ขาเข้า"]
        vehicle_name = row["ประเภทรถ"]
        if edge_alias not in ALIAS_METADATA:
            raise RuntimeError(f"Unsupported dataset edge alias: {edge_alias}")
        if vehicle_name not in VEHICLE_SPECS:
            raise RuntimeError(f"Unsupported dataset vehicle type: {vehicle_name}")

        meta = ALIAS_METADATA[edge_alias]
        spec = VEHICLE_SPECS[vehicle_name]
        cache_key = (edge_alias, spec.type_id)
        if cache_key not in source_cache:
            source_cache[cache_key] = _resolve_vehicle_class(meta, spec, edges_by_road_code.get(meta.road_code, []), location)
        resolved_class, source_edge = source_cache[cache_key]
        if source_edge.edge_id not in reachable_cache:
            reachable_cache[source_edge.edge_id] = _collect_reachable_edges(source_edge.edge_id, outgoing_by_edge)

        rate = float(row["ปริมาณรถ(คัน/ชม.)"])
        begin = int(row["วินาทีเริ่มต้น"])
        end = int(row["วินาทีสิ้นสุด"])
        destination_key = (edge_alias, resolved_class, source_edge.edge_id, _destination_variant_count(rate))
        if destination_key not in destination_cache:
            destination_cache[destination_key] = _choose_destination_edges(
                meta,
                source_edge,
                major_edges,
                edges,
                resolved_class,
                location,
                rate,
                args.seed,
                reachable_cache[source_edge.edge_id],
            )
        destinations = destination_cache[destination_key]

        alias_summary = mapping_summary["aliases"].setdefault(edge_alias, {
            "label": meta.label,
            "junction_id": meta.junction_id,
            "road_code": meta.road_code,
            "lat": meta.lat,
            "lng": meta.lng,
            "approximate": meta.approximate,
            "source_edges": {},
        })
        alias_summary["source_edges"][resolved_class] = source_edge.edge_id

        period_token = f"{begin}_{end}"
        vehicle_token = _sanitize_token(spec.type_id)
        alias_token = _sanitize_token(edge_alias)

        for index, (destination_edge, weight) in enumerate(destinations, start=1):
            scaled_rate = round(rate * weight, 3)
            if scaled_rate <= 0:
                continue

            flow = ET.Element(
                "flow",
                {
                    "id": f"{alias_token}_{vehicle_token}_{period_token}_{index}",
                    "type": spec.type_id,
                    "begin": str(begin),
                    "end": str(end),
                    "vehsPerHour": f"{scaled_rate:.3f}",
                    "from": source_edge.edge_id,
                    "to": destination_edge.edge_id,
                    "departLane": "best",
                    "departSpeed": "max",
                },
            )
            flow_rows.append(flow)

    print(f"Prepared {len(flow_rows)} flows from {len(dataset_rows)} dataset rows")

    mapping_summary["generated_flows"] = len(flow_rows)

    _write_trips(flow_rows, output_trips)
    output_mapping.parent.mkdir(parents=True, exist_ok=True)
    output_mapping.write_text(json.dumps(mapping_summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Generated {len(flow_rows)} flow definitions from {len(dataset_rows)} dataset rows")
    print(f"Trips file: {output_trips}")
    print(f"Mapping file: {output_mapping}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())