"""Reset a user's password in the configured database.

Usage:
  python reset_user_password.py --identifier user@example.com --password NewPass123

Notes:
- Uses the same PBKDF2 format as services/auth_service.py.
- Intended for development / recovery when you have a user row but can't log in.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import or_, func

from database.connection import get_session
from database.models import User
from services.auth_service import _hash_password


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Reset a user's password")
    parser.add_argument(
        "--identifier",
        required=True,
        help="Username or email (case-insensitive)",
    )
    parser.add_argument(
        "--password",
        required=True,
        help="New password (min 6 chars recommended)",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    identifier = (args.identifier or "").strip()
    new_password = args.password or ""

    if not identifier or not new_password:
        print("ERROR: --identifier and --password are required")
        return 2

    session = get_session()
    try:
        ident_lower = identifier.lower()
        user = session.query(User).filter(
            or_(func.lower(User.username) == ident_lower, func.lower(User.email) == ident_lower)
        ).first()

        if not user:
            print("ERROR: user not found")
            return 1

        user.password_hash = _hash_password(new_password)
        user.is_active = True
        session.commit()

        print(f"OK: password reset for user id={user.id} username={user.username} email={user.email}")
        return 0
    except Exception as e:
        session.rollback()
        print(f"ERROR: {e}")
        return 1
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
