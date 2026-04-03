#!/usr/bin/env python3
"""
Auth Blueprint — shared across Chat and CMS.

Endpoints:
  POST /auth/login    → validate credentials, return access + refresh tokens
  POST /auth/refresh  → issue new access token from refresh token
  POST /auth/logout   → client-side only (tokens are stateless); endpoint
                        exists for future server-side blocklist support
  GET  /auth/me       → return current user info from token

Token structure:
  {
    "sub":       "user@email.com",
    "role":      "admin" | "user",
    "client_id": "acme" | null,
    "exp":       <unix timestamp>
  }

Usage in other blueprints:
  from auth import require_auth, require_admin, get_current_user
"""

import os
import jwt
import logging
from datetime import datetime, timezone, timedelta
from functools import wraps
from flask import Blueprint, request, jsonify, g
from models import db, User

logger = logging.getLogger(__name__)

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
JWT_SECRET      = os.getenv("JWT_SECRET", "change-me-in-production")
ACCESS_EXPIRES  = timedelta(hours=8)
REFRESH_EXPIRES = timedelta(days=30)
ALGORITHM       = "HS256"


# ---------------------------------------------------------------------------
# Token helpers
# ---------------------------------------------------------------------------

def _make_token(user: User, expires_in: timedelta) -> str:
    payload = {
        "sub":       user.email,
        "role":      user.role,
        "client_id": user.client_id,
        "exp":       datetime.now(timezone.utc) + expires_in,
        "iat":       datetime.now(timezone.utc),
        "type":      "access" if expires_in == ACCESS_EXPIRES else "refresh",
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=ALGORITHM)


def _decode_token(token: str) -> dict:
    """Decode and validate a JWT. Raises jwt.PyJWTError on failure."""
    return jwt.decode(token, JWT_SECRET, algorithms=[ALGORITHM])


def _token_from_request() -> str | None:
    """Extract Bearer token from Authorization header."""
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        return header[7:]
    return None


# ---------------------------------------------------------------------------
# Auth decorators — import and use these in other blueprints
# ---------------------------------------------------------------------------

def require_auth(f):
    """
    Decorator: requires a valid access token.
    Sets g.current_user (User model instance) and g.token_payload (dict).
    Returns 401 if token is missing, invalid, or expired.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        token = _token_from_request()
        if not token:
            return jsonify({"error": "Authentication required"}), 401
        try:
            payload = _decode_token(token)
        except jwt.ExpiredSignatureError:
            return jsonify({"error": "Token expired"}), 401
        except jwt.PyJWTError:
            return jsonify({"error": "Invalid token"}), 401

        user = User.query.filter_by(email=payload["sub"], active=True).first()
        if not user:
            return jsonify({"error": "User not found or inactive"}), 401

        g.current_user  = user
        g.token_payload = payload
        return f(*args, **kwargs)
    return decorated


def require_admin(f):
    """
    Decorator: requires a valid token AND role == 'admin'.
    Must be applied after @require_auth (or stacked — order matters).
    """
    @wraps(f)
    @require_auth
    def decorated(*args, **kwargs):
        if g.current_user.role != "admin":
            return jsonify({"error": "Admin access required"}), 403
        return f(*args, **kwargs)
    return decorated


def get_current_user() -> User | None:
    """Return the current authenticated user, or None if not authenticated."""
    return getattr(g, "current_user", None)


def get_current_token() -> dict | None:
    """Return the decoded token payload, or None."""
    return getattr(g, "token_payload", None)


def optional_auth(f):
    """
    Decorator: tries to authenticate but doesn't reject unauthenticated requests.
    Sets g.current_user and g.token_payload if token is valid, otherwise None.
    Useful for endpoints that behave differently for logged-in users.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        g.current_user  = None
        g.token_payload = None
        token = _token_from_request()
        if token:
            try:
                payload = _decode_token(token)
                user = User.query.filter_by(email=payload["sub"], active=True).first()
                if user:
                    g.current_user  = user
                    g.token_payload = payload
            except jwt.PyJWTError:
                pass
        return f(*args, **kwargs)
    return decorated


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@auth_bp.route("/login", methods=["POST"])
def login():
    """
    POST /auth/login
    Body: { "email": "...", "password": "..." }
    Returns: { "access_token": "...", "refresh_token": "...", "user": {...} }
    """
    body = request.get_json(silent=True) or {}
    email    = (body.get("email") or "").strip().lower()
    password = body.get("password") or ""

    if not email or not password:
        return jsonify({"error": "Email and password are required"}), 400

    user = User.query.filter_by(email=email, active=True).first()
    if not user or not user.check_password(password):
        logger.warning(f"[auth] Failed login attempt for: {email}")
        return jsonify({"error": "Invalid email or password"}), 401

    # Update last login timestamp
    user.last_login_at = datetime.now(timezone.utc)
    db.session.commit()

    access_token  = _make_token(user, ACCESS_EXPIRES)
    refresh_token = _make_token(user, REFRESH_EXPIRES)

    logger.info(f"[auth] Login success: {email} role={user.role}")

    return jsonify({
        "access_token":  access_token,
        "refresh_token": refresh_token,
        "expires_in":    int(ACCESS_EXPIRES.total_seconds()),
        "user":          user.to_dict(),
    })


@auth_bp.route("/refresh", methods=["POST"])
def refresh():
    """
    POST /auth/refresh
    Body: { "refresh_token": "..." }
    Returns: { "access_token": "..." }
    """
    body = request.get_json(silent=True) or {}
    token = body.get("refresh_token") or ""

    if not token:
        return jsonify({"error": "Refresh token required"}), 400

    try:
        payload = _decode_token(token)
    except jwt.ExpiredSignatureError:
        return jsonify({"error": "Refresh token expired, please log in again"}), 401
    except jwt.PyJWTError:
        return jsonify({"error": "Invalid refresh token"}), 401

    if payload.get("type") != "refresh":
        return jsonify({"error": "Not a refresh token"}), 400

    user = User.query.filter_by(email=payload["sub"], active=True).first()
    if not user:
        return jsonify({"error": "User not found or inactive"}), 401

    access_token = _make_token(user, ACCESS_EXPIRES)
    return jsonify({
        "access_token": access_token,
        "expires_in":   int(ACCESS_EXPIRES.total_seconds()),
    })


@auth_bp.route("/logout", methods=["POST"])
def logout():
    """
    POST /auth/logout
    Stateless — client should discard tokens.
    Endpoint exists for future server-side token blocklist support.
    """
    return jsonify({"message": "Logged out successfully"})


@auth_bp.route("/me", methods=["GET"])
@require_auth
def me():
    """
    GET /auth/me
    Returns the current user's profile.
    """
    return jsonify(g.current_user.to_dict())
