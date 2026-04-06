#!/usr/bin/env python3
"""
Documents Blueprint — document upload, preview/edit, and knowledge-base management.

Access: Platform Admin and Client Admin only (same cms_token cookie).
Platform Admins can switch client context via ?client= query param.
Client Admins are scoped to their own client automatically.

Routes:
  GET  /documents/                    → document library (history)
  POST /documents/upload              → accept file, extract, redirect to preview
  GET  /documents/<id>/preview        → editable chunk cards
  POST /documents/<id>/save           → embed chunks + upsert to Pinecone → active
  POST /documents/<id>/delete         → delete from Pinecone + DB
"""

import os
import logging
from datetime import datetime, timezone
from functools import wraps

from flask import (
    Blueprint, render_template, redirect, url_for,
    request, flash, g, abort, current_app
)
from werkzeug.utils import secure_filename

from models import db, ClientConfig, Document, DocumentChunk, Vessel

logger = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = {"docx", "pdf", "xlsx"}

documents_bp = Blueprint(
    "documents",
    __name__,
    url_prefix="/documents",
    template_folder="templates",
)


# ─────────────────────────────────────────────────────────────────────────────
# Template context — mirrors what cms/routes.py passes in every render call,
# but wired once via a context processor so no route can forget it.
# ─────────────────────────────────────────────────────────────────────────────

@documents_bp.context_processor
def inject_cms_context():
    """Make cms_user and is_platform_admin available in all documents templates."""
    return dict(
        cms_user=getattr(g, "cms_user", None),
        is_platform_admin=getattr(g, "is_platform_admin", False),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Auth gate (mirrors cms_required from cms/routes.py)
# ─────────────────────────────────────────────────────────────────────────────

def _check_cms_cookie():
    from auth import _decode_token
    import jwt
    token = request.cookies.get("cms_token")
    if not token:
        return None
    try:
        payload = _decode_token(token)
        if payload.get("type") != "access":
            return None
        if payload.get("role") not in ("admin", "client_admin"):
            return None
        return payload
    except jwt.ExpiredSignatureError:
        return None
    except Exception:
        return None


def documents_required(f):
    """Require a valid CMS session (admin or client_admin)."""
    @wraps(f)
    def decorated(*args, **kwargs):
        from models import User
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


def _resolve_client_id() -> str | None:
    """
    Determine the active client for the current request.

    - Client admin: always their own client.
    - Platform admin: ?client= query param, or None (shows all).
    """
    if not g.is_platform_admin:
        return g.scoped_client_id
    return request.args.get("client") or None


def _assert_doc_access(doc: Document) -> None:
    """Abort 403 if the current user cannot access this document."""
    if not g.is_platform_admin:
        if doc.client_id != g.scoped_client_id:
            abort(403)


def _allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


# ─────────────────────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────────────────────

@documents_bp.route("/")
@documents_required
def library():
    """Document library — shows all documents for the active client."""
    active_client_id = _resolve_client_id()

    # For platform admins: fetch all clients for the switcher dropdown
    all_clients = []
    if g.is_platform_admin:
        all_clients = ClientConfig.query.order_by(ClientConfig.name).all()

    # Fetch documents
    if active_client_id:
        docs = (
            Document.query
            .filter_by(client_id=active_client_id)
            .order_by(Document.uploaded_at.desc())
            .all()
        )
        active_client = ClientConfig.query.filter_by(client_id=active_client_id).first()
    else:
        # Platform admin, no client selected → show all
        docs = Document.query.order_by(Document.uploaded_at.desc()).all()
        active_client = None

    # Vessels for the upload dropdown — scoped to the active client
    vessels = []
    if active_client_id:
        vessels = (
            Vessel.query
            .filter_by(client_id=active_client_id)
            .order_by(Vessel.name)
            .all()
        )

    return render_template(
        "documents/library.html",
        docs=docs,
        all_clients=all_clients,
        active_client=active_client,
        active_client_id=active_client_id,
        vessels=vessels,
    )


@documents_bp.route("/upload", methods=["POST"])
@documents_required
def upload():
    """
    Accept one or more file uploads, extract text from each, and redirect to
    the preview of the first file.  Remaining files are passed as a comma-
    separated ?queue= of doc IDs so the user reviews them sequentially.
    """
    from documents.extractor import extract

    # Determine target client
    client_id = request.form.get("client_id") or _resolve_client_id()
    if not client_id:
        flash("Please select a client before uploading.", "error")
        return redirect(url_for("documents.library"))

    if not g.is_platform_admin and client_id != g.scoped_client_id:
        abort(403)

    client = ClientConfig.query.filter_by(client_id=client_id).first()
    if not client:
        flash("Client not found.", "error")
        return redirect(url_for("documents.library"))

    # Resolve selected vessel (optional — documents may be unassigned)
    vessel_id  = request.form.get("vessel_id", "").strip() or None
    vessel     = None
    group_name = None
    if vessel_id:
        vessel = Vessel.query.filter_by(id=vessel_id, client_id=client_id).first()
        if not vessel:
            flash("Selected vessel not found.", "error")
            return redirect(url_for("documents.library", client=client_id))
        group_name = vessel.name   # keep for display / backward compat

    files = [f for f in request.files.getlist("files") if f and f.filename]

    if not files:
        flash("No files selected.", "error")
        return redirect(url_for("documents.library", client=client_id))

    doc_ids = []

    for file in files:
        filename = secure_filename(file.filename)

        if not _allowed_file(filename):
            flash(f"'{file.filename}' skipped — unsupported type (use .docx, .pdf, .xlsx).", "error")
            continue

        ext = filename.rsplit(".", 1)[1].lower()

        try:
            raw_chunks = extract(file.stream, filename)
        except Exception as e:
            flash(f"'{filename}' skipped — could not extract text: {e}", "error")
            logger.error(f"[documents] Extraction error for {filename}: {e}", exc_info=True)
            continue

        if not raw_chunks or not any(c["body"].strip() for c in raw_chunks):
            flash(f"'{filename}' skipped — no text found (scanned image PDF?).", "error")
            continue

        # ── AI enrichment pass ───────────────────────────────────────────────
        # Sends baseline chunks to gpt-4o-mini for title assignment and
        # clause-level splitting. Falls back to raw_chunks on any failure.
        try:
            from documents.ai_enrichment import enrich_chunks
            raw_chunks = enrich_chunks(
                raw_chunks,
                filename,
                vessel_name=vessel.name if vessel else None,
            )
        except Exception as ae:
            logger.warning(
                f"[documents] AI enrichment failed for '{filename}', "
                f"using raw extraction: {ae}"
            )
        # ────────────────────────────────────────────────────────────────────

        doc = Document(
            client_id=client_id,
            filename=filename,
            file_type=ext,
            status="draft",
            uploaded_by=g.cms_user.email,
            chunk_count=len(raw_chunks),
            vessel_id=int(vessel_id) if vessel_id else None,
            group_name=group_name,
        )
        db.session.add(doc)
        db.session.flush()

        for pos, chunk in enumerate(raw_chunks):
            db.session.add(DocumentChunk(
                document_id=doc.id,
                position=pos,
                title=chunk.get("title") or "",
                body=chunk["body"],
            ))

        db.session.commit()
        doc_ids.append(doc.id)
        logger.info(
            f"[documents] Uploaded '{filename}' → doc {doc.id} "
            f"({len(raw_chunks)} chunks) for client '{client_id}'"
            + (f" vessel='{group_name}'" if group_name else "")
        )

        # ── Auto-fill vessel metadata from spec sheet chunks ─────────────────
        # Only updates the pre-existing Vessel record — never creates one.
        # Fields already set by the user are never overwritten.
        if vessel:
            try:
                from documents.vessel_extractor import fill_vessel_metadata
                fill_vessel_metadata(vessel, raw_chunks)
                db.session.commit()
            except Exception as ve:
                logger.warning(
                    f"[documents] Vessel metadata extraction failed for "
                    f"'{group_name}': {ve}"
                )

    if not doc_ids:
        return redirect(url_for("documents.library", client=client_id))

    first_id  = doc_ids[0]
    queue     = ",".join(str(i) for i in doc_ids[1:])
    total     = len(doc_ids)
    return redirect(url_for(
        "documents.preview",
        doc_id=first_id,
        queue=queue or None,
        total=total,
    ))


@documents_bp.route("/<int:doc_id>/preview", methods=["GET"])
@documents_required
def preview(doc_id: int):
    """Render the editable chunk card view for a draft document."""
    doc = Document.query.get_or_404(doc_id)
    _assert_doc_access(doc)

    if doc.status == "active":
        flash("This document is already live. Delete it first to re-upload.", "error")
        return redirect(url_for("documents.library", client=doc.client_id))

    chunks = (
        DocumentChunk.query
        .filter_by(document_id=doc_id)
        .order_by(DocumentChunk.position)
        .all()
    )

    # Multi-file queue state
    queue      = request.args.get("queue", "")
    total      = int(request.args.get("total", 1))
    queue_ids  = [i for i in queue.split(",") if i.strip().isdigit()]
    file_index = total - len(queue_ids)   # e.g. total=3, 1 remaining → index=2

    return render_template(
        "documents/preview.html",
        doc=doc,
        chunks=chunks,
        queue=queue,
        total=total,
        file_index=file_index,
    )


def _apply_chunk_edits(doc_id: int, request_form) -> list:
    """
    Shared helper: parse chunk edits from a form submission, apply deletions
    and content updates, re-number positions, flush to the session, and return
    the surviving chunk objects.

    Raises ValueError (message safe to show to the user) if no chunks remain.
    """
    deleted_positions = set()
    deleted_str = request_form.get("deleted_positions", "")
    if deleted_str.strip():
        for p in deleted_str.split(","):
            try:
                deleted_positions.add(int(p.strip()))
            except ValueError:
                pass

    existing_chunks = (
        DocumentChunk.query
        .filter_by(document_id=doc_id)
        .order_by(DocumentChunk.position)
        .all()
    )

    kept_chunks = []
    for chunk in existing_chunks:
        if chunk.position in deleted_positions:
            db.session.delete(chunk)
        else:
            new_title = request_form.get(f"chunk_{chunk.position}_title", chunk.title)
            new_body  = request_form.get(f"chunk_{chunk.position}_body",  chunk.body)
            chunk.title = new_title.strip()
            chunk.body  = new_body.strip()
            if chunk.body:
                kept_chunks.append(chunk)
            else:
                db.session.delete(chunk)

    for new_pos, chunk in enumerate(kept_chunks):
        chunk.position = new_pos

    db.session.flush()

    if not kept_chunks:
        raise ValueError("No chunks remaining after edits. Add content before saving.")

    return kept_chunks


@documents_bp.route("/<int:doc_id>/save_draft", methods=["POST"])
@documents_required
def save_draft(doc_id: int):
    """
    Save chunk edits to the database only — does NOT push anything to Pinecone.

    The document status stays (or returns to) 'draft'.  Use this to review and
    refine extraction quality without polluting the knowledge base.  When the
    chunks are ready, use the Publish route to embed and index them.
    """
    doc = Document.query.get_or_404(doc_id)
    _assert_doc_access(doc)

    if doc.status == "active":
        flash("This document is already live. Delete it first to re-upload.", "error")
        return redirect(url_for("documents.preview", doc_id=doc_id))

    try:
        kept_chunks = _apply_chunk_edits(doc_id, request.form)
    except ValueError as e:
        flash(str(e), "error")
        return redirect(url_for("documents.preview", doc_id=doc_id))

    doc.status      = "draft"
    doc.chunk_count = len(kept_chunks)
    db.session.commit()

    flash(
        f"'{doc.filename}' saved as draft — {len(kept_chunks)} chunks. "
        "Publish when you're ready to add it to the knowledge base.",
        "success",
    )

    # Multi-file queue: if there are more files to review, advance to the next
    queue     = request.form.get("queue", "").strip()
    total     = request.form.get("total", "1")
    queue_ids = [i for i in queue.split(",") if i.strip().isdigit()]

    if queue_ids:
        next_id   = queue_ids[0]
        remaining = ",".join(queue_ids[1:])
        return redirect(url_for(
            "documents.preview",
            doc_id=next_id,
            queue=remaining or None,
            total=total,
        ))

    return redirect(url_for("documents.preview", doc_id=doc_id))


@documents_bp.route("/<int:doc_id>/save", methods=["POST"])
@documents_required
def save(doc_id: int):
    """
    1. Read edited chunk titles + bodies from the form.
    2. Update chunks in DB.
    3. Embed and upsert to Pinecone.
    4. Mark document as active.
    """
    from documents.embedder import embed_and_upsert

    doc = Document.query.get_or_404(doc_id)
    _assert_doc_access(doc)

    if doc.status == "active":
        flash("Document is already live.", "error")
        return redirect(url_for("documents.library", client=doc.client_id))

    client = ClientConfig.query.filter_by(client_id=doc.client_id).first()
    if not client:
        flash("Client config not found.", "error")
        return redirect(url_for("documents.library"))

    # ── Apply chunk edits ─────────────────────────────────────────────────────
    try:
        kept_chunks = _apply_chunk_edits(doc_id, request.form)
    except ValueError as e:
        flash(str(e), "error")
        return redirect(url_for("documents.preview", doc_id=doc_id))

    # ── Mark processing ───────────────────────────────────────────────────────
    doc.status      = "processing"
    doc.chunk_count = len(kept_chunks)
    db.session.commit()

    # ── Embed + upsert ────────────────────────────────────────────────────────
    try:
        upserted = embed_and_upsert(
            document=doc,
            chunks=kept_chunks,
            pinecone_index=client.pinecone_index,
            pinecone_namespace=client.pinecone_namespace,
            embedding_model=client.embedding_model,
        )
    except Exception as e:
        logger.error(f"[documents] Embedding failed for doc {doc_id}: {e}", exc_info=True)
        doc.status        = "error"
        doc.error_message = str(e)
        db.session.commit()
        flash(f"Embedding failed: {e}", "error")
        return redirect(url_for("documents.preview", doc_id=doc_id))

    # ── Store pinecone_ids + mark active ──────────────────────────────────────
    for chunk in kept_chunks:
        chunk.pinecone_id = chunk.vector_id()

    doc.status       = "active"
    doc.chunk_count  = upserted
    doc.activated_at = datetime.now(timezone.utc)
    doc.error_message = None
    db.session.commit()

    logger.info(
        f"[documents] Doc {doc_id} '{doc.filename}' live — "
        f"{upserted} vectors in {client.pinecone_index}/{client.pinecone_namespace}"
    )
    flash(f"'{doc.filename}' is now live — {upserted} chunks added to the knowledge base.", "success")

    # Multi-file queue: advance to next doc if there is one
    queue     = request.form.get("queue", "").strip()
    total     = request.form.get("total", "1")
    queue_ids = [i for i in queue.split(",") if i.strip().isdigit()]

    if queue_ids:
        next_id   = queue_ids[0]
        remaining = ",".join(queue_ids[1:])
        return redirect(url_for(
            "documents.preview",
            doc_id=next_id,
            queue=remaining or None,
            total=total,
        ))

    return redirect(url_for("documents.library", client=doc.client_id))


@documents_bp.route("/<int:doc_id>/publish", methods=["POST"])
@documents_required
def publish(doc_id: int):
    """
    Embed the document's current DB chunks and upsert them to Pinecone.

    This is the same as the final step of `save`, but skips form parsing —
    it uses whatever chunks are already in the database.  Intended for:
      - One-click Publish from the library for already-reviewed draft docs.
      - Calling after Save Draft when the user is ready to go live.
    """
    from documents.embedder import embed_and_upsert

    doc = Document.query.get_or_404(doc_id)
    _assert_doc_access(doc)

    if doc.status == "active":
        flash("Document is already live.", "error")
        return redirect(url_for("documents.library", client=doc.client_id))

    client = ClientConfig.query.filter_by(client_id=doc.client_id).first()
    if not client:
        flash("Client config not found.", "error")
        return redirect(url_for("documents.library"))

    chunks = (
        DocumentChunk.query
        .filter_by(document_id=doc_id)
        .order_by(DocumentChunk.position)
        .all()
    )
    if not chunks:
        flash("No chunks to publish. Open the document and add content first.", "error")
        return redirect(url_for("documents.preview", doc_id=doc_id))

    doc.status = "processing"
    db.session.commit()

    try:
        upserted = embed_and_upsert(
            document=doc,
            chunks=chunks,
            pinecone_index=client.pinecone_index,
            pinecone_namespace=client.pinecone_namespace,
            embedding_model=client.embedding_model,
        )
    except Exception as e:
        logger.error(f"[documents] Publish failed for doc {doc_id}: {e}", exc_info=True)
        doc.status        = "error"
        doc.error_message = str(e)
        db.session.commit()
        flash(f"Publish failed: {e}", "error")
        return redirect(url_for("documents.library", client=doc.client_id))

    for chunk in chunks:
        chunk.pinecone_id = chunk.vector_id()

    doc.status        = "active"
    doc.chunk_count   = upserted
    doc.activated_at  = datetime.now(timezone.utc)
    doc.error_message = None
    db.session.commit()

    logger.info(
        f"[documents] Doc {doc_id} '{doc.filename}' published — "
        f"{upserted} vectors in {client.pinecone_index}/{client.pinecone_namespace}"
    )
    flash(
        f"'{doc.filename}' is now live — {upserted} chunks added to the knowledge base.",
        "success",
    )
    return redirect(url_for("documents.library", client=doc.client_id))


@documents_bp.route("/<int:doc_id>/delete", methods=["POST"])
@documents_required
def delete(doc_id: int):
    """Delete a document: remove its Pinecone vectors then delete the DB record."""
    from documents.embedder import delete_document_vectors

    doc = Document.query.get_or_404(doc_id)
    _assert_doc_access(doc)

    client = ClientConfig.query.filter_by(client_id=doc.client_id).first()
    client_id_for_redirect = doc.client_id

    if doc.status == "active" and client:
        try:
            delete_document_vectors(
                document=doc,
                pinecone_index=client.pinecone_index,
                pinecone_namespace=client.pinecone_namespace,
            )
        except Exception as e:
            logger.error(
                f"[documents] Pinecone delete failed for doc {doc_id}: {e}",
                exc_info=True,
            )
            flash(
                f"Warning: vectors could not be removed from Pinecone ({e}). "
                "The document record has still been deleted.",
                "error",
            )

    filename = doc.filename
    db.session.delete(doc)
    db.session.commit()

    logger.info(f"[documents] Deleted doc {doc_id} '{filename}' for client '{client_id_for_redirect}'")
    flash(f"'{filename}' has been deleted.", "success")
    return redirect(url_for("documents.library", client=client_id_for_redirect))
