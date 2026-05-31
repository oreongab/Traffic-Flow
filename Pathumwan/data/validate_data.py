"""Validate data quality before training — comprehensive pre-flight checks.

Run this script to verify all data sources are correct before starting RL training.

Usage:
    cd Pathumwan/data
    python validate_data.py
"""

from __future__ import annotations

import csv
import json
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = DATA_DIR.parent

# ─── Expected constants ──────────────────────────────────────────────
PATHUMWAN_BBOX = {
    "min_lat": 13.72,
    "max_lat": 13.76,
    "min_lng": 100.51,
    "max_lng": 100.56,
}

VALID_EDGE_ALIASES = {
    "edge_phayathai_in", "edge_rama1_in", "edge_ratchadamri_in",
    "edge_chalermphao_in", "edge_rama1_pongphraram_in",
    "edge_banthatthong_in", "edge_samyan_phayathai_in",
    "edge_ram4_saladaeng_in", "edge_ram4_witthayu_in",
    "edge_phloenchit_in", "edge_chitlom_in",
    "edge_sarasin_ratchadamri_in", "edge_ram4_henri_in",
    "edge_ram4_saphanluang_in", "edge_phetchaburi_uruphong_in",
    "edge_ratchadamri_pratunam_in", "edge_banthatthong_charat_in",
}

VALID_VEHICLE_TYPES = {
    "รถจักรยาน", "รถจักรยานยนต์", "รถตุ๊กตุ๊ก_สามล้อ",
    "รถยนต์นั่งส่วนบุคคล", "รถแท็กซี่", "รถตู้",
    "รถโดยสารขนาดเล็ก", "รถโดยสารขนาดใหญ่",
    "รถบรรทุก4ล้อ", "รถบรรทุก6ล้อ", "รถบรรทุก10ล้อขึ้นไป",
    "รถพ่วง_รถกึ่งพ่วง",
}


class ValidationResult:
    def __init__(self, name: str):
        self.name = name
        self.passed: list[str] = []
        self.warnings: list[str] = []
        self.errors: list[str] = []

    def ok(self, msg: str):
        self.passed.append(msg)

    def warn(self, msg: str):
        self.warnings.append(msg)

    def error(self, msg: str):
        self.errors.append(msg)

    def print_summary(self):
        status = "✅" if not self.errors else "❌"
        if not self.errors and self.warnings:
            status = "⚠️"
        print(f"\n{status} {self.name}")
        print(f"   {'─' * 50}")
        for msg in self.passed:
            print(f"   ✅ {msg}")
        for msg in self.warnings:
            print(f"   ⚠️  {msg}")
        for msg in self.errors:
            print(f"   ❌ {msg}")

    @property
    def is_ok(self) -> bool:
        return len(self.errors) == 0


def validate_traffic_profile(use_cleaned: bool = True) -> ValidationResult:
    """Validate pathumwan_traffic_profile.json (or .cleaned.json)."""
    result = ValidationResult("Traffic Profile")

    # Prefer cleaned version if it exists
    cleaned_path = DATA_DIR / "pathumwan_traffic_profile.cleaned.json"
    original_path = DATA_DIR / "pathumwan_traffic_profile.json"

    if use_cleaned and cleaned_path.exists():
        profile_path = cleaned_path
    elif original_path.exists():
        profile_path = original_path
    else:
        result.error("pathumwan_traffic_profile.json not found")
        return result

    result.ok(f"Using: {profile_path.name}")

    with profile_path.open(encoding="utf-8") as f:
        profile = json.load(f)

    stations = profile.get("stations", {})
    if not stations:
        result.error("No stations found in profile")
        return result

    result.ok(f"Stations: {len(stations)}")

    bbox = profile.get("bounding_box", {})
    for station_name, station_data in stations.items():
        lat = station_data.get("lat", 0)
        lng = station_data.get("lng", 0)

        # Check bounding box
        if bbox:
            if not (bbox.get("min_lat", 0) <= lat <= bbox.get("max_lat", 999)):
                result.warn(f"{station_name}: lat {lat} outside bounding box")
            if not (bbox.get("min_lng", 0) <= lng <= bbox.get("max_lng", 999)):
                result.warn(f"{station_name}: lng {lng} outside bounding box")

        hourly = station_data.get("hourly", {})
        for hour_key, data in hourly.items():
            # Check speed
            speed = data.get("speed_avg", 0)
            if speed < 2.0:
                result.error(f"{station_name} hour={hour_key}: speed_avg={speed} km/h (< 2.0)")
            elif speed > 80.0:
                result.warn(f"{station_name} hour={hour_key}: speed_avg={speed} km/h (> 80)")

            # Check volume skew
            vol_avg = data.get("volume_avg", 0)
            vol_med = data.get("volume_median", 0)
            if vol_med > 0 and vol_avg / vol_med > 3.0:
                result.warn(
                    f"{station_name} hour={hour_key}: "
                    f"volume_avg/median = {vol_avg}/{vol_med} = {vol_avg / vol_med:.1f}x"
                )

            # Check samples
            samples = data.get("samples", 0)
            if samples < 5:
                # Only warn if not already interpolated
                if not data.get("_low_sample_interpolated"):
                    result.warn(f"{station_name} hour={hour_key}: samples={samples} (< 5)")

    result.ok("All station data validated")
    return result


def validate_dataset_csv() -> ValidationResult:
    """Validate Dataset.csv."""
    result = ValidationResult("Dataset.csv")

    csv_path = DATA_DIR / "Dataset.csv"
    if not csv_path.exists():
        result.error("Dataset.csv not found")
        return result

    with csv_path.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))

    result.ok(f"Rows: {len(rows)}")

    junctions = set()
    unknown_aliases = set()
    unknown_vehicles = set()
    zero_volume_count = 0

    for i, row in enumerate(rows, start=2):
        edge_alias = row.get("Edge_ขาเข้า", "")
        vehicle_type = row.get("ประเภทรถ", "")
        volume_str = row.get("ปริมาณรถ(คัน/ชม.)", "0")
        junction_code = row.get("รหัสทางแยก", "")

        junctions.add(junction_code)

        if edge_alias and edge_alias not in VALID_EDGE_ALIASES:
            unknown_aliases.add(edge_alias)

        if vehicle_type and vehicle_type not in VALID_VEHICLE_TYPES:
            unknown_vehicles.add(vehicle_type)

        try:
            volume = float(volume_str)
            if volume <= 0:
                zero_volume_count += 1
        except (ValueError, TypeError):
            result.error(f"Row {i}: invalid volume '{volume_str}'")

    result.ok(f"Junctions: {sorted(junctions)}")

    if unknown_aliases:
        result.error(f"Unknown edge aliases: {unknown_aliases}")
    else:
        result.ok("All edge aliases are valid")

    if unknown_vehicles:
        result.error(f"Unknown vehicle types: {unknown_vehicles}")
    else:
        result.ok(f"All {len(VALID_VEHICLE_TYPES)} vehicle types recognized")

    if zero_volume_count > 0:
        result.warn(f"{zero_volume_count} rows with volume <= 0")
    else:
        result.ok("All volumes > 0")

    return result


def validate_sumo_config() -> ValidationResult:
    """Validate osm.sumocfg."""
    result = ValidationResult("SUMO Configuration")

    cfg_path = PROJECT_ROOT / "osm.sumocfg"
    if not cfg_path.exists():
        result.error("osm.sumocfg not found")
        return result

    try:
        tree = ET.parse(cfg_path)
        root = tree.getroot()
    except ET.ParseError as e:
        result.error(f"XML parse error: {e}")
        return result

    # Check scale
    scale_elem = root.find(".//scale")
    if scale_elem is not None:
        scale_val = float(scale_elem.get("value", "1.0"))
        if scale_val < 1.0:
            result.warn(f"scale = {scale_val} (< 1.0) — simulation uses reduced traffic")
        else:
            result.ok(f"scale = {scale_val}")
    else:
        result.ok("scale = 1.0 (default)")

    # Check time
    begin_elem = root.find(".//begin")
    end_elem = root.find(".//end")
    if begin_elem is not None and end_elem is not None:
        begin_sec = int(begin_elem.get("value", "0"))
        end_sec = int(end_elem.get("value", "0"))
        begin_hr = begin_sec / 3600
        end_hr = end_sec / 3600
        duration_hr = (end_sec - begin_sec) / 3600
        result.ok(f"Simulation period: {begin_hr:.0f}:00 – {end_hr:.0f}:00 ({duration_hr:.0f}h)")

        if begin_hr > 7:
            result.warn(f"Missing early morning traffic (starts at {begin_hr:.0f}:00)")
        if end_hr < 22:
            result.warn(f"Missing evening traffic (ends at {end_hr:.0f}:00)")

    # Check route files
    route_elem = root.find(".//route-files")
    if route_elem is not None:
        route_file = route_elem.get("value", "")
        result.ok(f"Route file: {route_file}")
        route_path = PROJECT_ROOT / route_file
        if not route_path.exists():
            result.error(f"Route file not found: {route_path}")
    else:
        result.warn("No route-files specified")

    # Check net file
    net_elem = root.find(".//net-file")
    if net_elem is not None:
        net_file = net_elem.get("value", "")
        net_path = PROJECT_ROOT / net_file
        if not net_path.exists():
            result.error(f"Network file not found: {net_path}")
        else:
            size_mb = net_path.stat().st_size / (1024 * 1024)
            result.ok(f"Network file: {net_file} ({size_mb:.1f} MB)")

    return result


def validate_model_files() -> ValidationResult:
    """Validate trained model files."""
    result = ValidationResult("Model Files")

    models_dir = PROJECT_ROOT / "backend" / "ai" / "models"
    if not models_dir.exists():
        result.warn(f"Models directory not found: {models_dir}")
        result.warn("Run: python -m ai.trainer --algorithm PPO --timesteps 100000")
        return result

    model_files = list(models_dir.glob("*.zip"))
    if not model_files:
        result.warn("No trained model .zip files found")
        result.warn("Run: python -m ai.trainer --algorithm PPO --timesteps 100000")
    else:
        for model_file in model_files:
            size_kb = model_file.stat().st_size / 1024
            if size_kb < 1:
                result.error(f"{model_file.name}: suspiciously small ({size_kb:.1f} KB)")
            else:
                result.ok(f"{model_file.name} ({size_kb:.0f} KB)")

    return result


def main() -> int:
    print("=" * 60)
    print("  📊 Data Quality Validation — Pathumwan Traffic")
    print("=" * 60)

    results = [
        validate_traffic_profile(use_cleaned=True),
        validate_dataset_csv(),
        validate_sumo_config(),
        validate_model_files(),
    ]

    for r in results:
        r.print_summary()

    total_errors = sum(len(r.errors) for r in results)
    total_warnings = sum(len(r.warnings) for r in results)
    total_passed = sum(len(r.passed) for r in results)

    print(f"\n{'=' * 60}")
    print(f"  Summary: {total_passed} passed, {total_warnings} warnings, {total_errors} errors")
    if total_errors > 0:
        print("  ❌ Fix errors before training!")
    elif total_warnings > 0:
        print("  ⚠️  Review warnings — training possible but may have issues")
    else:
        print("  ✅ All checks passed — ready to train!")
    print(f"{'=' * 60}")

    return 1 if total_errors > 0 else 0


if __name__ == "__main__":
    if sys.platform == "win32":
        reconfigure = getattr(sys.stdout, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
