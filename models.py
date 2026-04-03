#!/usr/bin/env python3
"""
Database models for the platform.
Uses SQLAlchemy with SQLite for Phase 2 (dev).
Swap DATABASE_URL in .env to postgres:// for production.

Tables:
  - users         Login credentials + role + client assignment
  - client_configs  All per-client settings (replaces hardcoded dict in client_config.py)
"""

from datetime import datetime, timezone
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()


class User(db.Model):
    __tablename__ = "users"

    id            = db.Column(db.Integer, primary_key=True)
    email         = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role          = db.Column(db.String(50), nullable=False, default="user")
    # "admin"        — platform operator; full CMS access, sees all clients
    # "client_admin" — client operator; scoped CMS access, sees own client only
    # "user"         — end user; chat only, no CMS access

    client_id     = db.Column(db.String(100), nullable=True)
    # null for platform admins (they see everything)
    # required for client_admin and user roles

    active        = db.Column(db.Boolean, nullable=False, default=True)
    created_at    = db.Column(db.DateTime, nullable=False,
                              default=lambda: datetime.now(timezone.utc))
    last_login_at = db.Column(db.DateTime, nullable=True)

    def set_password(self, password: str):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    def to_dict(self) -> dict:
        return {
            "id":         self.id,
            "email":      self.email,
            "role":       self.role,
            "client_id":  self.client_id,
            "active":     self.active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def __repr__(self):
        return f"<User {self.email} role={self.role}>"


class ClientConfig(db.Model):
    __tablename__ = "client_configs"

    id         = db.Column(db.Integer, primary_key=True)
    client_id  = db.Column(db.String(100), unique=True, nullable=False, index=True)
    # slug used in subdomain routing, e.g. "acme" → acme.platform.com

    # ── Identity ────────────────────────────────────────────────────────────
    name       = db.Column(db.String(255), nullable=False)

    # ── Pinecone ─────────────────────────────────────────────────────────────
    pinecone_index     = db.Column(db.String(255), nullable=False,
                                   default="vessel-embeddings")
    pinecone_namespace = db.Column(db.String(255), nullable=False, default="")

    # ── AI models ────────────────────────────────────────────────────────────
    embedding_model = db.Column(db.String(100), nullable=False,
                                default="text-embedding-3-small")
    llm_model       = db.Column(db.String(100), nullable=False,
                                default="claude-opus-4-6")

    # ── Prompt ───────────────────────────────────────────────────────────────
    system_prompt = db.Column(db.Text, nullable=False, default="")

    # ── RAG tuning ───────────────────────────────────────────────────────────
    max_context_chunks = db.Column(db.Integer, nullable=False, default=8)
    max_history        = db.Column(db.Integer, nullable=False, default=10)

    # ── Branding / theme ─────────────────────────────────────────────────────
    primary_color   = db.Column(db.String(20),  nullable=False, default="#1a1a2e")
    secondary_color = db.Column(db.String(20),  nullable=False, default="#16213e")
    accent_color    = db.Column(db.String(20),  nullable=False, default="#0f3460")
    text_color      = db.Column(db.String(20),  nullable=False, default="#ffffff")
    font_family     = db.Column(db.String(100), nullable=False,
                                default="Inter, sans-serif")
    logo_url        = db.Column(db.String(500), nullable=True)
    company_name    = db.Column(db.String(255), nullable=False, default="")
    chatbot_name    = db.Column(db.String(255), nullable=False, default="Fleet AI")

    # ── Chat UX ──────────────────────────────────────────────────────────────
    welcome_message     = db.Column(db.Text, nullable=True)
    # First message shown as an assistant bubble when a new chat starts.

    suggested_questions = db.Column(db.Text, nullable=True)
    # JSON array of strings, e.g. '["What is the vessel IMO?", "Show trading limits"]'
    # Displayed as clickable chips in the empty state.

    default_theme = db.Column(db.String(10), nullable=False, default="dark")
    # "dark" or "light" — the mode the chat UI opens in.

    show_mode_toggle = db.Column(db.Boolean, nullable=False, default=True)
    # Whether to show the dark/light toggle button to end users.

    # ── Status ───────────────────────────────────────────────────────────────
    active     = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False,
                           default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, nullable=False,
                           default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))

    def to_config_dict(self) -> dict:
        """Return a dict matching the shape expected by chat_routes.py."""
        import json
        try:
            questions = json.loads(self.suggested_questions) if self.suggested_questions else []
        except Exception:
            questions = []
        return {
            "client_id":          self.client_id,
            "name":               self.name,
            "pinecone_index":     self.pinecone_index,
            "pinecone_namespace": self.pinecone_namespace,
            "embedding_model":    self.embedding_model,
            "llm_model":          self.llm_model,
            "system_prompt":      self.system_prompt,
            "max_context_chunks": self.max_context_chunks,
            "max_history":        self.max_history,
            "welcome_message":    self.welcome_message or "",
            "suggested_questions": questions,
            "theme": {
                "primary_color":    self.primary_color,
                "secondary_color":  self.secondary_color,
                "accent_color":     self.accent_color,
                "text_color":       self.text_color,
                "font_family":      self.font_family,
                "logo_url":         self.logo_url,
                "company_name":     self.company_name,
                "chatbot_name":     self.chatbot_name,
                "default_theme":    self.default_theme,
                "show_mode_toggle": self.show_mode_toggle,
            },
            "active": self.active,
        }

    def to_dict(self) -> dict:
        """Full dict for the CMS API."""
        d = self.to_config_dict()
        d["id"]         = self.id
        d["created_at"] = self.created_at.isoformat() if self.created_at else None
        d["updated_at"] = self.updated_at.isoformat() if self.updated_at else None
        return d

    def __repr__(self):
        return f"<ClientConfig {self.client_id} active={self.active}>"
