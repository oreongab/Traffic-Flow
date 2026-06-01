"""Clean pathumwan_traffic_profile.json — fix data quality issues #7 and #8.

Issues addressed:
  #7: Low sample counts (< 5) and anomalous speed (e.g. 0.7 km/h)
  #8: Extreme volume_avg / volume_median skew (> 3x)

Outputs:
  pathumwan_traffic_profile.cleaned.json  — cleaned data
  pathumwan_traffic_profile.changelog.json — what was changed and why
"""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

# ─── Thresholds ───────────────────────────────────────────────────────
MIN_SAMPLES = 5          # Below this → interpolate from neighbours
MIN_SPEED_KMH = 2.0      # speed_avg below this is likely sensor error
MAX_SPEED_KMH = 80.0     # Urban Pathumwan cap
VOLUME_SKEW_RATIO = 3.0  # If avg/median > this → replace avg with median
# ──────────────────────────────────────────────────────────────────────

DATA_DIR = Path(__file__).resolve().parent
INPUT_FILE = DATA_DIR / "pathumwan_traffic_profile.json"
OUTPUT_FILE = DATA_DIR / "pathumwan_traffic_profile.cleaned.json"
CHANGELOG_FILE = DATA_DIR / "pathumwan_traffic_profile.changelog.json"


def _interpolate_from_neighbours(hourly: dict, hour_key: str, field: str) -> float | None:
    """Linear-interpolate a field from the two neighbouring hours."""
    h = int(hour_key)
    prev_key = str(h - 1)
    next_key = str(h + 1)
    values = []
    for k in (prev_key, next_key):
        if k in hourly and field in hourly[k]:
            values.append(hourly[k][field])
    if not values:
        return None
    return round(sum(values) / len(values), 2)


def _station_median(hourly: dict, field: str, exclude_hour: str | None = None) -> float | None:
    """Compute median of a field across all hours of a station (optionally excluding one)."""
    values = []
    for h, data in hourly.items():
        if h == exclude_hour:
            continue
        if field in data and data[field] is not None:
            values.append(data[field])
    if not values:
        return None
    return round(statistics.median(values), 2)


def clean_profile(profile: dict) -> tuple[dict, list[dict]]:
    """Return (cleaned_profile, changelog)."""
    import copy
    cleaned = copy.deepcopy(profile)
    changelog: list[dict] = []

    stations = cleaned.get("stations", {})
    for station_name, station in stations.items():
        hourly = station.get("hourly", {})

        # ── Pass 1: Fix low-sample hours ──────────────────────────
        for hour_key, data in hourly.items():
            samples = data.get("samples", 0)
            if samples < MIN_SAMPLES:
                change = {
                    "station": station_name,
                    "hour": hour_key,
                    "issue": f"low_samples ({samples} < {MIN_SAMPLES})",
                    "fixes": [],
                }
                # Interpolate volume_avg from neighbours
                for field in ("volume_avg", "speed_avg", "density_avg", "vc_avg"):
                    old_val = data.get(field)
                    new_val = _interpolate_from_neighbours(hourly, hour_key, field)
                    if new_val is not None and old_val != new_val:
                        change["fixes"].append({
                            "field": field,
                            "old": old_val,
                            "new": new_val,
                            "method": "neighbour_interpolation",
                        })
                        data[field] = new_val
                # Flag
                data["_low_sample_interpolated"] = True
                if change["fixes"]:
                    changelog.append(change)

        # ── Pass 2: Fix anomalous speed ───────────────────────────
        for hour_key, data in hourly.items():
            speed = data.get("speed_avg", 0.0)
            if speed < MIN_SPEED_KMH:
                median_speed = _station_median(hourly, "speed_avg", exclude_hour=hour_key)
                if median_speed is not None:
                    changelog.append({
                        "station": station_name,
                        "hour": hour_key,
                        "issue": f"anomalous_speed ({speed} km/h < {MIN_SPEED_KMH})",
                        "fixes": [{
                            "field": "speed_avg",
                            "old": speed,
                            "new": median_speed,
                            "method": "station_median_replacement",
                        }],
                    })
                    data["speed_avg"] = median_speed
                    data["_speed_corrected"] = True

            elif speed > MAX_SPEED_KMH:
                capped = MAX_SPEED_KMH
                changelog.append({
                    "station": station_name,
                    "hour": hour_key,
                    "issue": f"speed_too_high ({speed} km/h > {MAX_SPEED_KMH})",
                    "fixes": [{
                        "field": "speed_avg",
                        "old": speed,
                        "new": capped,
                        "method": "urban_speed_cap",
                    }],
                })
                data["speed_avg"] = capped
                data["_speed_capped"] = True

        # ── Pass 3: Fix volume skew ───────────────────────────────
        for hour_key, data in hourly.items():
            vol_avg = data.get("volume_avg", 0)
            vol_med = data.get("volume_median", 0)
            if vol_med > 0 and vol_avg / vol_med > VOLUME_SKEW_RATIO:
                changelog.append({
                    "station": station_name,
                    "hour": hour_key,
                    "issue": f"volume_skew (avg={vol_avg} / median={vol_med} = {vol_avg / vol_med:.1f}x)",
                    "fixes": [{
                        "field": "volume_avg",
                        "old": vol_avg,
                        "new": vol_med,
                        "method": "replaced_with_median",
                    }],
                })
                data["volume_avg"] = vol_med
                data["_volume_corrected"] = True

    # ── Recompute average_hourly_profile ─────────────────────────
    all_hours: dict[str, list[dict]] = {}
    for station in stations.values():
        for hour_key, data in station.get("hourly", {}).items():
            all_hours.setdefault(hour_key, []).append(data)

    new_avg_profile: dict[str, dict] = {}
    for hour_key in sorted(all_hours.keys(), key=int):
        entries = all_hours[hour_key]
        n = len(entries)
        new_avg_profile[hour_key] = {
            "volume_avg": round(sum(e.get("volume_avg", 0) for e in entries) / n, 0),
            "speed_avg": round(sum(e.get("speed_avg", 0) for e in entries) / n, 1),
            "density_avg": round(sum(e.get("density_avg", 0) for e in entries) / n, 1),
            "vc_avg": round(sum(e.get("vc_avg", 0) for e in entries) / n, 2),
            "stations": n,
        }
    cleaned["average_hourly_profile"] = new_avg_profile

    return cleaned, changelog


def main() -> int:
    if not INPUT_FILE.exists():
        print(f"❌ Input file not found: {INPUT_FILE}")
        return 1

    with INPUT_FILE.open(encoding="utf-8") as f:
        profile = json.load(f)

    print(f"📂 Input:  {INPUT_FILE}")
    print(f"   Stations: {len(profile.get('stations', {}))}")

    cleaned, changelog = clean_profile(profile)

    # Write cleaned file
    OUTPUT_FILE.write_text(
        json.dumps(cleaned, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"✅ Output: {OUTPUT_FILE}")

    # Write changelog
    CHANGELOG_FILE.write_text(
        json.dumps({
            "source": str(INPUT_FILE.name),
            "output": str(OUTPUT_FILE.name),
            "total_fixes": len(changelog),
            "thresholds": {
                "min_samples": MIN_SAMPLES,
                "min_speed_kmh": MIN_SPEED_KMH,
                "max_speed_kmh": MAX_SPEED_KMH,
                "volume_skew_ratio": VOLUME_SKEW_RATIO,
            },
            "changes": changelog,
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"📋 Changelog: {CHANGELOG_FILE}")
    print(f"   Total fixes: {len(changelog)}")

    # Summary
    issues_by_type: dict[str, int] = {}
    for change in changelog:
        issue_type = change["issue"].split("(")[0].strip()
        issues_by_type[issue_type] = issues_by_type.get(issue_type, 0) + 1

    for issue_type, count in sorted(issues_by_type.items()):
        print(f"   - {issue_type}: {count} fixes")

    return 0


if __name__ == "__main__":
    # Fix Windows console encoding for emojis
    if sys.platform == "win32":
        reconfigure = getattr(sys.stdout, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
