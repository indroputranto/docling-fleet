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

    # ── Cargo-pipeline hold geometry ─────────────────────────────────────────
    # JSON-serialized list of hold dicts that the cargo visualizer + packer
    # consume.  Schema (per element):
    #   {id, length, breadth, height,
    #    has_tween, lower_height, upper_height,
    #    estimated, tween_estimated}
    # Source-of-truth on Vercel and any environment without the legacy
    # output/vessels/<slug>/<slug>_data.json files on disk.  When a vessel
    # has filesystem hold data, cargo.holds.get_holds_for_vessel() will
    # parse it on first read and cache the result here automatically.
    # NULL when no hold data has been set yet — the cargo upload flow will
    # surface a "set holds first" prompt.
    holds_json           = db.Column(db.Text,  nullable=True)

    # Total volumetric capacity across all holds (m³).  Used by the cargo
    # visualizer's stats bar and the packer's weight-target heuristic.
    hold_capacity_m3     = db.Column(db.Float, nullable=True)

    # Double-bottom height in meters (the structural void below the tank
    # top).  Defaults to 1.5m when null — matches the historical default
    # used by the side-view renderer.
    double_bottom_height = db.Column(db.Float, nullable=True)

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


# ─────────────────────────────────────────────────────────────────────────────
# Cargo / Packing-list pipeline models
#
# These tables are entirely self-contained.  They do NOT modify any existing
# table (documents, document_chunks, vessels, …) and they do NOT import from
# the documents/ pipeline.  Vessel-side relationship attributes are attached
# via SQLAlchemy `backref` so the Vessel class above is left untouched.
#
# Schema layout:
#   vessel_trips        — optional voyage container; one vessel has many trips
#   cargo_manifests     — one uploaded packing list; belongs to vessel & (opt) trip
#   cargo_items         — one row of the spreadsheet; child of manifest
#   cargo_placements    — packer output: where each item ended up (or unplaced)
# ─────────────────────────────────────────────────────────────────────────────

class VesselTrip(db.Model):
    """
    A scheduled or completed voyage of a vessel.

    Trips are an optional grouping concept for cargo manifests — a single
    trip can carry one active packing list at a time.  When trip_id is left
    NULL on a manifest, the "one active per vessel" rule is used as a
    fallback so the feature works before the trip UI is built.

    Vessel-side accessor: `vessel.trips`  (added via backref below).
    """
    __tablename__ = "vessel_trips"

    id              = db.Column(db.Integer, primary_key=True)
    client_id       = db.Column(db.String(100), nullable=False, index=True)

    vessel_id       = db.Column(
        db.Integer,
        db.ForeignKey("vessels.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    vessel          = db.relationship(
        "Vessel",
        backref=db.backref("trips", lazy="dynamic", cascade="all, delete-orphan"),
    )

    label           = db.Column(db.String(500), nullable=False, default="Untitled trip")
    departure_port  = db.Column(db.String(255), nullable=True)
    arrival_port    = db.Column(db.String(255), nullable=True)
    departure_date  = db.Column(db.Date, nullable=True)
    arrival_date    = db.Column(db.Date, nullable=True)

    # planned | in_progress | completed | cancelled
    status          = db.Column(db.String(20),  nullable=False, default="planned")

    notes           = db.Column(db.Text, nullable=True)

    created_at      = db.Column(db.DateTime, nullable=False,
                                default=lambda: datetime.now(timezone.utc))
    updated_at      = db.Column(db.DateTime, nullable=False,
                                default=lambda: datetime.now(timezone.utc),
                                onupdate=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "id":             self.id,
            "client_id":      self.client_id,
            "vessel_id":      self.vessel_id,
            "label":          self.label,
            "departure_port": self.departure_port,
            "arrival_port":   self.arrival_port,
            "departure_date": self.departure_date.isoformat() if self.departure_date else None,
            "arrival_date":   self.arrival_date.isoformat()   if self.arrival_date   else None,
            "status":         self.status,
            "notes":          self.notes,
            "created_at":     self.created_at.isoformat() if self.created_at else None,
            "updated_at":     self.updated_at.isoformat() if self.updated_at else None,
        }

    def __repr__(self):
        return (f"<VesselTrip {self.label} vessel={self.vessel_id} "
                f"status={self.status}>")


class CargoManifest(db.Model):
    """
    One uploaded packing list for a vessel (and optionally a specific trip).

    Lifecycle:
      draft     → file uploaded + parsed; user is reviewing items in /preview
      active    → committed; packer has run; rendered in the cargo visualizer
      archived  → superseded by a newer active manifest for the same trip/vessel

    "One active per vessel trip" rule is enforced in the route layer when the
    manifest is moved to `active`: any prior active manifest with the same
    (trip_id) — or same (vessel_id) when trip_id is NULL — is flipped to
    `archived` first.

    `layout_json` caches the full packer output so the visualizer can render
    in one query.  Shape (also returned by the API):
        {
          "placements":     [ {item_id, hold_id, level, x, y, z, rotation, ...}, ... ],
          "unplaced":       [ {item_id, reason}, ... ],
          "weight_per_hold": { "1": 12345.6, "2": 9876.5 },
          "fill_pct_per_hold": { "1": 78.4, "2": 61.2 },
          "balance_score":  87.5,
          "generated_at":   "2026-05-06T..."
        }

    Vessel-side accessor: `vessel.cargo_manifests`  (added via backref below).
    Trip-side  accessor:  `trip.cargo_manifests`    (added via backref below).
    """
    __tablename__ = "cargo_manifests"

    id              = db.Column(db.Integer, primary_key=True)
    client_id       = db.Column(db.String(100), nullable=False, index=True)

    vessel_id       = db.Column(
        db.Integer,
        db.ForeignKey("vessels.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    vessel          = db.relationship(
        "Vessel",
        backref=db.backref("cargo_manifests", lazy="dynamic", cascade="all, delete-orphan"),
    )

    trip_id         = db.Column(
        db.Integer,
        db.ForeignKey("vessel_trips.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    trip            = db.relationship(
        "VesselTrip",
        backref=db.backref("cargo_manifests", lazy="dynamic"),
    )

    # ── File ────────────────────────────────────────────────────────────────
    filename        = db.Column(db.String(500), nullable=False)
    file_type       = db.Column(db.String(10),  nullable=False)   # xlsx | xls
    storage_key     = db.Column(db.String(1000), nullable=True)   # DO Spaces

    # ── Status ──────────────────────────────────────────────────────────────
    status          = db.Column(db.String(20),  nullable=False, default="draft")
    error_message   = db.Column(db.Text, nullable=True)

    # ── Voyage metadata (mirrored from trip when one is set, else freeform) ─
    voyage_label    = db.Column(db.String(500), nullable=True)
    departure_port  = db.Column(db.String(255), nullable=True)
    arrival_port    = db.Column(db.String(255), nullable=True)
    departure_date  = db.Column(db.Date, nullable=True)

    # ── Aggregates (populated after parse + pack) ───────────────────────────
    total_items      = db.Column(db.Integer, nullable=False, default=0)
    total_weight_kg  = db.Column(db.Float,   nullable=False, default=0.0)
    total_volume_m3  = db.Column(db.Float,   nullable=False, default=0.0)
    placed_count     = db.Column(db.Integer, nullable=False, default=0)
    unplaced_count   = db.Column(db.Integer, nullable=False, default=0)
    balance_score    = db.Column(db.Float,   nullable=True)   # 0-100, higher = better

    # ── Cached packer output (full layout JSON for the 3D viewer) ───────────
    layout_json      = db.Column(db.Text, nullable=True)

    # ── Audit ───────────────────────────────────────────────────────────────
    uploaded_by      = db.Column(db.String(255), nullable=True)
    uploaded_at      = db.Column(db.DateTime, nullable=False,
                                 default=lambda: datetime.now(timezone.utc))
    packed_at        = db.Column(db.DateTime, nullable=True)
    updated_at       = db.Column(db.DateTime, nullable=False,
                                 default=lambda: datetime.now(timezone.utc),
                                 onupdate=lambda: datetime.now(timezone.utc))

    items = db.relationship(
        "CargoItem",
        backref="manifest",
        cascade="all, delete-orphan",
        order_by="CargoItem.position",
        lazy="dynamic",
    )

    placements = db.relationship(
        "CargoPlacement",
        backref="manifest",
        cascade="all, delete-orphan",
        lazy="dynamic",
    )

    def to_dict(self, include_items: bool = False) -> dict:
        d = {
            "id":               self.id,
            "client_id":        self.client_id,
            "vessel_id":        self.vessel_id,
            "trip_id":          self.trip_id,
            "filename":         self.filename,
            "file_type":        self.file_type,
            "storage_key":      self.storage_key,
            "status":           self.status,
            "error_message":    self.error_message,
            "voyage_label":     self.voyage_label,
            "departure_port":   self.departure_port,
            "arrival_port":     self.arrival_port,
            "departure_date":   self.departure_date.isoformat() if self.departure_date else None,
            "total_items":      self.total_items,
            "total_weight_kg":  self.total_weight_kg,
            "total_volume_m3":  self.total_volume_m3,
            "placed_count":     self.placed_count,
            "unplaced_count":   self.unplaced_count,
            "balance_score":    self.balance_score,
            "uploaded_by":      self.uploaded_by,
            "uploaded_at":      self.uploaded_at.isoformat() if self.uploaded_at else None,
            "packed_at":        self.packed_at.isoformat() if self.packed_at else None,
            "updated_at":       self.updated_at.isoformat() if self.updated_at else None,
        }
        if include_items:
            d["items"] = [it.to_dict() for it in self.items]
        return d

    def __repr__(self):
        return (f"<CargoManifest {self.id} vessel={self.vessel_id} "
                f"trip={self.trip_id} status={self.status} "
                f"items={self.total_items}>")


class CargoItem(db.Model):
    """
    One row from the parsed packing-list spreadsheet.

    Dimensional values are normalized to METERS, weights to KILOGRAMS,
    volume to CUBIC METERS, regardless of the source file's units.

    `raw_row_json` preserves the original spreadsheet row as a dict so
    later features can surface fields we don't currently model
    (destination, customs, expiry dates, etc.) without re-parsing.
    """
    __tablename__ = "cargo_items"

    id              = db.Column(db.Integer, primary_key=True)
    manifest_id     = db.Column(
        db.Integer,
        db.ForeignKey("cargo_manifests.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    position        = db.Column(db.Integer, nullable=False)         # order in source file
    item_id         = db.Column(db.String(255), nullable=False)     # PL N° / NR. PLIST
    description     = db.Column(db.Text, nullable=True)
    packing_type    = db.Column(db.String(100), nullable=True)

    # ── Geometry (meters) ───────────────────────────────────────────────────
    length_m        = db.Column(db.Float, nullable=False, default=0.0)
    width_m         = db.Column(db.Float, nullable=False, default=0.0)
    height_m        = db.Column(db.Float, nullable=False, default=0.0)
    volume_m3       = db.Column(db.Float, nullable=False, default=0.0)

    # ── Weight (kilograms) ─────────────────────────────────────────────────
    net_weight_kg   = db.Column(db.Float, nullable=True)
    gross_weight_kg = db.Column(db.Float, nullable=False, default=0.0)

    # ── Flags ──────────────────────────────────────────────────────────────
    imo_flag                 = db.Column(db.Boolean, nullable=False, default=False)
    can_stack                = db.Column(db.Boolean, nullable=False, default=True)
    can_rotate_horizontal    = db.Column(db.Boolean, nullable=False, default=True)

    # ── Visual / extra ─────────────────────────────────────────────────────
    color_hex       = db.Column(db.String(20), nullable=True)
    raw_row_json    = db.Column(db.Text, nullable=True)

    created_at      = db.Column(db.DateTime, nullable=False,
                                default=lambda: datetime.now(timezone.utc))

    placement = db.relationship(
        "CargoPlacement",
        backref="item",
        uselist=False,
        cascade="all, delete-orphan",
    )

    def to_dict(self) -> dict:
        return {
            "id":                    self.id,
            "manifest_id":           self.manifest_id,
            "position":              self.position,
            "item_id":               self.item_id,
            "description":           self.description,
            "packing_type":          self.packing_type,
            "length_m":              self.length_m,
            "width_m":               self.width_m,
            "height_m":              self.height_m,
            "volume_m3":             self.volume_m3,
            "net_weight_kg":         self.net_weight_kg,
            "gross_weight_kg":       self.gross_weight_kg,
            "imo_flag":              self.imo_flag,
            "can_stack":             self.can_stack,
            "can_rotate_horizontal": self.can_rotate_horizontal,
            "color_hex":             self.color_hex,
        }

    def __repr__(self):
        return (f"<CargoItem {self.item_id} {self.length_m}x{self.width_m}x"
                f"{self.height_m}m {self.gross_weight_kg}kg>")


class CargoPlacement(db.Model):
    """
    Where the packer chose to put one CargoItem.

    is_placed=False means the item didn't fit anywhere; hold_id is NULL
    and `unplaced_reason` carries the diagnostic ("too tall for any hold",
    "exceeds remaining weight", "no extreme point fits", …).

    Coordinates are relative to the hold origin:
      x = forward (along ship length, +X is bow)
      y = up      (vertical, +Y is up)
      z = port    (athwart, +Z is port)
    Units are meters.
    """
    __tablename__ = "cargo_placements"

    id              = db.Column(db.Integer, primary_key=True)
    manifest_id     = db.Column(
        db.Integer,
        db.ForeignKey("cargo_manifests.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    item_id         = db.Column(
        db.Integer,
        db.ForeignKey("cargo_items.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,        # one placement per item
        index=True,
    )

    is_placed       = db.Column(db.Boolean, nullable=False, default=False)
    hold_id         = db.Column(db.Integer, nullable=True)         # 1, 2, 3 …
    level           = db.Column(db.String(20), nullable=True)      # lower | tween | None

    x_m             = db.Column(db.Float, nullable=True)
    y_m             = db.Column(db.Float, nullable=True)
    z_m             = db.Column(db.Float, nullable=True)
    rotation_deg    = db.Column(db.Integer, nullable=False, default=0)   # 0 | 90

    # Manual override flag — when True, the joint packer treats this
    # placement as a fixed obstacle on repack instead of recomputing it.
    # Set by the manual-move UI; cleared by the unpin endpoint or by
    # discarding / re-uploading the manifest.
    is_pinned       = db.Column(
        db.Boolean, nullable=False, default=False, server_default="0"
    )

    unplaced_reason = db.Column(db.String(255), nullable=True)

    def to_dict(self) -> dict:
        return {
            "id":              self.id,
            "manifest_id":     self.manifest_id,
            "item_id":         self.item_id,
            "is_placed":       self.is_placed,
            "hold_id":         self.hold_id,
            "level":           self.level,
            "x_m":             self.x_m,
            "y_m":             self.y_m,
            "z_m":             self.z_m,
            "rotation_deg":    self.rotation_deg,
            "is_pinned":       bool(self.is_pinned),
            "unplaced_reason": self.unplaced_reason,
        }

    def __repr__(self):
        if self.is_placed:
            return (f"<CargoPlacement item={self.item_id} hold={self.hold_id} "
                    f"level={self.level} rot={self.rotation_deg}>")
        return f"<CargoPlacement item={self.item_id} UNPLACED ({self.unplaced_reason})>"
