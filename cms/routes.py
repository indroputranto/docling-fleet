#!/usr/bin/env python3
"""
CMS Blueprint — client management dashboard.

Routes:
  GET  /cms/              → dashboard (client list)
  GET  /cms/clients/new   → create client form
  POST /cms/clients/new   → save new client
  GET  /cms/clients/<id>/edit  → edit client form
  POST /cms/clients/<id>/edit  → save edits
  POST /cms/clients/<id>/toggle → activate / deactivate
  POST /cms/clients/<id>/delete → delete client

  GET  /cms/users         → user list
  GET  /cms/users/new     → create user form
  POST /cms/users/new     → save new user
  POST /cms/users/<id>/toggle → activate / deactivate user

All routes require admin role via the @require_admin decorator from auth.py,
EXCEPT the login/logout pages which are open.
"""

import os
import json
import logging
from datetime import datetime, timezone
from flask import (
    Blueprint, render_template, redirect, url_for,
    request, flash, g, make_response
)
from auth import require_admin
from models import db, ClientConfig, User

logger = logging.getLogger(__name__)

cms_bp = Blueprint(
    "cms",
    __name__,
    url_prefix="/cms",
    template_folder="templates",
)


# ---------------------------------------------------------------------------
# Auth gate — redirect to login if no valid token cookie
# ---------------------------------------------------------------------------

def _check_admin_cookie():
    """
    CMS pages use an httpOnly cookie (cms_token) for auth rather than
    Authorization headers, since these are browser-rendered pages.
    Returns the decoded payload or None.
    """
    from auth import _decode_token
    import jwt
    token = request.cookies.get("cms_token")
    if not token:
        return None
    try:
        payload = _decode_token(token)
        if payload.get("role") != "admin":
            return None
        return payload
    except jwt.PyJWTError:
        return None


def admin_required(f):
    """Page-level guard that redirects to /cms/login instead of returning 401."""
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        payload = _check_admin_cookie()
        if not payload:
            return redirect(url_for("cms.login"))
        g.cms_user = User.query.filter_by(
            email=payload["sub"], active=True
        ).first()
        if not g.cms_user:
            return redirect(url_for("cms.login"))
        return f(*args, **kwargs)
    return decorated


# ---------------------------------------------------------------------------
# Login / Logout
# ---------------------------------------------------------------------------

@cms_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        # Already logged in?
        if _check_admin_cookie():
            return redirect(url_for("cms.dashboard"))
        return render_template("cms/login.html")

    email    = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")

    user = User.query.filter_by(email=email, active=True).first()
    if not user or not user.check_password(password) or user.role != "admin":
        flash("Invalid credentials or insufficient permissions.", "error")
        return render_template("cms/login.html")

    from auth import _make_token, ACCESS_EXPIRES
    token = _make_token(user, ACCESS_EXPIRES)

    user.last_login_at = datetime.now(timezone.utc)
    db.session.commit()

    resp = make_response(redirect(url_for("cms.dashboard")))
    resp.set_cookie(
        "cms_token", token,
        httponly=True, samesite="Lax",
        max_age=int(ACCESS_EXPIRES.total_seconds()),
    )
    return resp


@cms_bp.route("/logout")
def logout():
    resp = make_response(redirect(url_for("cms.login")))
    resp.delete_cookie("cms_token")
    return resp


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@cms_bp.route("/")
@admin_required
def dashboard():
    clients = ClientConfig.query.order_by(ClientConfig.created_at.desc()).all()
    users   = User.query.order_by(User.created_at.desc()).all()
    return render_template(
        "cms/dashboard.html",
        clients=clients,
        users=users,
        cms_user=g.cms_user,
    )


# ---------------------------------------------------------------------------
# Client CRUD
# ---------------------------------------------------------------------------

def _suggested_questions_to_text(client: ClientConfig | None) -> str:
    """Convert the JSON-stored suggested_questions field to one-per-line text for the textarea."""
    if not client or not client.suggested_questions:
        return ""
    try:
        questions = json.loads(client.suggested_questions)
        return "\n".join(questions)
    except Exception:
        return ""


@cms_bp.route("/clients/new", methods=["GET", "POST"])
@admin_required
def client_new():
    if request.method == "GET":
        # Pre-load the default system prompt from prompt.md
        prompt_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "prompt.md"
        )
        default_prompt = ""
        if os.path.exists(prompt_path):
            with open(prompt_path, encoding="utf-8") as f:
                default_prompt = f.read()
        return render_template(
            "cms/client_form.html",
            client=None,
            default_prompt=default_prompt,
            suggested_questions_text="",
            cms_user=g.cms_user,
        )

    return _save_client(client=None)


@cms_bp.route("/clients/<int:client_db_id>/edit", methods=["GET", "POST"])
@admin_required
def client_edit(client_db_id: int):
    client = ClientConfig.query.get_or_404(client_db_id)
    if request.method == "GET":
        return render_template(
            "cms/client_form.html",
            client=client,
            default_prompt=client.system_prompt,
            suggested_questions_text=_suggested_questions_to_text(client),
            cms_user=g.cms_user,
        )
    return _save_client(client=client)


def _save_client(client: ClientConfig | None):
    """Shared create/update logic."""
    f = request.form

    client_id = f.get("client_id", "").strip().lower().replace(" ", "-")
    if not client_id:
        flash("Client ID is required.", "error")
        return redirect(request.url)

    # Duplicate check on create
    if client is None:
        existing = ClientConfig.query.filter_by(client_id=client_id).first()
        if existing:
            flash(f"Client ID '{client_id}' already exists.", "error")
            return redirect(request.url)
        client = ClientConfig(client_id=client_id)
        db.session.add(client)

    client.name               = f.get("name", "").strip()
    client.pinecone_index     = f.get("pinecone_index", "vessel-embeddings").strip()
    client.pinecone_namespace = f.get("pinecone_namespace", "").strip()
    client.embedding_model    = f.get("embedding_model", "text-embedding-3-small").strip()
    client.llm_model          = f.get("llm_model", "claude-opus-4-6").strip()
    client.system_prompt      = f.get("system_prompt", "").strip()
    client.max_context_chunks = int(f.get("max_context_chunks", 8))
    client.max_history        = int(f.get("max_history", 10))
    client.primary_color      = f.get("primary_color", "#1a1a2e").strip()
    client.secondary_color    = f.get("secondary_color", "#16213e").strip()
    client.accent_color       = f.get("accent_color", "#0f3460").strip()
    client.text_color         = f.get("text_color", "#ffffff").strip()
    client.logo_url           = f.get("logo_url", "").strip() or None
    client.company_name       = f.get("company_name", "").strip()
    client.chatbot_name       = f.get("chatbot_name", "Fleet AI").strip()
    client.font_family        = f.get("font_family", "Inter, sans-serif").strip()
    client.active             = "active" in f  # checkbox

    # ── Chat UX ──────────────────────────────────────────────────────────────
    client.welcome_message = f.get("welcome_message", "").strip() or None

    # Suggested questions: textarea → one per line → JSON array
    raw_questions = f.get("suggested_questions", "").strip()
    if raw_questions:
        questions = [q.strip() for q in raw_questions.splitlines() if q.strip()]
        client.suggested_questions = json.dumps(questions)
    else:
        client.suggested_questions = None

    client.default_theme    = f.get("default_theme", "dark").strip()
    client.show_mode_toggle = "show_mode_toggle" in f  # checkbox

    client.updated_at = datetime.now(timezone.utc)

    db.session.commit()
    flash(f"Client '{client.name}' saved successfully.", "success")
    return redirect(url_for("cms.dashboard"))


@cms_bp.route("/clients/<int:client_db_id>/toggle", methods=["POST"])
@admin_required
def client_toggle(client_db_id: int):
    client = ClientConfig.query.get_or_404(client_db_id)
    client.active     = not client.active
    client.updated_at = datetime.now(timezone.utc)
    db.session.commit()
    status = "activated" if client.active else "deactivated"
    flash(f"Client '{client.name}' {status}.", "success")
    return redirect(url_for("cms.dashboard"))


@cms_bp.route("/clients/<int:client_db_id>/delete", methods=["POST"])
@admin_required
def client_delete(client_db_id: int):
    client = ClientConfig.query.get_or_404(client_db_id)
    name = client.name
    db.session.delete(client)
    db.session.commit()
    flash(f"Client '{name}' deleted.", "success")
    return redirect(url_for("cms.dashboard"))


# ---------------------------------------------------------------------------
# User management
# ---------------------------------------------------------------------------

@cms_bp.route("/users/new", methods=["GET", "POST"])
@admin_required
def user_new():
    if request.method == "GET":
        clients = ClientConfig.query.filter_by(active=True).all()
        return render_template(
            "cms/user_form.html",
            user=None,
            clients=clients,
            cms_user=g.cms_user,
        )

    f        = request.form
    email    = f.get("email", "").strip().lower()
    password = f.get("password", "").strip()
    role     = f.get("role", "user")
    client_id = f.get("client_id", "").strip() or None

    if not email or not password:
        flash("Email and password are required.", "error")
        return redirect(request.url)

    if User.query.filter_by(email=email).first():
        flash(f"Email '{email}' already exists.", "error")
        return redirect(request.url)

    user = User(email=email, role=role, client_id=client_id)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    flash(f"User '{email}' created.", "success")
    return redirect(url_for("cms.dashboard"))


@cms_bp.route("/users/<int:user_id>/toggle", methods=["POST"])
@admin_required
def user_toggle(user_id: int):
    user = User.query.get_or_404(user_id)
    if user.email == g.cms_user.email:
        flash("You cannot deactivate your own account.", "error")
        return redirect(url_for("cms.dashboard"))
    user.active = not user.active
    db.session.commit()
    status = "activated" if user.active else "deactivated"
    flash(f"User '{user.email}' {status}.", "success")
    return redirect(url_for("cms.dashboard"))
