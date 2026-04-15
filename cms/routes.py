#!/usr/bin/env python3
"""
CMS Blueprint — client management dashboard.

Two-tier access model:
  admin        (platform operator) — sees ALL clients and users; can create/delete clients
  client_admin (client operator)   — sees ONLY their own client; can edit branding,
                                     Chat UX, and manage their own team's users

Routes:
  GET  /cms/                        → dashboard
  GET  /cms/clients/new             → create client form          [admin only]
  POST /cms/clients/new             → save new client             [admin only]
  GET  /cms/clients/<id>/edit       → edit client form            [admin + scoped client_admin]
  POST /cms/clients/<id>/edit       → save edits                  [admin + scoped client_admin]
  POST /cms/clients/<id>/toggle     → activate / deactivate       [admin only]
  POST /cms/clients/<id>/delete     → delete client               [admin only]

  GET  /cms/users/new               → create user form            [admin + client_admin]
  POST /cms/users/new               → save new user               [admin + client_admin]
  GET  /cms/users/<id>/edit         → edit user / reset password  [admin + scoped client_admin]
  POST /cms/users/<id>/edit         → save user edits             [admin + scoped client_admin]
  POST /cms/users/<id>/toggle       → activate / deactivate user  [admin + scoped client_admin]

  GET  /cms/analytics               → usage charts & top users    [admin + client_admin]

Login/logout are open (no auth required).
"""

import os
import json
import logging
from datetime import datetime, timezone, date as date_type
from functools import wraps
from flask import (
    Blueprint, render_template, redirect, url_for,
    request, flash, g, make_response, abort, jsonify
)
from models import db, ClientConfig, User, Vessel, Document, DocumentChunk, DossierSectionConfig
from documents.routes import DOCUMENT_SECTIONS

logger = logging.getLogger(__name__)

# ── Available LLM models with display metadata ────────────────────────────────
# Each entry: (value, label, tier_badge, description)
LLM_MODELS = [
    (
        "claude-haiku-4-5-20251001",
        "Claude Haiku 4.5",
        "Fast · Low cost",
        "Fastest responses, lowest cost. Great for straightforward Q&A and high-volume queries.",
    ),
    (
        "claude-sonnet-4-6",
        "Claude Sonnet 4.6",
        "Balanced · Mid cost",
        "Best balance of speed, quality, and cost. Recommended for most deployments.",
    ),
    (
        "claude-opus-4-6",
        "Claude Opus 4.6",
        "Powerful · High cost",
        "Most capable model. Best for complex reasoning, multi-step analysis, and nuanced answers.",
    ),
]

cms_bp = Blueprint(
    "cms",
    __name__,
    url_prefix="/cms",
    template_folder="templates",
)


@cms_bp.context_processor
def inject_cms_context():
    """Make cms_user and is_platform_admin available in every CMS template."""
    return dict(
        cms_user=getattr(g, "cms_user", None),
        is_platform_admin=getattr(g, "is_platform_admin", False),
    )


# ---------------------------------------------------------------------------
# Auth gate helpers
# ---------------------------------------------------------------------------

def _check_cms_cookie():
    """
    Validate the cms_token cookie.
    Returns decoded payload if role is 'admin' or 'client_admin', else None.
    """
    from auth import _decode_token
    import jwt
    token = request.cookies.get("cms_token")
    if not token:
        return None
    try:
        payload = _decode_token(token)
        if payload.get("role") not in ("admin", "client_admin"):
            return None
        return payload
    except jwt.PyJWTError:
        return None


def cms_required(f):
    """
    Page-level guard — allows admin AND client_admin.
    Sets on g:
      g.cms_user          — User model instance
      g.is_platform_admin — True if role == 'admin'
      g.scoped_client_id  — None for admin, client_id string for client_admin
    Redirects to /cms/login if not authenticated.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        payload = _check_cms_cookie()
        if not payload:
            return redirect(url_for("cms.login"))
        user = User.query.filter_by(email=payload["sub"], active=True).first()
        if not user:
            return redirect(url_for("cms.login"))
        g.cms_user          = user
        g.is_platform_admin = (user.role == "admin")
        g.scoped_client_id  = None if g.is_platform_admin else user.client_id
        return f(*args, **kwargs)
    return decorated


def platform_admin_required(f):
    """
    Stricter guard — platform admin only.
    Must be used AFTER @cms_required (or standalone — it calls cms_required internally).
    """
    @wraps(f)
    @cms_required
    def decorated(*args, **kwargs):
        if not g.is_platform_admin:
            flash("This action requires platform administrator access.", "error")
            return redirect(url_for("cms.dashboard"))
        return f(*args, **kwargs)
    return decorated


def _assert_client_access(client: ClientConfig):
    """
    For client_admin users: abort 403 if the client doesn't match their scope.
    No-op for platform admins.
    """
    if not g.is_platform_admin:
        if client.client_id != g.scoped_client_id:
            abort(403)


# ---------------------------------------------------------------------------
# Login / Logout
# ---------------------------------------------------------------------------

@cms_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        if _check_cms_cookie():
            return redirect(url_for("cms.dashboard"))
        return render_template("cms/login.html")

    email    = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")

    user = User.query.filter_by(email=email, active=True).first()
    if not user or not user.check_password(password):
        flash("Invalid credentials.", "error")
        return render_template("cms/login.html")

    if user.role not in ("admin", "client_admin"):
        flash("You don't have CMS access. Contact your administrator.", "error")
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
@cms_required
def dashboard():
    if g.is_platform_admin:
        # Platform admin: see all clients and all users
        clients = ClientConfig.query.order_by(ClientConfig.created_at.desc()).all()
        users   = User.query.order_by(User.created_at.desc()).all()
    else:
        # Client admin: see only their own client and its users
        clients = ClientConfig.query.filter_by(
            client_id=g.scoped_client_id
        ).all()
        users = User.query.filter_by(
            client_id=g.scoped_client_id
        ).order_by(User.created_at.desc()).all()

    # ── Usage stats ──────────────────────────────────────────────────────────
    from models import UsageLog
    from sqlalchemy import func

    today       = date_type.today()
    month_start = today.replace(day=1)

    # Per-client counts for today
    today_rows = (
        db.session.query(UsageLog.client_id, func.count(UsageLog.id))
        .filter(UsageLog.date == today)
        .group_by(UsageLog.client_id)
        .all()
    )
    today_by_client = {cid: cnt for cid, cnt in today_rows}

    # Per-client counts + tokens for this month
    month_rows = (
        db.session.query(
            UsageLog.client_id,
            func.count(UsageLog.id),
            func.coalesce(func.sum(UsageLog.tokens_in + UsageLog.tokens_out), 0),
        )
        .filter(UsageLog.date >= month_start)
        .group_by(UsageLog.client_id)
        .all()
    )
    month_by_client = {cid: (cnt, tok) for cid, cnt, tok in month_rows}

    # Build a per-client stats dict to pass to the template
    usage_stats = {}
    for c in clients:
        cid = c.client_id
        m_cnt, m_tok = month_by_client.get(cid, (0, 0))
        usage_stats[cid] = {
            "today":        today_by_client.get(cid, 0),
            "month":        m_cnt,
            "tokens_month": m_tok,
        }

    # Platform-level totals for the top stat cards
    total_today  = sum(v["today"]        for v in usage_stats.values())
    total_month  = sum(v["month"]        for v in usage_stats.values())
    total_tokens = sum(v["tokens_month"] for v in usage_stats.values())

    return render_template(
        "cms/dashboard.html",
        clients=clients,
        users=users,
        usage_stats=usage_stats,
        total_today=total_today,
        total_month=total_month,
        total_tokens=total_tokens,
        llm_models=LLM_MODELS,
    )


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------

@cms_bp.route("/analytics")
@cms_required
def analytics():
    from models import UsageLog
    from sqlalchemy import func
    from datetime import timedelta

    today       = date_type.today()
    days_30_ago = today - timedelta(days=29)
    month_start = today.replace(day=1)

    # Helper: apply client scope to a query
    def scoped(q):
        if not g.is_platform_admin:
            return q.filter(UsageLog.client_id == g.scoped_client_id)
        return q

    # ── Daily breakdown for the last 30 days ─────────────────────────────────
    daily_rows = scoped(
        db.session.query(
            UsageLog.date,
            func.count(UsageLog.id).label("requests"),
            func.coalesce(
                func.sum(UsageLog.tokens_in + UsageLog.tokens_out), 0
            ).label("tokens"),
            func.coalesce(func.avg(UsageLog.response_ms), 0).label("avg_ms"),
        )
        .filter(UsageLog.date >= days_30_ago)
        .group_by(UsageLog.date)
        .order_by(UsageLog.date)
    ).all()

    daily_map = {
        str(r.date): {"requests": r.requests, "tokens": r.tokens, "avg_ms": round(r.avg_ms)}
        for r in daily_rows
    }

    chart_labels, chart_requests, chart_tokens = [], [], []
    for i in range(30):
        d  = days_30_ago + timedelta(days=i)
        ds = str(d)
        chart_labels.append(d.strftime("%-d %b"))
        chart_requests.append(daily_map.get(ds, {}).get("requests", 0))
        chart_tokens.append(daily_map.get(ds, {}).get("tokens", 0))

    # ── Month-to-date summary totals ─────────────────────────────────────────
    totals = scoped(
        db.session.query(
            func.count(UsageLog.id).label("requests"),
            func.coalesce(func.sum(UsageLog.tokens_in),  0).label("tokens_in"),
            func.coalesce(func.sum(UsageLog.tokens_out), 0).label("tokens_out"),
            func.coalesce(func.avg(UsageLog.response_ms), 0).label("avg_ms"),
        )
        .filter(UsageLog.date >= month_start)
    ).first()

    # ── Top users this month ─────────────────────────────────────────────────
    top_users = (
        scoped(
            db.session.query(
                UsageLog.user_email,
                func.count(UsageLog.id).label("requests"),
                func.coalesce(func.sum(UsageLog.tokens_in + UsageLog.tokens_out), 0).label("tokens"),
                func.coalesce(func.avg(UsageLog.response_ms), 0).label("avg_ms"),
            )
            .filter(UsageLog.date >= month_start)
            .group_by(UsageLog.user_email)
            .order_by(func.count(UsageLog.id).desc())
        )
        .limit(10)
        .all()
    )

    # ── Per-client breakdown (platform admin only) ────────────────────────────
    client_chart_labels, client_chart_data = [], []
    if g.is_platform_admin:
        client_rows = (
            db.session.query(
                UsageLog.client_id,
                func.count(UsageLog.id).label("requests"),
            )
            .filter(UsageLog.date >= month_start)
            .group_by(UsageLog.client_id)
            .order_by(func.count(UsageLog.id).desc())
            .all()
        )
        client_chart_labels = [r.client_id for r in client_rows]
        client_chart_data   = [r.requests  for r in client_rows]

    return render_template(
        "cms/analytics.html",
        chart_labels=chart_labels,
        chart_requests=chart_requests,
        chart_tokens=chart_tokens,
        totals=totals,
        top_users=top_users,
        client_chart_labels=client_chart_labels,
        client_chart_data=client_chart_data,
        month_start=month_start,
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
@platform_admin_required        # only platform admins can create new clients
def client_new():
    if request.method == "GET":
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
            dossier_sections=DOCUMENT_SECTIONS,
            dossier_section_configs={},
        )
    return _save_client(client=None)


@cms_bp.route("/clients/<int:client_db_id>/set-model", methods=["POST"])
@cms_required
def client_set_model(client_db_id: int):
    """Quick-save the LLM model for a client.

    Platform admins can set the model for any client.
    Client admins can set the model for their own client only.
    """
    client = ClientConfig.query.get_or_404(client_db_id)

    # Access check: platform admin can touch any client; client_admin only their own
    if not g.is_platform_admin:
        if g.scoped_client_id != client.client_id:
            return jsonify({"ok": False, "error": "Forbidden"}), 403

    data   = request.get_json(force=True, silent=True) or {}
    model  = (data.get("llm_model") or "").strip()

    VALID_MODELS = {m for m, *_ in LLM_MODELS}
    if model not in VALID_MODELS:
        return jsonify({"ok": False, "error": f"Unknown model: {model}"}), 400

    client.llm_model  = model
    client.updated_at = datetime.now(timezone.utc)
    db.session.commit()
    logger.info(f"[cms] client '{client.client_id}' llm_model set to '{model}' by {g.cms_user.email}")
    return jsonify({"ok": True, "llm_model": model})


@cms_bp.route("/clients/<int:client_db_id>/edit", methods=["GET", "POST"])
@cms_required
def client_edit(client_db_id: int):
    client = ClientConfig.query.get_or_404(client_db_id)
    _assert_client_access(client)   # 403 if client_admin tries another client

    if request.method == "GET":
        section_configs = {
            cfg.slug: cfg
            for cfg in DossierSectionConfig.query.filter_by(client_id=client.client_id).all()
        }
        return render_template(
            "cms/client_form.html",
            client=client,
            default_prompt=client.system_prompt,
            suggested_questions_text=_suggested_questions_to_text(client),
            dossier_sections=DOCUMENT_SECTIONS,
            dossier_section_configs=section_configs,
            llm_models=LLM_MODELS,
        )
    return _save_client(client=client)


def _save_client(client: ClientConfig | None):
    """Shared create/update logic."""
    f = request.form

    client_id = f.get("client_id", "").strip().lower().replace(" ", "-")
    if not client_id:
        flash("Client ID is required.", "error")
        return redirect(request.url)

    if client is None:
        existing = ClientConfig.query.filter_by(client_id=client_id).first()
        if existing:
            flash(f"Client ID '{client_id}' already exists.", "error")
            return redirect(request.url)
        client = ClientConfig(client_id=client_id)
        db.session.add(client)

    # ── Fields editable by everyone with access ───────────────────────────────
    client.name             = f.get("name", "").strip()
    client.company_name     = f.get("company_name", "").strip()
    client.chatbot_name     = f.get("chatbot_name", "Fleet AI").strip()
    client.logo_url         = f.get("logo_url", "").strip() or None
    client.primary_color    = f.get("primary_color", "#1a1a2e").strip()
    client.secondary_color  = f.get("secondary_color", "#16213e").strip()
    client.accent_color     = f.get("accent_color", "#0f3460").strip()
    client.text_color       = f.get("text_color", "#ffffff").strip()
    client.font_family      = f.get("font_family", "Inter, sans-serif").strip()
    client.welcome_message  = f.get("welcome_message", "").strip() or None
    client.default_theme    = f.get("default_theme", "dark").strip()
    client.show_mode_toggle = "show_mode_toggle" in f

    raw_questions = f.get("suggested_questions", "").strip()
    if raw_questions:
        questions = [q.strip() for q in raw_questions.splitlines() if q.strip()]
        client.suggested_questions = json.dumps(questions)
    else:
        client.suggested_questions = None

    # ── AI model fields — editable by all CMS users for their own client ──────
    submitted_model = f.get("llm_model", "").strip()
    VALID_MODELS = {m for m, *_ in LLM_MODELS}
    if submitted_model in VALID_MODELS:
        client.llm_model = submitted_model
    client.max_context_chunks = int(f.get("max_context_chunks", 8) or 8)
    client.max_history        = int(f.get("max_history", 10) or 10)

    # ── Fields editable by platform admin only ────────────────────────────────
    if g.is_platform_admin:
        client.pinecone_index     = f.get("pinecone_index", "vessel-embeddings").strip()
        client.pinecone_namespace = f.get("pinecone_namespace", "").strip()
        client.embedding_model    = f.get("embedding_model", "text-embedding-3-small").strip()
        client.system_prompt      = f.get("system_prompt", "").strip()
        client.daily_request_limit = max(0, int(f.get("daily_request_limit", 0) or 0))
        client.active              = "active" in f

    client.updated_at = datetime.now(timezone.utc)

    # ── Dossier section config upsert ─────────────────────────────────────────
    # Only process on edit (not on initial create — client_id doesn't exist yet
    # for new clients, and the form doesn't show the section card for new clients).
    if client.client_id:
        existing_cfgs = {
            cfg.slug: cfg
            for cfg in DossierSectionConfig.query.filter_by(client_id=client.client_id).all()
        }
        for slug, _default_label in DOCUMENT_SECTIONS:
            custom_label = f.get(f"section_label_{slug}", "").strip() or None
            is_active    = f"section_active_{slug}" in f   # checkbox: present = on

            cfg = existing_cfgs.get(slug)
            # Only write a row if it differs from the defaults (label set OR hidden)
            if custom_label or not is_active:
                if cfg is None:
                    cfg = DossierSectionConfig(client_id=client.client_id, slug=slug)
                    db.session.add(cfg)
                cfg.label  = custom_label
                cfg.active = is_active
            elif cfg is not None:
                # Back to default — remove the override row to keep the table sparse
                db.session.delete(cfg)

    db.session.commit()
    flash(f"Client '{client.name}' saved successfully.", "success")
    return redirect(url_for("cms.dashboard"))


@cms_bp.route("/clients/<int:client_db_id>/toggle", methods=["POST"])
@platform_admin_required
def client_toggle(client_db_id: int):
    client = ClientConfig.query.get_or_404(client_db_id)
    client.active     = not client.active
    client.updated_at = datetime.now(timezone.utc)
    db.session.commit()
    status = "activated" if client.active else "deactivated"
    flash(f"Client '{client.name}' {status}.", "success")
    return redirect(url_for("cms.dashboard"))


@cms_bp.route("/clients/<int:client_db_id>/delete", methods=["POST"])
@platform_admin_required
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
@cms_required
def user_new():
    if request.method == "GET":
        if g.is_platform_admin:
            clients = ClientConfig.query.filter_by(active=True).all()
        else:
            # Client admin can only add users to their own client
            clients = ClientConfig.query.filter_by(
                client_id=g.scoped_client_id, active=True
            ).all()
        return render_template(
            "cms/user_form.html",
            user=None,
            clients=clients,
        )

    f         = request.form
    email     = f.get("email", "").strip().lower()
    password  = f.get("password", "").strip()
    client_id = f.get("client_id", "").strip() or None

    # client_admin can only create users for their own client
    if not g.is_platform_admin:
        client_id = g.scoped_client_id
        role = "user"   # client_admin cannot promote anyone to admin
    else:
        role = f.get("role", "user")

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


@cms_bp.route("/users/<int:user_id>/edit", methods=["GET", "POST"])
@cms_required
def user_edit(user_id: int):
    user = User.query.get_or_404(user_id)

    # Scope check: client_admin can only edit users in their own client
    if not g.is_platform_admin and user.client_id != g.scoped_client_id:
        abort(403)

    if request.method == "GET":
        if g.is_platform_admin:
            clients = ClientConfig.query.filter_by(active=True).all()
        else:
            clients = ClientConfig.query.filter_by(
                client_id=g.scoped_client_id, active=True
            ).all()
        return render_template(
            "cms/user_form.html",
            user=user,
            clients=clients,
        )

    f         = request.form
    new_email = f.get("email", "").strip().lower()
    new_pw    = f.get("password", "").strip()

    if not new_email:
        flash("Email is required.", "error")
        return redirect(request.url)

    # Check email uniqueness (allow same email = no change)
    existing = User.query.filter_by(email=new_email).first()
    if existing and existing.id != user.id:
        flash(f"Email '{new_email}' is already taken.", "error")
        return redirect(request.url)

    user.email = new_email

    # client_admin can't change roles or client assignments
    if g.is_platform_admin:
        user.role      = f.get("role", user.role)
        user.client_id = f.get("client_id", "").strip() or None

    # Only update password if a new one was provided
    if new_pw:
        user.set_password(new_pw)

    db.session.commit()
    flash(f"User '{user.email}' updated.", "success")
    return redirect(url_for("cms.dashboard"))


@cms_bp.route("/users/<int:user_id>/toggle", methods=["POST"])
@cms_required
def user_toggle(user_id: int):
    user = User.query.get_or_404(user_id)

    # client_admin can only toggle users within their own client
    if not g.is_platform_admin and user.client_id != g.scoped_client_id:
        abort(403)

    if user.email == g.cms_user.email:
        flash("You cannot deactivate your own account.", "error")
        return redirect(url_for("cms.dashboard"))

    user.active = not user.active
    db.session.commit()
    status = "activated" if user.active else "deactivated"
    flash(f"User '{user.email}' {status}.", "success")
    return redirect(url_for("cms.dashboard"))


# ─────────────────────────────────────────────────────────────────────────────
# Vessel Library
# ─────────────────────────────────────────────────────────────────────────────

def _resolve_vessel_client_id() -> str | None:
    """Same pattern as documents: scoped for client_admin, ?client= for admin."""
    if not g.is_platform_admin:
        return g.scoped_client_id
    return request.args.get("client") or None


def _assert_vessel_access(vessel: Vessel) -> None:
    if not g.is_platform_admin and vessel.client_id != g.scoped_client_id:
        abort(403)


def _vessel_summary(vessel: Vessel) -> dict:
    """
    Compute per-vessel metrics for the library table.

    Returns a dict with:
      docs          — all Document rows linked by group_name
      doc_count     — total number of documents
      active_count  — documents with status='active'
      draft_count   — documents with status='draft'
      total_chunks  — sum of chunk_count across all docs
      active_chunks — chunk_count of active docs only
      status_label  — "Live" | "Incomplete" | "Draft" | "Empty"
      last_updated  — most recent uploaded_at or activated_at across docs
    """
    from sqlalchemy import func

    docs = (
        Document.query
        .filter_by(vessel_id=vessel.id)
        .order_by(Document.uploaded_at.desc())
        .all()
    )

    doc_count    = len(docs)
    active_count = sum(1 for d in docs if d.status == "active")
    draft_count  = sum(1 for d in docs if d.status == "draft")
    total_chunks  = sum(d.chunk_count for d in docs)
    active_chunks = sum(d.chunk_count for d in docs if d.status == "active")

    if doc_count == 0:
        status_label = "Empty"
    elif active_count == doc_count:
        status_label = "Live"
    elif active_count > 0:
        status_label = "Incomplete"
    else:
        status_label = "Draft"

    timestamps = []
    for d in docs:
        if d.activated_at:
            timestamps.append(d.activated_at)
        elif d.uploaded_at:
            timestamps.append(d.uploaded_at)
    last_updated = max(timestamps) if timestamps else vessel.created_at

    return {
        "docs":          docs,
        "doc_count":     doc_count,
        "active_count":  active_count,
        "draft_count":   draft_count,
        "total_chunks":  total_chunks,
        "active_chunks": active_chunks,
        "status_label":  status_label,
        "last_updated":  last_updated,
    }


@cms_bp.route("/vessels")
@cms_required
def vessel_library():
    """Vessel Library — overview of all vessels for the active client."""
    active_client_id = _resolve_vessel_client_id()

    all_clients = []
    if g.is_platform_admin:
        all_clients = ClientConfig.query.order_by(ClientConfig.name).all()

    if active_client_id:
        vessels = (
            Vessel.query
            .filter_by(client_id=active_client_id)
            .order_by(Vessel.name)
            .all()
        )
        active_client = ClientConfig.query.filter_by(client_id=active_client_id).first()
    else:
        vessels = Vessel.query.order_by(Vessel.name).all()
        active_client = None

    # Build summary rows
    vessel_rows = []
    total_docs   = 0
    total_chunks = 0
    live_count   = 0
    pending_count = 0

    for v in vessels:
        summary = _vessel_summary(v)
        vessel_rows.append({"vessel": v, **summary})
        total_docs   += summary["doc_count"]
        total_chunks += summary["total_chunks"]
        if summary["status_label"] == "Live":
            live_count += 1
        if summary["draft_count"] > 0:
            pending_count += summary["draft_count"]

    metrics = {
        "total_vessels":  len(vessels),
        "live_vessels":   live_count,
        "total_docs":     total_docs,
        "total_chunks":   total_chunks,
        "pending_review": pending_count,
    }

    return render_template(
        "cms/vessel_library.html",
        vessel_rows=vessel_rows,
        metrics=metrics,
        all_clients=all_clients,
        active_client=active_client,
        active_client_id=active_client_id,
        auto_open_create=bool(request.args.get("add_vessel")),
    )


@cms_bp.route("/vessels/<int:vessel_id>", methods=["GET"])
@cms_required
def vessel_detail(vessel_id: int):
    """Return vessel JSON (used by the side drawer)."""
    from flask import jsonify
    vessel = Vessel.query.get_or_404(vessel_id)
    _assert_vessel_access(vessel)
    summary = _vessel_summary(vessel)
    data = vessel.to_dict()
    data["doc_count"]     = summary["doc_count"]
    data["active_count"]  = summary["active_count"]
    data["total_chunks"]  = summary["total_chunks"]
    data["status_label"]  = summary["status_label"]
    data["docs"] = [
        {
            "id":          d.id,
            "filename":    d.filename,
            "file_type":   d.file_type,
            "status":      d.status,
            "chunk_count": d.chunk_count,
            "uploaded_at": d.uploaded_at.isoformat() if d.uploaded_at else None,
        }
        for d in summary["docs"]
    ]
    return jsonify(data)


@cms_bp.route("/vessels/<int:vessel_id>/edit", methods=["POST"])
@cms_required
def vessel_edit(vessel_id: int):
    """Save edits to a vessel record (from the side drawer form)."""
    from flask import jsonify
    vessel = Vessel.query.get_or_404(vessel_id)
    _assert_vessel_access(vessel)

    f = request.get_json(silent=True) or request.form
    editable_fields = [
        "name", "imo_number", "call_sign", "flag_state",
        "port_of_registry", "vessel_type",
        "year_built", "gross_tonnage", "dwat", "loa", "notes",
    ]
    for field in editable_fields:
        if field in f:
            val = f[field]
            setattr(vessel, field, val.strip() if isinstance(val, str) and val.strip() else None)

    db.session.commit()
    return jsonify({"ok": True, "vessel": vessel.to_dict()})


@cms_bp.route("/vessels/create", methods=["POST"])
@cms_required
def vessel_create():
    """Manually create a new vessel record."""
    client_id = request.form.get("client_id") or _resolve_vessel_client_id()
    if not client_id:
        flash("Select a client first.", "error")
        return redirect(url_for("cms.vessel_library"))

    if not g.is_platform_admin and client_id != g.scoped_client_id:
        abort(403)

    name = request.form.get("name", "").strip()
    if not name:
        flash("Vessel name is required.", "error")
        return redirect(url_for("cms.vessel_library", client=client_id))

    existing = Vessel.query.filter_by(client_id=client_id, name=name).first()
    if existing:
        flash(f"A vessel named '{name}' already exists.", "error")
        return redirect(url_for("cms.vessel_library", client=client_id))

    vessel = Vessel(client_id=client_id, name=name)
    db.session.add(vessel)
    db.session.commit()
    flash(f"Vessel '{name}' created.", "success")
    return redirect(url_for("cms.vessel_library", client=client_id))


@cms_bp.route("/vessels/<int:vessel_id>/delete", methods=["POST"])
@cms_required
def vessel_delete(vessel_id: int):
    """Delete a vessel record (does NOT delete associated documents)."""
    vessel = Vessel.query.get_or_404(vessel_id)
    _assert_vessel_access(vessel)
    client_id = vessel.client_id
    name = vessel.name
    db.session.delete(vessel)
    db.session.commit()
    flash(f"Vessel '{name}' removed from the library.", "success")
    return redirect(url_for("cms.vessel_library", client=client_id))
