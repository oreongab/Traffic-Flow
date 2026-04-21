"""Fix existing admin password hash to match auth_service format."""
import sys
import os
import hashlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from database.connection import get_session
from database.models import User


def fix_admin_password():
    session = get_session()
    try:
        admin = session.query(User).filter(User.role == "admin").first()
        if not admin:
            print("No admin user found")
            return

        print(f"Fixing admin: {admin.username} ({admin.email})")
        password = input("Enter new password for admin: ").strip()
        if not password:
            print("Password cannot be empty")
            return

        # Hash using the same format as auth_service._hash_password()
        salt = os.urandom(32)
        key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100000)
        admin.password_hash = (salt + key).hex()
        session.commit()
        print(f"Admin password updated! Hash length: {len(admin.password_hash)}")
        print("You can now login with the new password.")
    except Exception as e:
        session.rollback()
        print(f"Error: {e}")
    finally:
        session.close()


if __name__ == "__main__":
    fix_admin_password()
