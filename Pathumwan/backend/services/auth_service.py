"""
Authentication Service — JWT + PBKDF2
"""

import hashlib
import hmac
import os
import time
import json
import base64
from datetime import datetime, timezone

from sqlalchemy import or_
from database.connection import get_session
from database.models import User
from config import Config


def _hash_password(password):
    """Hash password using PBKDF2-HMAC-SHA256."""
    salt = os.urandom(32)
    key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100000)
    return (salt + key).hex()


def _verify_password(password, stored_hash):
    """Verify password against stored hash."""
    stored = bytes.fromhex(stored_hash)
    salt = stored[:32]
    stored_key = stored[32:]
    key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100000)
    return hmac.compare_digest(key, stored_key)


def _create_jwt(payload):
    """Create a simple JWT token."""
    header = base64.urlsafe_b64encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode()).decode().rstrip("=")
    payload["exp"] = int(time.time()) + Config.JWT_ACCESS_TOKEN_EXPIRES
    payload["iat"] = int(time.time())
    payload_b64 = base64.urlsafe_b64encode(json.dumps(payload, default=str).encode()).decode().rstrip("=")
    message = f"{header}.{payload_b64}"
    sig = hmac.new(Config.JWT_SECRET_KEY.encode(), message.encode(), hashlib.sha256).digest()
    sig_b64 = base64.urlsafe_b64encode(sig).decode().rstrip("=")
    return f"{message}.{sig_b64}"


def _decode_jwt(token):
    """Decode and verify JWT token."""
    parts = token.split(".")
    if len(parts) != 3:
        return None
    header_b64, payload_b64, sig_b64 = parts
    message = f"{header_b64}.{payload_b64}"
    expected_sig = hmac.new(Config.JWT_SECRET_KEY.encode(), message.encode(), hashlib.sha256).digest()
    sig_b64_padded = sig_b64 + "=" * (4 - len(sig_b64) % 4)
    actual_sig = base64.urlsafe_b64decode(sig_b64_padded)
    if not hmac.compare_digest(expected_sig, actual_sig):
        return None
    payload_padded = payload_b64 + "=" * (4 - len(payload_b64) % 4)
    payload = json.loads(base64.urlsafe_b64decode(payload_padded))
    if payload.get("exp", 0) < time.time():
        return None
    return payload


def register_user(username, email, password):
    """Register a new user. Returns (success, message, token)."""
    if not username or not email or not password:
        return False, "กรุณากรอกข้อมูลให้ครบถ้วน", None
    if len(password) < 6:
        return False, "รหัสผ่านต้องมีอย่างน้อย 6 ตัวอักษร", None

    session = get_session()
    try:
        existing = session.query(User).filter(
            or_(User.username == username, User.email == email)
        ).first()
        if existing:
            return False, "ชื่อผู้ใช้หรืออีเมลนี้ถูกใช้แล้ว", None

        password_hash = _hash_password(password)
        user = User(
            username=username,
            email=email,
            password_hash=password_hash,
            role="user",
        )
        session.add(user)
        session.commit()

        token = _create_jwt({"user_id": str(user.id), "username": username, "role": "user"})
        return True, "ลงทะเบียนสำเร็จ", token
    except Exception as e:
        session.rollback()
        return False, f"เกิดข้อผิดพลาด: {str(e)}", None
    finally:
        session.close()


def login_user(username_or_email, password):
    """Login user. Returns (success, message, token, user_info)."""
    session = get_session()
    try:
        user = session.query(User).filter(
            or_(User.username == username_or_email, User.email == username_or_email)
        ).first()

        if not user:
            return False, "ไม่พบบัญชีผู้ใช้", None, None

        if not user.is_active:
            return False, "บัญชีถูกระงับ", None, None

        if not _verify_password(password, user.password_hash):
            return False, "รหัสผ่านไม่ถูกต้อง", None, None

        token = _create_jwt({
            "user_id": str(user.id),
            "username": user.username,
            "role": user.role,
        })

        user_info = {
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "role": user.role,
        }
        return True, "เข้าสู่ระบบสำเร็จ", token, user_info
    finally:
        session.close()


def reset_password(username_or_email, new_password):
    """Reset password. Returns (success, message)."""
    if len(new_password) < 6:
        return False, "รหัสผ่านต้องมีอย่างน้อย 6 ตัวอักษร"

    session = get_session()
    try:
        user = session.query(User).filter(
            or_(User.username == username_or_email, User.email == username_or_email)
        ).first()
        if not user:
            return False, "ไม่พบบัญชีผู้ใช้"

        user.password_hash = _hash_password(new_password)
        user.updated_at = datetime.now(timezone.utc)
        session.commit()
        return True, "เปลี่ยนรหัสผ่านสำเร็จ"
    except Exception as e:
        session.rollback()
        return False, f"เกิดข้อผิดพลาด: {str(e)}"
    finally:
        session.close()


def get_user_from_token(token):
    """Extract user info from JWT token."""
    payload = _decode_jwt(token)
    if not payload:
        return None
    return payload


def update_user_profile(user_id, updates):
    """Update user profile. Returns (success, message)."""
    session = get_session()
    try:
        user = session.query(User).filter(User.id == int(user_id)).first()
        if not user:
            return False, "ไม่พบบัญชีผู้ใช้"

        allowed = {"username", "email"}
        changed = False
        for key, value in updates.items():
            if key in allowed and value:
                setattr(user, key, value)
                changed = True

        if not changed:
            return False, "ไม่มีข้อมูลที่จะอัปเดต"

        user.updated_at = datetime.now(timezone.utc)
        session.commit()
        return True, "อัปเดตสำเร็จ"
    except Exception as e:
        session.rollback()
        return False, f"เกิดข้อผิดพลาด: {str(e)}"
    finally:
        session.close()


def deactivate_user(user_id):
    """Soft-delete user account."""
    session = get_session()
    try:
        user = session.query(User).filter(User.id == int(user_id)).first()
        if user:
            user.is_active = False
            user.updated_at = datetime.now(timezone.utc)
            session.commit()
        return True, "ยกเลิกบัญชีสำเร็จ"
    except Exception as e:
        session.rollback()
        return False, f"เกิดข้อผิดพลาด: {str(e)}"
    finally:
        session.close()
