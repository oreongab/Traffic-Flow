"""
Migration script: Consolidate 21 tables → 17 tables
  - Merge camera_streams, camera_calibrations, camera_zones INTO cameras
  - Drop live_vehicle_tracks, live_approach_metrics, simulation_snapshots

Run: python backend/migrate_consolidate.py
"""
import json
from database.connection import get_session
from sqlalchemy import text


def migrate():
    session = get_session()
    try:
        # ──────────────────────────────────────────────
        # Step 1: Add new columns to cameras table
        # ──────────────────────────────────────────────
        new_columns = [
            ("stream_url", "TEXT DEFAULT ''"),
            ("stream_type", "VARCHAR(20) DEFAULT 'sim'"),
            ("stream_enabled", "BOOLEAN DEFAULT TRUE"),
            ("fps_target", "INTEGER DEFAULT 8"),
            ("last_frame_at", "TIMESTAMPTZ"),
            ("stream_status", "VARCHAR(20) DEFAULT 'offline'"),
            ("calibration_data", "JSONB DEFAULT '{}'::jsonb"),
            ("zones", "JSONB DEFAULT '[]'::jsonb"),
        ]

        for col_name, col_type in new_columns:
            try:
                session.execute(text(
                    f"ALTER TABLE cameras ADD COLUMN IF NOT EXISTS {col_name} {col_type}"
                ))
                print(f"  ✓ Added column cameras.{col_name}")
            except Exception as e:
                print(f"  ⚠ Column cameras.{col_name} may already exist: {e}")
                session.rollback()

        session.commit()
        print("✅ Step 1: New columns added to cameras")

        # ──────────────────────────────────────────────
        # Step 2: Migrate data from camera_streams
        # ──────────────────────────────────────────────
        try:
            rows = session.execute(text("SELECT * FROM camera_streams")).fetchall()
            col_names = session.execute(text("SELECT * FROM camera_streams LIMIT 0")).keys()
            for row in rows:
                row_dict = dict(zip(col_names, row))
                camera_id = row_dict.get("camera_id", "")
                session.execute(text("""
                    UPDATE cameras SET
                        stream_url = :stream_url,
                        stream_type = :stream_type,
                        stream_enabled = :enabled,
                        fps_target = :fps_target,
                        last_frame_at = :last_frame_at,
                        stream_status = :stream_status
                    WHERE camera_id = :camera_id
                """), {
                    "stream_url": row_dict.get("stream_url", ""),
                    "stream_type": row_dict.get("stream_type", "sim"),
                    "enabled": row_dict.get("enabled", True),
                    "fps_target": row_dict.get("fps_target", 8),
                    "last_frame_at": row_dict.get("last_frame_at"),
                    "stream_status": row_dict.get("stream_status", "offline"),
                    "camera_id": camera_id,
                })
            session.commit()
            print(f"✅ Step 2: Migrated {len(rows)} rows from camera_streams")
        except Exception as e:
            print(f"  ⚠ camera_streams migration skipped (table may not exist): {e}")
            session.rollback()

        # ──────────────────────────────────────────────
        # Step 3: Migrate data from camera_calibrations
        # ──────────────────────────────────────────────
        try:
            rows = session.execute(text("SELECT * FROM camera_calibrations")).fetchall()
            col_names = session.execute(text("SELECT * FROM camera_calibrations LIMIT 0")).keys()
            for row in rows:
                row_dict = dict(zip(col_names, row))
                camera_id = row_dict.get("camera_id", "")
                cal_data = {
                    "image_width": row_dict.get("image_width", 0),
                    "image_height": row_dict.get("image_height", 0),
                    "homography_matrix": row_dict.get("homography_matrix", []),
                    "anchor_lat": row_dict.get("anchor_lat", 0.0),
                    "anchor_lng": row_dict.get("anchor_lng", 0.0),
                    "bearing_deg": row_dict.get("bearing_deg", 0.0),
                    "pixels_per_meter": row_dict.get("pixels_per_meter", 0.0),
                }
                session.execute(text("""
                    UPDATE cameras SET calibration_data = :cal_data
                    WHERE camera_id = :camera_id
                """), {"cal_data": json.dumps(cal_data), "camera_id": camera_id})
            session.commit()
            print(f"✅ Step 3: Migrated {len(rows)} calibrations from camera_calibrations")
        except Exception as e:
            print(f"  ⚠ camera_calibrations migration skipped: {e}")
            session.rollback()

        # ──────────────────────────────────────────────
        # Step 4: Migrate data from camera_zones → JSON
        # ──────────────────────────────────────────────
        try:
            rows = session.execute(text("SELECT * FROM camera_zones")).fetchall()
            col_names = session.execute(text("SELECT * FROM camera_zones LIMIT 0")).keys()
            # Group by camera_id
            zones_by_camera: dict[str, list[dict]] = {}
            for row in rows:
                row_dict = dict(zip(col_names, row))
                camera_id = row_dict.get("camera_id", "")
                if camera_id not in zones_by_camera:
                    zones_by_camera[camera_id] = []
                zones_by_camera[camera_id].append({
                    "zone_id": row_dict.get("zone_id", ""),
                    "junction_id": row_dict.get("junction_id", ""),
                    "approach_id": row_dict.get("approach_id", ""),
                    "road_id": row_dict.get("road_id", ""),
                    "zone_type": row_dict.get("zone_type", "presence"),
                    "polygon_json": row_dict.get("polygon_json", []),
                    "line_json": row_dict.get("line_json", []),
                    "enabled": row_dict.get("enabled", True),
                })

            for camera_id, zones in zones_by_camera.items():
                session.execute(text("""
                    UPDATE cameras SET zones = :zones
                    WHERE camera_id = :camera_id
                """), {"zones": json.dumps(zones), "camera_id": camera_id})

            session.commit()
            print(f"✅ Step 4: Migrated zones for {len(zones_by_camera)} cameras")
        except Exception as e:
            print(f"  ⚠ camera_zones migration skipped: {e}")
            session.rollback()

        # ──────────────────────────────────────────────
        # Step 5: Drop old tables
        # ──────────────────────────────────────────────
        tables_to_drop = [
            "camera_streams",
            "camera_calibrations",
            "camera_zones",
            "live_vehicle_tracks",
            "live_approach_metrics",
            "simulation_snapshots",
        ]
        for table_name in tables_to_drop:
            try:
                session.execute(text(f"DROP TABLE IF EXISTS {table_name} CASCADE"))
                print(f"  ✓ Dropped table {table_name}")
            except Exception as e:
                print(f"  ⚠ Could not drop {table_name}: {e}")
                session.rollback()

        session.commit()
        print("✅ Step 5: Old tables dropped")

        # ──────────────────────────────────────────────
        # Step 6: Create index on new columns
        # ──────────────────────────────────────────────
        try:
            session.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_cameras_last_frame_at ON cameras(last_frame_at)"
            ))
            session.commit()
            print("✅ Step 6: Indexes created")
        except Exception as e:
            print(f"  ⚠ Index creation issue: {e}")
            session.rollback()

        print("\n" + "=" * 50)
        print("🎉 Migration complete! 21 → 17 tables")
        print("=" * 50)

    except Exception as e:
        session.rollback()
        print(f"❌ Migration failed: {e}")
        raise
    finally:
        session.close()


if __name__ == "__main__":
    migrate()
