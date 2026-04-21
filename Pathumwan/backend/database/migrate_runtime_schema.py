"""Ensure additive runtime schema objects exist for sim/real unified mode."""

from __future__ import annotations

import os
import sys

from sqlalchemy import inspect

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.connection import get_engine, init_db


def main() -> None:
    print("=" * 60)
    print("  TraffixFlow — Runtime Schema Migration")
    print("=" * 60)
    init_db()

    engine = get_engine()
    inspector = inspect(engine)
    tables = sorted(inspector.get_table_names())

    required = {
        "roads",
        "junctions",
        "approaches",
        "camera_streams",
        "camera_calibrations",
        "camera_zones",
        "live_vehicle_tracks",
        "live_approach_metrics",
        "signal_controllers",
        "signal_states",
        "runtime_config",
    }
    missing = sorted(required - set(tables))

    print(f"Tables present: {len(tables)}")
    for name in tables:
        print(f"  - {name}")

    if missing:
        print("\n❌ Missing required runtime tables:")
        for name in missing:
            print(f"  - {name}")
        raise SystemExit(1)

    print("\n✅ Runtime schema is ready")


if __name__ == "__main__":
    main()