"""Copy application users from Neon into the local Docker Postgres database."""

from __future__ import annotations

import argparse
import os

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from config import Config
from database.connection import init_db
from database.models import User


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sync users from Neon PostgreSQL into the current target database.",
    )
    parser.add_argument(
        "--source-uri",
        default=os.getenv("NEON_DATABASE_URI") or "",
        help="Source PostgreSQL URI. Defaults to NEON_DATABASE_URI.",
    )
    parser.add_argument(
        "--target-uri",
        default=Config.DATABASE_URI,
        help="Target PostgreSQL URI. Defaults to Config.DATABASE_URI.",
    )
    return parser.parse_args()


def _upsert_users(source_uri: str, target_uri: str) -> tuple[int, int]:
    source_engine = create_engine(source_uri, future=True)
    target_engine = create_engine(target_uri, future=True)

    inserted = 0
    updated = 0

    with Session(source_engine) as source_session, Session(target_engine) as target_session:
        source_users = source_session.execute(select(User).order_by(User.id.asc())).scalars().all()

        for source_user in source_users:
            target_user = target_session.execute(
                select(User).where(
                    (User.username == source_user.username) | (User.email == source_user.email)
                )
            ).scalar_one_or_none()

            if target_user is None:
                target_user = User(
                    username=source_user.username,
                    email=source_user.email,
                    password_hash=source_user.password_hash,
                    role=source_user.role,
                    is_active=source_user.is_active,
                    created_at=source_user.created_at,
                    updated_at=source_user.updated_at,
                )
                target_session.add(target_user)
                inserted += 1
                continue

            target_user.username = source_user.username
            target_user.email = source_user.email
            target_user.password_hash = source_user.password_hash
            target_user.role = source_user.role
            target_user.is_active = source_user.is_active
            target_user.created_at = source_user.created_at
            target_user.updated_at = source_user.updated_at
            updated += 1

        target_session.commit()

    return inserted, updated


def main() -> int:
    args = _parse_args()
    if not args.source_uri:
        raise SystemExit("NEON_DATABASE_URI is required. Set it in backend/.env or pass --source-uri")

    init_db()
    inserted, updated = _upsert_users(args.source_uri, args.target_uri)
    print(f"Synced users from Neon -> target DB (inserted={inserted}, updated={updated})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())