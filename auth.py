"""
Auth module - JWT helpers + login_required decorator
"""
import jwt
import os
from datetime import datetime, timedelta, timezone
from functools import wraps
from flask import request, jsonify, redirect, url_for, make_response, current_app

JWT_SECRET = os.environ.get("JWT_SECRET", "raabbar-super-secret-key-change-in-prod")
JWT_ALGO = "HS256"
JWT_EXP_HOURS = 24


def generate_token(user_id: int, username: str) -> str:
    payload = {
        "sub": str(user_id),
        "username": username,
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + timedelta(hours=JWT_EXP_HOURS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGO)


def decode_token(token: str):
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGO])
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


def get_token_from_request():
    """Extract JWT from cookie or Authorization header."""
    token = request.cookies.get("auth_token")
    if not token:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth[7:]
    return token


def get_current_user():
    """Return User object if valid JWT, else None."""
    from models.user import User
    token = get_token_from_request()
    if not token:
        return None
    payload = decode_token(token)
    if not payload:
        return None
    try:
        uid = int(payload["sub"])
    except (KeyError, ValueError):
        return None
    return User.query.get(uid)


def login_required(view):
    """Decorator: redirect to login if not authenticated (HTML routes)."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = get_current_user()
        if not user:
            if request.path.startswith("/api/"):
                return jsonify({"success": False, "error": "Unauthorized"}), 401
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def set_auth_cookie(response, token):
    """Set HttpOnly cookie for JWT."""
    response.set_cookie(
        "auth_token",
        token,
        max_age=JWT_EXP_HOURS * 3600,
        httponly=True,
        samesite="Lax",
        path="/",
    )


def clear_auth_cookie(response):
    response.delete_cookie("auth_token", path="/")