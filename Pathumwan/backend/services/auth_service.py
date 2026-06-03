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
from typing import Optional, Tuple

from sqlalchemy import or_, func
from database.connection import get_session
from database.models import User
from config import Config


def _hash_password(password):
    """Hash password using PBKDF2-HMAC-SHA256."""
    salt = os.urandom(32)
    key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100000)
    return (salt + key).hex()


def _verify_password(password: str, stored_hash: Optional[str]) -> Tuple[bool, Optional[str]]:
    """Verify password against stored hash.

    Supports the current format: (salt + key).hex() where key is PBKDF2-HMAC-SHA256.
    Also supports legacy variants where salt length or derived-key length differed.

    Returns (ok, error_code). error_code is only set for "unverifiable" hashes.
    """
    if not stored_hash:
        return False, "missing_hash"

    try:
        stored = bytes.fromhex(stored_hash)
    except Exception:
        # Not hex => we cannot verify with our PBKDF2 scheme.
        return False, "unsupported_hash_format"

    # We don't know salt length for legacy rows. Try common key lengths.
    if len(stored) < 16 + 16:
        return False, "invalid_hash_length"

    password_bytes = password.encode("utf-8")
    for key_len in (32, 64, 16):
        if len(stored) <= key_len:
            continue
        salt = stored[:-key_len]
        stored_key = stored[-key_len:]
        try:
            derived = hashlib.pbkdf2_hmac(
                "sha256",
                password_bytes,
                salt,
                100000,
                dklen=len(stored_key),
            )
        except Exception:
            continue
        if hmac.compare_digest(derived, stored_key):
            return True, None

    return False, None


def _normalize_identifier(value: str) -> str:
    return (value or "").strip()


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
    """Register a new user.

    Returns (success, message, token, user_info).
    """
    username = _normalize_identifier(username)
    email = _normalize_identifier(email).lower()
    password = password or ""

    if not username or not email or not password:
        return False, "กรุณากรอกข้อมูลให้ครบถ้วน", None, None
    if len(password) < 6:
        return False, "รหัสผ่านต้องมีอย่างน้อย 6 ตัวอักษร", None, None

    session = get_session()
    try:
        username_lower = username.lower()
        existing = session.query(User).filter(
            or_(func.lower(User.username) == username_lower, func.lower(User.email) == email)
        ).first()
        if existing:
            return False, "ชื่อผู้ใช้หรืออีเมลนี้ถูกใช้แล้ว", None, None

        password_hash = _hash_password(password)
        user = User(
            username=username,
            email=email,
            password_hash=password_hash,
            role="admin", # เปลี่ยนเป็น "admin" เพื่อให้มีสิทธิ์ใช้, beta ตอนจริงเป็น "user"
        )
        session.add(user)
        session.commit()

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
        return True, "ลงทะเบียนสำเร็จ", token, user_info
    except Exception as e:
        session.rollback()
        return False, f"เกิดข้อผิดพลาด: {str(e)}", None, None
    finally:
        session.close()


def login_user(username_or_email, password):
    """Login user. Returns (success, message, token, user_info)."""
    username_or_email = _normalize_identifier(username_or_email)
    password = password or ""

    if not username_or_email or not password:
        return False, "กรุณากรอกข้อมูลให้ครบถ้วน", None, None

    session = get_session()
    try:
        ident = username_or_email
        ident_lower = ident.lower()
        # Case-insensitive match for both username and email to avoid
        # confusing failures when users type different casing.
        user = session.query(User).filter(
            or_(func.lower(User.username) == ident_lower, func.lower(User.email) == ident_lower)
        ).first()

        if not user:
            return False, "ไม่พบบัญชีผู้ใช้", None, None

        if not user.is_active:
            return False, "บัญชีถูกระงับ", None, None

        ok, err = _verify_password(password, user.password_hash)
        if not ok:
            if err in ("unsupported_hash_format", "missing_hash", "invalid_hash_length"):
                return False, "บัญชีนี้ต้องตั้งรหัสผ่านใหม่ (ข้อมูลรหัสผ่านเดิมไม่รองรับ)", None, None
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
    username_or_email = _normalize_identifier(username_or_email)
    new_password = new_password or ""

    if len(new_password) < 6:
        return False, "รหัสผ่านต้องมีอย่างน้อย 6 ตัวอักษร"

    session = get_session()
    try:
        ident_lower = username_or_email.lower()
        user = session.query(User).filter(
            or_(func.lower(User.username) == ident_lower, func.lower(User.email) == ident_lower)
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
                if key == "email":
                    value = str(value).strip().lower()
                else:
                    value = str(value).strip()
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
