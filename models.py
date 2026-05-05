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

    # Explicit FK to the Vessel this document belongs to.
    # Set by the user at upload time via the vessel dropdown.
    # When the vessel is deleted the column is set to NULL (document is retained).
    vessel_id    = db.Column(
        db.Integer,
        db.ForeignKey("vessels.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    vessel       = db.relationship("Vessel", back_populates="documents")

    # Legacy free-text group label kept for display / backward compat.
    # New uploads populate this from vessel.name automatically.
    group_name   = db.Column(db.String(500), nullable=True, index=True)

    # Structured document category — set explicitly by the user via the
    # Vessel Dossier upload UI.  One of the DOCUMENT_SECTIONS keys defined
    # in documents/routes.py  (e.g. "fixture_recap", "charter_party").
    # NULL for documents uploaded via the legacy library upload form.
    document_category = db.Column(db.String(100), nullable=True, index=True)

    uploaded_by  = db.Column(db.String(255), nullable=True)    # user email
    uploaded_at  = db.Column(db.DateTime,    nullable=False,
                             default=lambda: datetime.now(timezone.utc))
    activated_at = db.Column(db.DateTime,    nullable=True)    # when sent to Pinecone

    # Post-extraction coverage check results.
    # coverage_pct:   0-100 headline score (NULL = check not run / skipped).
    # coverage_notes: JSON string produced by documents/coverage.py with full
    #                 details (issues, warnings, missed headings, word counts).
    coverage_pct   = db.Column(db.Integer, nullable=True)
    coverage_notes = db.Column(db.Text,    nullable=True)

    # Object storage — the key (path) of the original uploaded file in
    # DigitalOcean Spaces, e.g. "documents/ocean7/fixture_recap_mv_aurora.pdf".
    # NULL when object storage is not configured or the upload was skipped.
    storage_key    = db.Column(db.String(1000), nullable=True)

    # When True, the extract → preview pipeline skips the GPT enrichment pass
    # (set at upload time; required for deferred processing so /preview can
    # invoke process-from-storage without losing the user preference).
    skip_ai_enrichment = db.Column(db.Boolean, nullable=False, default=False)

    # When True, the extractor runs the OCR pre-pass (ocrmypdf + Tesseract)
    # unconditionally — bypassing the image-PDF auto-detection. Use this for
    # scanned charter parties where the existing OCR text layer is poor:
    # garbled words, missing strikethroughs, OCR-mangled BIMCO text, etc.
    # On hosts without ocrmypdf installed (Vercel) this falls back gracefully.
    # Persisted on the row so the deferred /process-from-storage handler can
    # read it without losing the user's checkbox choice.
    force_reocr = db.Column(db.Boolean, nullable=False, default=False)

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
    # Format (new): {client}_{vessel}_{section_num}_{section}_{chunk_title}_{position}
    # Example:      test_client_mv_rot_3_fixture_recap_vessel_owners_2
    # Populated after successful embedding; used for deletion.

    # Section ordering mirrors DOCUMENT_SECTIONS in documents/routes.py.
    # Stored here so vector_id() is self-contained without circular imports.
    _SECTION_ORDER: dict = {
        "vessel_specifications":  1,
        "addendum":               2,
        "fixture_recap":          3,
        "charter_party":          4,
        "delivery_details":       5,
        "speed_consumption":      6,
        "inventory":              7,
        "lifting_equipment":      8,
        "hseq_documents":         9,
        "vessel_owners_details": 10,
    }

    @staticmethod
    def _slug(text: str, max_len: int = 40) -> str:
        """
        Convert arbitrary text to a clean snake_case slug suitable for use
        in a Pinecone vector ID.

        Steps:
          1. Strip a leading clause number ("1. " / "14 — ") so that chunk
             titles like "1. Vessel / Owners" become "vessel_owners" rather
             than "1_vessel_owners".
          2. Lowercase.
          3. Replace any run of non-alphanumeric characters with a single
             underscore.
          4. Strip leading/trailing underscores and truncate to max_len.
        """
        import re
        text = str(text or "")
        # Strip leading "1. " / "14 — " / "1.1 " clause prefixes
        text = re.sub(r'^\d+[\.\-—\s]+', '', text.strip())
        text = text.lower()
        text = re.sub(r'[^a-z0-9]+', '_', text)
        text = re.sub(r'_+', '_', text)
        return text.strip('_')[:max_len]

    def vector_id(self) -> str:
        """
        Deterministic, human-readable Pinecone vector ID.

        Format:
            {client}_{vessel}_{section_num}_{section}_{chunk_title}_{pos}

        Examples:
            test_client_mv_rot_3_fixture_recap_vessel_owners_2
            acme_mv_aurora_4_charter_party_hire_payment_1
        """
        doc          = self.document
        client_slug  = self._slug(doc.client_id or "unknown", max_len=30)
        vessel_name  = (doc.vessel.name if doc.vessel else None) or doc.group_name or "unknown"
        vessel_slug  = self._slug(vessel_name, max_len=30)
        category     = doc.document_category or "document"
        section_num  = self._SECTION_ORDER.get(category, 0)
        section_slug = self._slug(category, max_len=25)
        chunk_slug   = self._slug(self.title or f"chunk_{self.position}", max_len=40)
        position     = self.position + 1   # 1-indexed for human readability

        return (
            f"{client_slug}_{vessel_slug}_{section_num}_"
            f"{section_slug}_{chunk_slug}_{position}"
        )

    def __repr__(self):
        return f"<DocumentChunk doc={self.document_id} pos={self.position}>"


class Vessel(db.Model):
    """
    A named vessel entry in a client's fleet.

    Vessel records are created automatically when documents are uploaded with
    a group name, with metadata auto-extracted from the vessel spec chunks.
    They can also be created or edited manually through the Vessel Library CMS.

    The `name` field corresponds to Document.group_name — this is the join key
    that associates uploaded documents with their vessel record.

    Vessel type vocabulary (open-ended; common values used in the UI picker):
      MPP, Bulk Carrier, General Cargo, Container, Tanker, RoRo, Feeder
    """
    __tablename__ = "vessels"

    id               = db.Column(db.Integer, primary_key=True)
    client_id        = db.Column(db.String(100), nullable=False, index=True)
    name             = db.Column(db.String(500), nullable=False)
    # Matches Document.group_name — the vessel grouping label used on upload

    # ── Identity ─────────────────────────────────────────────────────────────
    imo_number       = db.Column(db.String(50),  nullable=True)
    call_sign        = db.Column(db.String(50),  nullable=True)
    flag_state       = db.Column(db.String(100), nullable=True)
    port_of_registry = db.Column(db.String(100), nullable=True)
    vessel_type      = db.Column(db.String(50),  nullable=True)
    # "MPP" | "Bulk Carrier" | "General Cargo" | "Container" | "Tanker" | "RoRo" | "Feeder" | …

    # ── Key specs (stored as strings to preserve original formatting) ─────────
    year_built       = db.Column(db.String(10),  nullable=True)
    gross_tonnage    = db.Column(db.String(50),  nullable=True)
    dwat             = db.Column(db.String(50),  nullable=True)
    loa              = db.Column(db.String(50),  nullable=True)

    # ── Free text ─────────────────────────────────────────────────────────────
    notes            = db.Column(db.Text, nullable=True)

    documents = db.relationship(
        "Document",
        back_populates="vessel",
        lazy="dynamic",
        foreign_keys="Document.vessel_id",
    )

    created_at = db.Column(db.DateTime, nullable=False,
                           default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, nullable=False,
                           default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "id":               self.id,
            "client_id":        self.client_id,
            "name":             self.name,
            "imo_number":       self.imo_number,
            "call_sign":        self.call_sign,
            "flag_state":       self.flag_state,
            "port_of_registry": self.port_of_registry,
            "vessel_type":      self.vessel_type,
            "year_built":       self.year_built,
            "gross_tonnage":    self.gross_tonnage,
            "dwat":             self.dwat,
            "loa":              self.loa,
            "notes":            self.notes,
            "created_at":       self.created_at.isoformat() if self.created_at else None,
            "updated_at":       self.updated_at.isoformat() if self.updated_at else None,
        }

    def __repr__(self):
        return f"<Vessel {self.name} imo={self.imo_number} client={self.client_id}>"


class DossierSectionConfig(db.Model):
    """
    Per-client customisation of the 10 fixed Vessel Dossier sections.

    Only rows that differ from the default need to exist — the dossier
    route falls back to the DOCUMENT_SECTIONS defaults for any slug that
    has no row here.  This keeps the table sparse: a client that never
    touches the dossier settings has zero rows.

    slug    — matches one of the DOCUMENT_SECTIONS keys in documents/routes.py
    label   — custom display name; None means use the default label
    active  — False hides the section from the dossier entirely
    """
    __tablename__ = "dossier_section_configs"

    id        = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(
        db.String(100),
        db.ForeignKey("client_configs.client_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    slug      = db.Column(db.String(50),  nullable=False)
    label     = db.Column(db.String(100), nullable=True)   # None → use default
    active    = db.Column(db.Boolean,     nullable=False, default=True)

    __table_args__ = (
        db.UniqueConstraint("client_id", "slug", name="uq_dossier_section_client_slug"),
    )

    def __repr__(self):
        return (f"<DossierSectionConfig client={self.client_id} "
                f"slug={self.slug} active={self.active}>")


class ChatSession(db.Model):
    """
    One conversation thread per user per client.

    Created automatically on the first message of a new chat.
    Labelled with the first user message (truncated to 60 chars).
    Scoped by user_email + client_id so switching clients or users
    shows only the relevant history.
    """
    __tablename__ = "chat_sessions"

    id              = db.Column(db.Integer, primary_key=True)
    user_email      = db.Column(db.String(255), nullable=False, index=True)
    client_id       = db.Column(db.String(100), nullable=False, index=True)
    label           = db.Column(db.String(500),  nullable=False, default="New chat")
    conversation_id = db.Column(db.String(100),  nullable=True)
    # The UUID echoed back by /api/chat — kept so the LLM context
    # thread can be resumed if needed in the future.

    created_at = db.Column(db.DateTime, nullable=False,
                           default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, nullable=False,
                           default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))

    messages = db.relationship(
        "ChatMessage",
        backref="session",
        cascade="all, delete-orphan",
        order_by="ChatMessage.position",
        lazy="dynamic",
    )

    def to_dict(self) -> dict:
        return {
            "id":              self.id,
            "label":           self.label,
            "conversation_id": self.conversation_id,
            "created_at":      self.created_at.isoformat() if self.created_at else None,
            "updated_at":      self.updated_at.isoformat() if self.updated_at else None,
        }

    def __repr__(self):
        return f"<ChatSession {self.id} user={self.user_email} client={self.client_id}>"


class ChatMessage(db.Model):
    """
    One user or assistant message within a ChatSession.
    """
    __tablename__ = "chat_messages"

    id         = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(
        db.Integer,
        db.ForeignKey("chat_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role       = db.Column(db.String(20),  nullable=False)   # "user" | "assistant"
    content    = db.Column(db.Text,        nullable=False)
    position   = db.Column(db.Integer,     nullable=False)   # 0-indexed ordering
    created_at = db.Column(db.DateTime, nullable=False,
                           default=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "role":    self.role,
            "content": self.content,
        }

    def __repr__(self):
        return f"<ChatMessage session={self.session_id} pos={self.position} role={self.role}>"


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
