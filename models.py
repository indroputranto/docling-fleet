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

    # ── Rate limiting ─────────────────────────────────────────────────────────
    daily_request_limit = db.Column(db.Integer, nullable=False, default=0)
    # 0 = unlimited. When > 0, the chat API rejects requests once this
    # many requests have been logged for this client on the current UTC day.

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
            "daily_request_limit": self.daily_request_limit,
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


# ─────────────────────────────────────────────────────────────────────────────
# Document pipeline models
# ─────────────────────────────────────────────────────────────────────────────

class Document(db.Model):
    """
    Represents one uploaded source file (docx / pdf / xlsx).

    Lifecycle:
      draft      → file received, text extracted, chunks saved — awaiting review
      processing → save-to-Pinecone in progress
      active     → all chunks embedded and live in the vector store
      error      → embedding failed; error_message contains details
    """
    __tablename__ = "documents"

    id           = db.Column(db.Integer, primary_key=True)
    client_id    = db.Column(db.String(100), nullable=False, index=True)
    filename     = db.Column(db.String(500), nullable=False)
    file_type    = db.Column(db.String(10),  nullable=False)   # docx | pdf | xlsx
    status       = db.Column(db.String(20),  nullable=False, default="draft")
    chunk_count  = db.Column(db.Integer,     nullable=False, default=0)
    error_message = db.Column(db.Text, nullable=True)

    # Optional label grouping related files (e.g. "MV Industrial Ruby")
    group_name   = db.Column(db.String(500), nullable=True, index=True)

    uploaded_by  = db.Column(db.String(255), nullable=True)    # user email
    uploaded_at  = db.Column(db.DateTime,    nullable=False,
                             default=lambda: datetime.now(timezone.utc))
    activated_at = db.Column(db.DateTime,    nullable=True)    # when sent to Pinecone

    chunks = db.relationship(
        "DocumentChunk",
        backref="document",
        cascade="all, delete-orphan",
        order_by="DocumentChunk.position",
        lazy="dynamic",
    )

    def __repr__(self):
        return f"<Document {self.filename} client={self.client_id} status={self.status}>"


class DocumentChunk(db.Model):
    """
    One embeddable unit of text from a Document.

    The user reviews and edits these in the preview UI before committing
    them to Pinecone.  Each chunk maps to one Pinecone vector after save.
    """
    __tablename__ = "document_chunks"

    id          = db.Column(db.Integer, primary_key=True)
    document_id = db.Column(db.Integer, db.ForeignKey("documents.id",
                            ondelete="CASCADE"), nullable=False, index=True)
    position    = db.Column(db.Integer, nullable=False)   # ordering within document
    title       = db.Column(db.String(500), nullable=True)
    body        = db.Column(db.Text, nullable=False)
    pinecone_id = db.Column(db.String(300), nullable=True)
    # Format: "{client_id}:doc:{document_id}:chunk:{position}"
    # Populated after successful embedding; used for deletion.

    def vector_id(self) -> str:
        """Deterministic Pinecone vector ID for this chunk."""
        return f"{self.document.client_id}:doc:{self.document_id}:chunk:{self.position}"

    def __repr__(self):
        return f"<DocumentChunk doc={self.document_id} pos={self.position}>"


class UsageLog(db.Model):
    """
    One row per successful chat API request.

    Written synchronously after Claude returns a response so token counts
    are available.  The `date` column is a denormalised copy of the UTC date
    portion of `timestamp` — stored separately so daily aggregations can use
    a plain equality filter on an indexed column instead of a date-truncation
    expression (which SQLite can't index).

    Used for:
      - Per-client usage dashboards in the CMS
      - Rate limit enforcement (count today's rows before processing)
      - Future billing reconciliation
    """
    __tablename__ = "usage_logs"

    id           = db.Column(db.Integer, primary_key=True)
    client_id    = db.Column(db.String(100), nullable=False, index=True)
    user_email   = db.Column(db.String(255), nullable=True)
    # null when the request was made without a JWT (anonymous end-user)

    timestamp    = db.Column(db.DateTime, nullable=False,
                             default=lambda: datetime.now(timezone.utc))
    date         = db.Column(db.Date, nullable=False, index=True)
    # UTC date — indexed for fast daily counts and aggregations

    tokens_in    = db.Column(db.Integer, nullable=False, default=0)
    tokens_out   = db.Column(db.Integer, nullable=False, default=0)
    model        = db.Column(db.String(100), nullable=True)
    response_ms  = db.Column(db.Integer, nullable=True)
    # wall-clock milliseconds from request receipt to response sent

    def __repr__(self):
        return (f"<UsageLog client={self.client_id} date={self.date} "
                f"in={self.tokens_in} out={self.tokens_out}>")
