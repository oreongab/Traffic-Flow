"""
Auth Routes — Login, Register, Reset Password, Profile
"""

from flask import Blueprint, g, request, jsonify
from functools import wraps

from services.auth_service import (
    register_user, login_user, reset_password,
    get_user_from_token, update_user_profile, deactivate_user,
)

auth_bp = Blueprint("auth", __name__, url_prefix="/api/auth")


def require_auth(f):
    """Decorator: require valid JWT token."""
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return jsonify({"success": False, "message": "กรุณาเข้าสู่ระบบ"}), 401
        token = auth_header[7:]
        user = get_user_from_token(token)
        if not user:
            return jsonify({"success": False, "message": "Token ไม่ถูกต้องหรือหมดอายุ"}), 401
        g.current_user = user
        return f(*args, **kwargs)
    return decorated


def require_admin(f):
    """Decorator: require admin role."""
    @wraps(f)
    @require_auth
    def decorated(*args, **kwargs):
        current_user = getattr(g, "current_user", {})
        if not isinstance(current_user, dict) or current_user.get("role") != "admin":
            return jsonify({"success": False, "message": "ไม่มีสิทธิ์เข้าถึง"}), 403
        return f(*args, **kwargs)
    return decorated


@auth_bp.route("/register", methods=["POST"])
def api_register():
    data = request.get_json(silent=True) or {}
    username = data.get("username", "").strip()
    email = data.get("email", "").strip()
    password = data.get("password", "")

    success, message, token, user_info = register_user(username, email, password)
    if success:
        return jsonify({
            "success": True,
            "message": message,
            "token": token,
            "user": user_info,
        }), 201
    return jsonify({"success": False, "message": message, "error": message}), 400


@auth_bp.route("/login", methods=["POST"])
def api_login():
    data = request.get_json(silent=True) or {}
    username_or_email = data.get("username", "").strip() or data.get("email", "").strip()
    password = data.get("password", "")

    success, message, token, user_info = login_user(username_or_email, password)
    if success:
        return jsonify({"success": True, "message": message, "token": token, "user": user_info})
    return jsonify({"success": False, "message": message, "error": message}), 401


@auth_bp.route("/reset-password", methods=["POST"])
def api_reset_password():
    data = request.get_json(silent=True) or {}
    username_or_email = data.get("username", "").strip() or data.get("email", "").strip()
    new_password = data.get("new_password", "")

    success, message = reset_password(username_or_email, new_password)
    status = 200 if success else 400
    payload = {"success": success, "message": message}
    if not success:
        payload["error"] = message
    return jsonify(payload), status


@auth_bp.route("/me", methods=["GET"])
@require_auth
def api_me():
    return jsonify({"success": True, "user": getattr(g, "current_user", {})})


@auth_bp.route("/profile", methods=["PUT"])
@require_auth
def api_update_profile():
    data = request.get_json(silent=True) or {}
    current_user = getattr(g, "current_user", {})
    user_id = current_user.get("user_id") if isinstance(current_user, dict) else None
    success, message = update_user_profile(user_id, data)
    status = 200 if success else 400
    return jsonify({"success": success, "message": message}), status


@auth_bp.route("/deactivate", methods=["POST"])
@require_auth
def api_deactivate():
    current_user = getattr(g, "current_user", {})
    user_id = current_user.get("user_id") if isinstance(current_user, dict) else None
    success, message = deactivate_user(user_id)
    return jsonify({"success": success, "message": message})
