"""Seed camera calibration and zone configuration from JSON."""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import Config
from database.connection import init_db
from services.camera_runtime import apply_runtime_seed, load_runtime_seed_json, write_runtime_seed_template


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed camera runtime calibration/zones into the database")
    parser.add_argument(
        "--file",
        default=os.path.join(Config.PROJECT_ROOT, "data", "camera_runtime_config.json"),
        help="Path to camera runtime config JSON",
    )
    parser.add_argument(
        "--replace-zones",
        action="store_true",
        help="Replace all zones for cameras included in the seed file",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate JSON and print summary without writing to the database",
    )
    parser.add_argument(
        "--export-template",
        default="",
        help="Write the current camera inventory and runtime config out to a JSON file",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("  TraffixFlow — Camera Runtime Seed")
    print("=" * 60)
    print(f"Seed file: {args.file}")
    print(f"Replace zones: {args.replace_zones}")

    if args.export_template:
        init_db()
        payload = write_runtime_seed_template(args.export_template)
        camera_entries = payload.get("cameras") if isinstance(payload, dict) else []
        camera_count = len(camera_entries) if isinstance(camera_entries, list) else 0
        print(f"\n✅ Runtime template exported: {args.export_template}")
        print(f"  - camera_count: {camera_count}")
        return

    payload = load_runtime_seed_json(args.file)
    camera_entries = payload.get("cameras") if isinstance(payload, dict) else []
    camera_count = len(camera_entries) if isinstance(camera_entries, list) else 0
    print(f"Camera entries: {camera_count}")

    if args.dry_run:
        print("\n✅ Dry run passed")
        return

    init_db()
    summary = apply_runtime_seed(payload, replace_zones=args.replace_zones)
    print("\n✅ Runtime config seeded")
    for key, value in summary.items():
        print(f"  - {key}: {value}")


if __name__ == "__main__":
    main()