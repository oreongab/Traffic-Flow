"""
TraffixFlow — Database Initialization Script
=============================================
Run this to create all tables in Neon PostgreSQL and optionally add an admin user.
Does NOT require SUMO — can run standalone.

Usage:
  python init_database.py                    # Create tables only
  python init_database.py --admin            # Create tables + admin user
  python init_database.py --show             # Show all tables and row counts
"""

import sys
import os
import hashlib
import secrets
from urllib.parse import urlsplit, urlunsplit

# Fix Windows console encoding
if sys.platform == "win32":
    stdout_reconfigure = getattr(sys.stdout, "reconfigure", None)
    stderr_reconfigure = getattr(sys.stderr, "reconfigure", None)
    if callable(stdout_reconfigure):
        stdout_reconfigure(encoding="utf-8", errors="replace")
    if callable(stderr_reconfigure):
        stderr_reconfigure(encoding="utf-8", errors="replace")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import Config
from database.connection import init_db, get_engine, get_session
from database.models import Base, User, Camera
from database.reference_data import canonical_junction_id, ensure_junction, ensure_road


def show_tables():
    """Show all tables and their columns."""
    from sqlalchemy import inspect
    engine = get_engine()
    inspector = inspect(engine)
    tables = inspector.get_table_names()

    if not tables:
        print("❌ ไม่มีตารางในฐานข้อมูล")
        return

    print(f"\n📋 ตารางทั้งหมด ({len(tables)} ตาราง):")
    print("=" * 60)

    for table_name in sorted(tables):
        columns = inspector.get_columns(table_name)
        col_info = [f"{c['name']} ({str(c['type'])[:20]})" for c in columns]
        print(f"\n  📁 {table_name}")
        for ci in col_info:
            print(f"     - {ci}")

    print("\n" + "=" * 60)


def create_admin():
    """Create an admin user interactively."""
    session = get_session()
    try:
        # Check if admin exists
        existing = session.query(User).filter(User.role == "admin").first()
        if existing:
            print(f"⚠ Admin already exists: {existing.username} ({existing.email})")
            choice = input("  Create another admin? (y/n): ").strip().lower()
            if choice != "y":
                return

        username = input("  Username: ").strip()
        email = input("  Email: ").strip()
        password = input("  Password: ").strip()

        if not username or not email or not password:
            print("❌ All fields are required")
            return

        # Check duplicates
        if session.query(User).filter(User.username == username).first():
            print(f"❌ Username '{username}' already exists")
            return
        if session.query(User).filter(User.email == email).first():
            print(f"❌ Email '{email}' already exists")
            return

        # Hash password — MUST match auth_service._hash_password() format
        # Format: (salt_32bytes + key_32bytes).hex()  — no colon separator
        salt = os.urandom(32)
        key = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, 100000
        )
        password_hash = (salt + key).hex()

        user = User(
            username=username,
            email=email,
            password_hash=password_hash,
            role="admin",
            is_active=True,
        )
        session.add(user)
        session.commit()
        print(f"✅ Admin user '{username}' created successfully!")

    except Exception as e:
        session.rollback()
        print(f"❌ Error: {e}")
    finally:
        session.close()


def seed_cameras():
    """Seed camera definitions from pathumwan_roads.json."""
    import json
    roads_path = os.path.join(Config.PROJECT_ROOT, "data", "pathumwan_roads.json")
    if not os.path.exists(roads_path):
        print("⚠ pathumwan_roads.json not found, skipping camera seed")
        return

    with open(roads_path, encoding="utf-8") as f:
        data = json.load(f)

    session = get_session()
    added = 0
    try:
        for cam in data.get("cameras", []):
            road_id = ensure_road(session, cam.get("road"))
            junction_id = canonical_junction_id(cam.get("sumo_tls_id"), cam.get("junction"))
            ensure_junction(
                session,
                junction_id,
                junction_name=cam.get("name") or cam.get("junction"),
                sumo_tls_id=cam.get("sumo_tls_id"),
                lat=cam.get("lat"),
                lng=cam.get("lng"),
            )
            existing = session.query(Camera).filter(
                Camera.camera_id == cam["id"]
            ).first()
            if not existing:
                row = Camera(
                    camera_id=cam["id"],
                    name=cam["name"],
                    road=cam["road"],
                    road_id=road_id,
                    lat=cam["lat"],
                    lng=cam["lng"],
                    junction=cam.get("junction", ""),
                    junction_id=junction_id,
                    sumo_tls_id=cam.get("sumo_tls_id", ""),
                    status=cam.get("status", "active"),
                )
                session.add(row)
                added += 1
            else:
                setattr(existing, "name", str(cam.get("name") or existing.name or cam["id"]))
                setattr(existing, "road", str(cam.get("road") or existing.road or ""))
                setattr(existing, "road_id", road_id)
                lat_value = float(cam.get("lat", 0) or 0.0)
                lng_value = float(cam.get("lng", 0) or 0.0)
                setattr(existing, "lat", lat_value)
                setattr(existing, "lng", lng_value)
                setattr(existing, "junction", str(cam.get("junction") or existing.junction or ""))
                setattr(existing, "junction_id", junction_id)
                setattr(existing, "sumo_tls_id", str(cam.get("sumo_tls_id") or existing.sumo_tls_id or ""))
                setattr(existing, "status", str(cam.get("status") or existing.status or "active"))
        session.commit()
        print(f"✅ Seeded {added} cameras ({len(data.get('cameras', []))} total)")
    except Exception as e:
        session.rollback()
        print(f"❌ Camera seed error: {e}")
    finally:
        session.close()


def main():
    def _redact_db_uri(uri: str) -> str:
        if uri.startswith("sqlite"):
            return uri

        try:
            parts = urlsplit(uri)
            if not parts.scheme or not parts.netloc:
                return "<set>"

            hostname = parts.hostname or ""
            if parts.port:
                hostname = f"{hostname}:{parts.port}"

            if parts.username:
                netloc = f"{parts.username}:***@{hostname}"
            else:
                netloc = hostname

            return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
        except Exception:
            return "<set>"

    print("=" * 60)
    print("  TraffixFlow — Database Initialization")
    print(f"  Database: {_redact_db_uri(Config.DATABASE_URI)}")
    print("=" * 60)

    if "--show" in sys.argv:
        show_tables()
        return

    # Create all tables
    print("\n📦 Creating tables...")
    init_db()

    # Seed cameras
    print("\n📷 Seeding cameras...")
    seed_cameras()

    # Show tables
    show_tables()

    # Create admin if requested
    if "--admin" in sys.argv:
        print("\n👤 Creating admin user...")
        create_admin()

    print("\n✅ Done! Check Neon Console → Tables to verify.")


if __name__ == "__main__":
    main()
