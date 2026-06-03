"""Database smoke test.

Useful for verifying a Neon PostgreSQL (or SQLite) connection end-to-end:
- can connect
- can create tables
- can run queries
- (optional) can write a row

Usage examples:
  NEON_DATABASE_URI='postgresql://...?...sslmode=require' python smoke_db.py
  DATABASE_URI='postgresql://...?...sslmode=require' python smoke_db.py --write
"""

from __future__ import annotations

import argparse
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import text

from config import Config
from database.connection import get_engine, get_session, init_db


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


def main() -> int:
    parser = argparse.ArgumentParser(description="TraffixFlow DB smoke test")
    parser.add_argument("--no-init", action="store_true", help="Skip creating tables")
    parser.add_argument("--no-counts", action="store_true", help="Skip printing row counts")
    parser.add_argument(
        "--write",
        action="store_true",
        help="Write+delete a SystemLog row to verify write access",
    )
    args = parser.parse_args()

    uri = Config.DATABASE_URI
    print(f"DATABASE_URI = {_redact_db_uri(uri)}")

    engine = get_engine()
    print(f"dialect = {engine.dialect.name}")

    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        print("connectivity = OK")
    except Exception as exc:
        print(f"connectivity = FAILED: {exc}")
        return 2

    if not args.no_init:
        try:
            init_db()
        except Exception as exc:
            print(f"init_db = FAILED: {exc}")
            return 3
        else:
            print("init_db = OK")

    if args.write:
        try:
            from database.models import SystemLog

            session = get_session()
            row = SystemLog(event_type="smoke_db", details={"message": "smoke_db write test"})
            session.add(row)
            session.commit()
            session.delete(row)
            session.commit()
        except Exception as exc:
            print(f"write_test = FAILED: {exc}")
            try:
                session.rollback()
            except Exception:
                pass
            return 4
        else:
            print("write_test = OK")

    if not args.no_counts:
        try:
            from check_tables import check

            check()
        except Exception as exc:
            print(f"counts = FAILED: {exc}")
            return 5

    print("smoke_db = OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
