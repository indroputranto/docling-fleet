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
import json
import logging
import queue as _queue
import threading as _threading
import time as _time
from datetime import datetime, timezone
from functools import wraps

from flask import (
    Blueprint, render_template, redirect, url_for,
    request, flash, g, abort, current_app, Response
)
from werkzeug.utils import secure_filename

from models import db, ClientConfig, Document, DocumentChunk, Vessel

logger = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = {"docx", "pdf", "xlsx", "xls"}

# Ordered list of document sections for the Vessel Dossier UI.
# Each tuple is (slug_key, display_label).  The slug is stored as
# Document.document_category in the database and Pinecone metadata.
DOCUMENT_SECTIONS = [
    ("vessel_specifications",  "Vessel Specifications"),
    ("addendum",               "Addendum"),
    ("fixture_recap",          "Fixture Recap"),
    ("charter_party",          "Charter Party"),
    ("delivery_details",       "Delivery Details"),
    ("speed_consumption",      "Speed & Consumption"),
    ("inventory",              "Inventory"),
    ("lifting_equipment",      "Lifting Equipment"),
    ("hseq_documents",         "HSEQ Documents"),
    ("vessel_owners_details",  "Vessel & Owners Details"),
]
# full_document is a special top-level category for integrated vessel packages.
# It lives in VALID_CATEGORIES but NOT in DOCUMENT_SECTIONS (rendered separately
# at the top of the Dossier page, not inside the 10-section accordion).
FULL_DOCUMENT_CATEGORY = "full_document"
VALID_CATEGORIES = {key for key, _ in DOCUMENT_SECTIONS} | {FULL_DOCUMENT_CATEGORY}

documents_bp = Blueprint(
    "documents",
    __name__,
    url_prefix="/documents",
    template_folder="templates",
)


# ─────────────────────────────────────────────────────────────────────────────
# SSE upload-progress stream
#
# The upload route writes events into a per-task queue; the /upload-progress
# SSE endpoint streams them to the browser in real time.  Each request runs in
# its own thread (Flask threaded=True / gunicorn), so queue.Queue provides the
# needed thread-safe hand-off.
# ─────────────────────────────────────────────────────────────────────────────

_pq_lock:  _threading.Lock = _threading.Lock()
_pq_store: dict             = {}   # task_id → queue.Queue


def _pq_create(task_id: str) -> None:
    """Register a new progress queue for *task_id*."""
    with _pq_lock:
        _pq_store[task_id] = _queue.Queue()


def _pq_emit(task_id: str | None, type_: str, msg: str, pct: int | None = None) -> None:
    """Push one event onto the task's queue (no-op if task_id is falsy)."""
    if not task_id:
        return
    with _pq_lock:
        q = _pq_store.get(task_id)
    if q is None:
        return
    payload: dict = {"type": type_, "msg": msg}
    if pct is not None:
        payload["pct"] = pct
    q.put(payload)


def _pq_done(task_id: str | None, redirect_url: str) -> None:
    """Signal completion and supply the URL the browser should navigate to."""
    if not task_id:
        return
    with _pq_lock:
        q = _pq_store.get(task_id)
    if q is not None:
        q.put({"type": "done", "url": redirect_url})


@documents_bp.route("/upload-progress/<task_id>")
def upload_progress(task_id: str):
    """
    SSE endpoint — streams upload/processing progress back to the browser.
    Opened by the frontend before the AJAX upload is sent; holds open until
    the upload route calls _pq_done() or the 10-minute safety timeout fires.
    No auth required: the task_id UUID is only known to the tab that started it.
    """
    def _generate():
        # Give the upload request up to 10 s to create the queue before we bail.
        q = None
        for _ in range(100):
            with _pq_lock:
                q = _pq_store.get(task_id)
            if q is not None:
                break
            _time.sleep(0.1)

        if q is None:
            yield "data: " + json.dumps({"type": "error", "msg": "Task not found"}) + "\n\n"
            return

        try:
            while True:
                try:
                    item = q.get(timeout=600)   # 10-minute hard cap
                except _queue.Empty:
                    yield "data: " + json.dumps({"type": "timeout"}) + "\n\n"
                    return
                yield "data: " + json.dumps(item) + "\n\n"
                if item.get("type") == "done":
                    return
        finally:
            with _pq_lock:
                _pq_store.pop(task_id, None)

    return Response(
        _generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",   # disable nginx buffering
        },
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


@documents_bp.route("/<int:doc_id>/replace", methods=["POST"])
@documents_required
def replace(doc_id: int):
    """
    Replace an existing document with a new file upload.

    Deletes old Pinecone vectors and DB chunks, runs the full extract →
    enrich → coverage pipeline on the new file, and redirects to the
    preview page so the user can review before re-publishing.
    """
    from documents.embedder import delete_document_vectors
    import json as _json

    doc = Document.query.get_or_404(doc_id)
    _assert_doc_access(doc)

    file = request.files.get("file")
    if not file or not file.filename:
        flash("No file selected.", "error")
        return redirect(url_for("documents.vessel_dossier", vessel_id=doc.vessel_id))

    filename = secure_filename(file.filename)
    if not _allowed_file(filename):
        flash("Unsupported file type — use .docx, .pdf, .xlsx, or .xls.", "error")
        return redirect(url_for("documents.vessel_dossier", vessel_id=doc.vessel_id))

    from_vessel     = request.form.get("from_vessel", "").strip() or str(doc.vessel_id or "")
    skip_enrichment = request.form.get("skip_enrichment") == "on"
    client          = ClientConfig.query.filter_by(client_id=doc.client_id).first()

    # ── 0. Remove old object from storage ────────────────────────────────────
    try:
        from documents.object_storage import is_configured, delete_file
        if is_configured() and doc.storage_key:
            delete_file(doc.storage_key)
    except Exception as _se:
        logger.warning(f"[documents] Object storage delete skipped during replace (doc {doc_id}): {_se}")

    # ── 1. Remove old Pinecone vectors ────────────────────────────────────────
    if doc.status == "active" and client:
        try:
            delete_document_vectors(
                document=doc,
                pinecone_index=client.pinecone_index,
                pinecone_namespace=client.pinecone_namespace,
            )
        except Exception as e:
            logger.warning(f"[documents] Pinecone delete failed during replace (doc {doc_id}): {e}")

    # ── 2. Purge old chunks ───────────────────────────────────────────────────
    DocumentChunk.query.filter_by(document_id=doc.id).delete()
    db.session.flush()

    # ── 3. New file: defer processing when object storage is available ────────
    ext = filename.rsplit(".", 1)[1].lower()
    from documents.object_storage import is_configured, upload_file, build_storage_key
    import mimetypes as _mimetypes

    if is_configured():
        doc.filename       = filename
        doc.file_type      = ext
        doc.status         = "pending_upload"
        doc.chunk_count    = 0
        doc.storage_key    = None
        doc.coverage_pct   = None
        doc.coverage_notes = None
        doc.error_message  = None
        doc.skip_ai_enrichment = skip_enrichment
        db.session.commit()
        try:
            storage_key = build_storage_key(doc.client_id, filename)
            content_type = (
                _mimetypes.guess_type(filename)[0]
                or "application/octet-stream"
            )
            file.stream.seek(0)
            upload_file(file.stream, storage_key, content_type=content_type)
            doc.storage_key = storage_key
            db.session.commit()
        except Exception as e:
            doc.status         = "error"
            doc.error_message  = str(e)
            db.session.commit()
            flash(f"Could not store replacement '{filename}': {e}", "error")
            return redirect(url_for("documents.vessel_dossier", vessel_id=doc.vessel_id))

        logger.info(
            f"[documents] Replace doc {doc_id} deferred → '{filename}' (pending_upload)"
        )
        rkwargs = dict(doc_id=doc.id, total=1, from_vessel=from_vessel or None)
        rkwargs = {k: v for k, v in rkwargs.items() if v is not None}
        return redirect(url_for("documents.preview", **rkwargs))

    # ── 3b. No object storage — full inline extract (local dev) ──────────────
    from documents.extractor import extract
    try:
        raw_chunks = extract(file.stream, filename, document_category=doc.document_category)
    except Exception as e:
        flash(f"Could not extract text from '{filename}': {e}", "error")
        db.session.rollback()
        return redirect(url_for("documents.vessel_dossier", vessel_id=doc.vessel_id))

    if not raw_chunks or not any(c["body"].strip() for c in raw_chunks):
        flash(f"No text found in '{filename}' (scanned image PDF?).", "error")
        db.session.rollback()
        return redirect(url_for("documents.vessel_dossier", vessel_id=doc.vessel_id))

    raw_chunks_snapshot = [{"body": c.get("body", "")} for c in raw_chunks]

    # ── 4. AI enrichment ──────────────────────────────────────────────────────
    if not skip_enrichment:
        try:
            from documents.ai_enrichment import enrich_chunks
            vessel_obj = Vessel.query.get(doc.vessel_id) if doc.vessel_id else None
            raw_chunks = enrich_chunks(
                raw_chunks,
                filename,
                vessel_name=vessel_obj.name if vessel_obj else None,
                document_category=doc.document_category,
            )
        except Exception as ae:
            logger.warning(f"[documents] AI enrichment failed during replace (doc {doc_id}): {ae}")
    else:
        logger.info(f"[documents] AI enrichment skipped for replace of doc {doc_id}")

    # ── 5. Section title prefix ───────────────────────────────────────────────
    if doc.document_category and doc.document_category != FULL_DOCUMENT_CATEGORY:
        section_label = next(
            (lbl for slug, lbl in DOCUMENT_SECTIONS if slug == doc.document_category),
            None,
        )
        if section_label:
            try:
                from models import DossierSectionConfig
                cfg = DossierSectionConfig.query.filter_by(
                    client_id=doc.client_id, slug=doc.document_category
                ).first()
                if cfg and cfg.label and cfg.label.strip():
                    section_label = cfg.label.strip()
            except Exception:
                pass
            prefix = f"{section_label} - "
            for chunk in raw_chunks:
                title = (chunk.get("title") or "").strip()
                if title and not title.startswith(prefix):
                    chunk["title"] = prefix + title

    # ── 6. Coverage check ─────────────────────────────────────────────────────
    coverage_pct = coverage_notes = None
    try:
        from documents.coverage import run_coverage_check
        file.stream.seek(0)
        cov = run_coverage_check(
            file_stream=file.stream,
            filename=filename,
            raw_chunks_before_enrichment=raw_chunks_snapshot,
            final_chunks=raw_chunks,
        )
        coverage_pct   = cov.get("coverage_pct")
        coverage_notes = _json.dumps(cov)
    except Exception as ce:
        logger.warning(f"[documents] Coverage check failed during replace (doc {doc_id}): {ce}")

    # ── 7. Update Document record + persist new chunks ────────────────────────
    doc.filename       = filename
    doc.file_type      = ext
    doc.status         = "draft"
    doc.chunk_count    = len(raw_chunks)
    doc.coverage_pct   = coverage_pct
    doc.coverage_notes = coverage_notes
    doc.storage_key    = None  # cleared until we re-upload below

    for pos, chunk in enumerate(raw_chunks):
        db.session.add(DocumentChunk(
            document_id=doc.id,
            position=pos,
            title=chunk.get("title") or "",
            body=chunk["body"],
        ))

    db.session.commit()
    logger.info(
        f"[documents] Replaced doc {doc_id} with '{filename}' "
        f"({len(raw_chunks)} chunks) for client '{doc.client_id}'"
    )

    # ── Object storage: upload replacement file ───────────────────────────────
    try:
        from documents.object_storage import (
            is_configured, upload_file, build_storage_key
        )
        if is_configured():
            import mimetypes
            storage_key = build_storage_key(doc.client_id, filename)
            content_type = (
                mimetypes.guess_type(filename)[0] or "application/octet-stream"
            )
            file.stream.seek(0)
            upload_file(file.stream, storage_key, content_type=content_type)
            doc.storage_key = storage_key
            db.session.commit()
            logger.info(
                f"[documents] Stored replacement '{filename}' → {storage_key}"
            )
    except Exception as _se:
        logger.warning(
            f"[documents] Object storage upload skipped for replacement '{filename}': {_se}"
        )
    # ─────────────────────────────────────────────────────────────────────────

    return redirect(url_for(
        "documents.preview",
        doc_id=doc.id,
        total=1,
        from_vessel=from_vessel or None,
    ))


@documents_bp.route("/upload", methods=["POST"])
@documents_required
def upload():
    """
    Accept one or more file uploads, extract text from each, and redirect to
    the preview of the first file.  Remaining files are passed as a comma-
    separated ?queue= of doc IDs so the user reviews them sequentially.

    When DigitalOcean Spaces is configured (staging / production), this route
    only stores the raw bytes and creates ``pending_upload`` rows — extract,
    enrichment, and coverage run in a **second** HTTP request
    (``POST …/process-from-storage``) triggered from the preview page.  That
    splits wall-clock work across two Vercel serverless invocations so each
    stays under the platform time limit; locally, behaviour is unchanged when
    object storage is disabled (single-request inline pipeline).
    """
    from documents.object_storage import is_configured, upload_file, build_storage_key
    import mimetypes

    storage_ok = is_configured()

    # ── SSE task registration ────────────────────────────────────────────────
    task_id = request.form.get("task_id", "").strip() or None
    if task_id:
        _pq_create(task_id)
    # ────────────────────────────────────────────────────────────────────────

    # Determine target client
    client_id = request.form.get("client_id") or _resolve_client_id()
    if not client_id:
        flash("Please select a client before uploading.", "error")
        redir = url_for("documents.library")
        _pq_done(task_id, redir)
        return redirect(redir)

    if not g.is_platform_admin and client_id != g.scoped_client_id:
        abort(403)

    client = ClientConfig.query.filter_by(client_id=client_id).first()
    if not client:
        flash("Client not found.", "error")
        redir = url_for("documents.library")
        _pq_done(task_id, redir)
        return redirect(redir)

    # Resolve selected vessel (optional — documents may be unassigned)
    vessel_id  = request.form.get("vessel_id", "").strip() or None
    vessel     = None
    group_name = None
    if vessel_id:
        vessel = Vessel.query.filter_by(id=vessel_id, client_id=client_id).first()
        if not vessel:
            flash("Selected vessel not found.", "error")
            redir = url_for("documents.library", client=client_id)
            _pq_done(task_id, redir)
            return redirect(redir)
        group_name = vessel.name   # keep for display / backward compat

    # Explicit document category from Vessel Dossier upload (optional)
    document_category = request.form.get("document_category", "").strip() or None
    if document_category and document_category not in VALID_CATEGORIES:
        document_category = None  # reject unknown slugs silently

    # If upload came from the Vessel Dossier, remember the vessel id for redirect
    from_vessel = request.form.get("from_vessel", "").strip() or None

    # Allow the caller to bypass AI enrichment (e.g. large structured full-document
    # packages where the extraction is already clean and enrichment would be slow).
    skip_enrichment = request.form.get("skip_enrichment") == "on"

    files = [f for f in request.files.getlist("files") if f and f.filename]

    if not files:
        flash("No files selected.", "error")
        redir = url_for("documents.library", client=client_id)
        _pq_done(task_id, redir)
        return redirect(redir)

    doc_ids = []

    for file in files:
        filename = secure_filename(file.filename)

        if not _allowed_file(filename):
            flash(f"'{file.filename}' skipped — unsupported type (use .docx, .pdf, .xlsx, or .xls).", "error")
            _pq_emit(task_id, "warn", f"Skipped '{file.filename}' — unsupported file type")
            continue

        ext = filename.rsplit(".", 1)[1].lower()

        # ── Fast path: persist to object storage only (two-phase pipeline) ─
        if storage_ok:
            _pq_emit(
                task_id, "stage",
                f"Saving {filename} to object storage…",
            )
            _pq_emit(
                task_id, "info",
                "Heavy processing runs in a follow-up request (avoids Vercel timeout).",
                pct=30,
            )
            doc = Document(
                client_id=client_id,
                filename=filename,
                file_type=ext,
                status="pending_upload",
                uploaded_by=g.cms_user.email,
                chunk_count=0,
                vessel_id=int(vessel_id) if vessel_id else None,
                group_name=group_name,
                document_category=document_category,
                skip_ai_enrichment=skip_enrichment,
            )
            db.session.add(doc)
            db.session.flush()
            new_doc_id = doc.id
            try:
                storage_key = build_storage_key(client_id, filename)
                content_type = (
                    mimetypes.guess_type(filename)[0]
                    or "application/octet-stream"
                )
                file.stream.seek(0)
                upload_file(file.stream, storage_key, content_type=content_type)
                doc.storage_key = storage_key
                db.session.commit()
                doc_ids.append(doc.id)
                logger.info(
                    f"[documents] Stored '{filename}' for deferred processing → doc {doc.id} "
                    f"(client '{client_id}')"
                    + (f" vessel='{group_name}'" if group_name else "")
                )
                _pq_emit(task_id, "ok", f"Stored {filename} — will extract on next step ✓", pct=88)
            except Exception as e:
                db.session.rollback()
                Document.query.filter_by(id=new_doc_id).delete()
                db.session.commit()
                flash(f"'{filename}' skipped — could not save to storage: {e}", "error")
                logger.error(f"[documents] Storage upload failed for {filename}: {e}", exc_info=True)
                _pq_emit(task_id, "warn", f"Storage failed for '{filename}': {e}")
            continue

        # ── Local / no-storage path: full inline pipeline ───────────────────
        from documents.extractor import extract

        # ── Extraction ───────────────────────────────────────────────────────
        _pq_emit(task_id, "stage", "Extracting document content…")
        _pq_emit(task_id, "info",  f"File received: {filename}", pct=15)
        _pq_emit(task_id, "info",  f"Detecting document format ({ext.upper()})…", pct=18)
        _pq_emit(task_id, "info",  "Initialising parser…", pct=20)
        try:
            raw_chunks = extract(file.stream, filename, document_category=document_category)
        except Exception as e:
            flash(f"'{filename}' skipped — could not extract text: {e}", "error")
            logger.error(f"[documents] Extraction error for {filename}: {e}", exc_info=True)
            _pq_emit(task_id, "warn", f"Extraction failed for '{filename}': {e}")
            continue

        if not raw_chunks or not any(c["body"].strip() for c in raw_chunks):
            flash(f"'{filename}' skipped — no text found (scanned image PDF?).", "error")
            _pq_emit(task_id, "warn", f"No text found in '{filename}' — may be a scanned PDF")
            continue

        _pq_emit(task_id, "info", "Applying clause-aware chunking…", pct=32)
        _pq_emit(task_id, "ok",   f"Extraction complete — {len(raw_chunks)} chunks found ✓", pct=40)
        # ────────────────────────────────────────────────────────────────────

        # ── Snapshot raw chunks before enrichment (for coverage check) ─────────
        raw_chunks_snapshot = [{"body": c.get("body", "")} for c in raw_chunks]

        # ── AI enrichment pass ───────────────────────────────────────────────
        if not skip_enrichment:
            _pq_emit(task_id, "stage", "Running AI enrichment…")
            _pq_emit(task_id, "info",  f"Sending {len(raw_chunks)} chunks to GPT-4o mini…", pct=45)
            try:
                from documents.ai_enrichment import enrich_chunks
                raw_chunks = enrich_chunks(
                    raw_chunks,
                    filename,
                    vessel_name=vessel.name if vessel else None,
                    document_category=document_category,
                )
                _pq_emit(task_id, "info", "Cleaning and normalising chunk titles…", pct=60)
                _pq_emit(task_id, "info", "Detecting and splitting compound clauses…", pct=68)
                _pq_emit(task_id, "ok",   "AI enrichment complete ✓", pct=75)
            except Exception as ae:
                logger.warning(
                    f"[documents] AI enrichment failed for '{filename}', "
                    f"using raw extraction: {ae}"
                )
                _pq_emit(task_id, "warn", f"AI enrichment failed — using raw extraction: {ae}")
        else:
            logger.info(
                f"[documents] AI enrichment skipped for '{filename}' (skip_enrichment=True)"
            )
            _pq_emit(task_id, "warn", "AI enrichment skipped (user preference)", pct=62)
        # ────────────────────────────────────────────────────────────────────

        # ── Section title prefix ──────────────────────────────────────────────
        if document_category and document_category != FULL_DOCUMENT_CATEGORY:
            section_label = next(
                (lbl for slug, lbl in DOCUMENT_SECTIONS if slug == document_category),
                None,
            )
            if section_label:
                try:
                    from models import DossierSectionConfig
                    cfg = DossierSectionConfig.query.filter_by(
                        client_id=client_id, slug=document_category
                    ).first()
                    if cfg and cfg.label and cfg.label.strip():
                        section_label = cfg.label.strip()
                except Exception:
                    pass  # fall back to the built-in default

            if section_label:
                prefix = f"{section_label} - "
                for chunk in raw_chunks:
                    title = (chunk.get("title") or "").strip()
                    if title and not title.startswith(prefix):
                        chunk["title"] = prefix + title
                logger.info(
                    f"[documents] Applied section prefix '{section_label}' "
                    f"to {len(raw_chunks)} chunk(s) of '{filename}'"
                )
        # ─────────────────────────────────────────────────────────────────────

        # ── Coverage check ────────────────────────────────────────────────────
        _pq_emit(task_id, "stage", "Finalising document…")
        _pq_emit(task_id, "info",  "Running coverage check against source…", pct=80)
        coverage_pct   = None
        coverage_notes = None
        try:
            from documents.coverage import run_coverage_check
            file.stream.seek(0)
            cov = run_coverage_check(
                file_stream=file.stream,
                filename=filename,
                raw_chunks_before_enrichment=raw_chunks_snapshot,
                final_chunks=raw_chunks,
            )
            coverage_pct   = cov.get("coverage_pct")
            coverage_notes = json.dumps(cov)
            if coverage_pct is not None:
                _pq_emit(task_id, "info", f"Coverage check passed — {coverage_pct:.0f}% content retained", pct=84)
        except Exception as ce:
            logger.warning(f"[documents] Coverage check failed for '{filename}': {ce}")
        # ─────────────────────────────────────────────────────────────────────

        _pq_emit(task_id, "info", "Writing document record to database…", pct=87)
        doc = Document(
            client_id=client_id,
            filename=filename,
            file_type=ext,
            status="draft",
            uploaded_by=g.cms_user.email,
            chunk_count=len(raw_chunks),
            vessel_id=int(vessel_id) if vessel_id else None,
            group_name=group_name,
            document_category=document_category,
            coverage_pct=coverage_pct,
            coverage_notes=coverage_notes,
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
        _pq_emit(task_id, "ok", f"Document saved — {len(raw_chunks)} chunks ✓", pct=92)
        logger.info(
            f"[documents] Uploaded '{filename}' → doc {doc.id} "
            f"({len(raw_chunks)} chunks) for client '{client_id}'"
            + (f" vessel='{group_name}'" if group_name else "")
        )

        # ── Object storage: save original file ───────────────────────────────
        _pq_emit(task_id, "info", "Uploading original file to object storage…", pct=94)
        try:
            if is_configured():
                storage_key = build_storage_key(client_id, filename)
                content_type = (
                    mimetypes.guess_type(filename)[0]
                    or "application/octet-stream"
                )
                file.stream.seek(0)
                upload_file(file.stream, storage_key, content_type=content_type)
                doc.storage_key = storage_key
                db.session.commit()
                logger.info(
                    f"[documents] Stored original '{filename}' → {storage_key}"
                )
                _pq_emit(task_id, "ok", "Original file stored ✓", pct=97)
            else:
                _pq_emit(task_id, "info", "Object storage not configured — skipping", pct=97)
        except Exception as _se:
            logger.warning(
                f"[documents] Object storage upload skipped for '{filename}': {_se}"
            )
            _pq_emit(task_id, "warn", f"Object storage skipped: {_se}")
        # ─────────────────────────────────────────────────────────────────────

        # ── Auto-fill vessel metadata from spec sheet chunks ─────────────────
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
        redir = url_for("documents.library", client=client_id)
        _pq_done(task_id, redir)
        return redirect(redir)

    first_id  = doc_ids[0]
    queue     = ",".join(str(i) for i in doc_ids[1:])
    total     = len(doc_ids)
    preview_kwargs = dict(
        doc_id=first_id,
        queue=queue or None,
        total=total,
        from_vessel=from_vessel or None,
    )
    preview_kwargs = {k: v for k, v in preview_kwargs.items() if v is not None}
    redir = url_for("documents.preview", **preview_kwargs)
    _pq_emit(task_id, "ok", "Processing complete — loading chunk review…", pct=99)
    _pq_done(task_id, redir)
    return redirect(redir)


@documents_bp.route("/presign", methods=["POST"])
@documents_required
def presign():
    """
    Issue a pre-signed PUT URL for a direct browser-to-storage upload.

    Called by the frontend when a single file exceeds the large-file threshold
    (currently 3 MB).  Creates a placeholder Document record in ``pending_upload``
    status so the browser has a ``doc_id`` to pass back once the PUT completes,
    then returns the signed URL and that ID.

    Request JSON
    ------------
    filename          : str   — original file name (will be sanitised)
    content_type      : str   — MIME type (informational; DO Spaces sets it on PUT)
    file_size         : int   — byte size (logged only)
    client_id         : str   — target client slug
    vessel_id         : str   — optional vessel ID
    document_category : str   — optional category slug
    from_vessel       : str   — optional vessel ID for post-process redirect
    skip_enrichment   : bool  — skip AI enrichment step

    Response JSON (direct upload available)
    ---------------------------------------
    { use_direct: true, presign_url, storage_key, doc_id }

    Response JSON (fallback — object storage not configured)
    --------------------------------------------------------
    { use_direct: false }
    """
    from flask import jsonify
    from documents.object_storage import (
        is_configured, build_storage_key, generate_presigned_put_url,
    )

    if not is_configured():
        return jsonify({"use_direct": False})

    data = request.get_json(silent=True) or {}

    filename = secure_filename(data.get("filename", ""))
    if not filename or not _allowed_file(filename):
        return jsonify({"error": "Invalid or unsupported file type"}), 400

    client_id = data.get("client_id") or _resolve_client_id()
    if not client_id:
        return jsonify({"error": "No client selected"}), 400

    if not g.is_platform_admin and client_id != g.scoped_client_id:
        abort(403)

    client = ClientConfig.query.filter_by(client_id=client_id).first()
    if not client:
        return jsonify({"error": "Client not found"}), 404

    vessel_id         = data.get("vessel_id") or None
    document_category = data.get("document_category") or None
    if document_category and document_category not in VALID_CATEGORIES:
        document_category = None
    skip_enrichment   = bool(data.get("skip_enrichment", False))

    ext         = filename.rsplit(".", 1)[1].lower()
    storage_key = build_storage_key(client_id, filename)

    # Resolve vessel name for group_name
    group_name = None
    vessel_obj = None
    if vessel_id:
        vessel_obj = Vessel.query.filter_by(id=vessel_id, client_id=client_id).first()
        if vessel_obj:
            group_name = vessel_obj.name

    # Create a placeholder Document so we have a stable doc_id to track progress.
        doc = Document(
            client_id=client_id,
            filename=filename,
            file_type=ext,
            status="pending_upload",
            uploaded_by=g.cms_user.email,
            chunk_count=0,
            vessel_id=int(vessel_id) if vessel_id else None,
            group_name=group_name,
            document_category=document_category,
            storage_key=storage_key,
            skip_ai_enrichment=skip_enrichment,
        )
    db.session.add(doc)
    db.session.commit()

    try:
        presign_url = generate_presigned_put_url(storage_key)
    except Exception as e:
        db.session.delete(doc)
        db.session.commit()
        logger.error(f"[documents] Pre-signed PUT URL generation failed: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

    file_size = data.get("file_size", 0)
    logger.info(
        f"[documents] Pre-signed PUT issued for '{filename}' ({file_size} bytes) → "
        f"{storage_key} (doc {doc.id}, client '{client_id}')"
    )

    return jsonify({
        "use_direct":   True,
        "presign_url":  presign_url,
        "storage_key":  storage_key,
        "doc_id":       doc.id,
    })


@documents_bp.route("/<int:doc_id>/process-from-storage", methods=["POST"])
@documents_required
def process_from_storage(doc_id: int):
    """
    Download a file that was PUT directly to DO Spaces and run the full
    extraction → AI enrichment → coverage pipeline on it.

    Called by the frontend after a successful direct PUT, passing back the
    ``doc_id`` returned by ``/presign``.  Wires into the same SSE progress
    stream as the regular upload route.

    Form fields
    -----------
    task_id         : str  — SSE task ID (optional but expected)
    from_vessel     : str  — vessel ID for post-process redirect (optional)
    skip_enrichment : str  — "on" to skip AI enrichment
    """
    import io
    import json as _json
    from documents.extractor import extract
    from documents.object_storage import is_configured, download_file

    doc = Document.query.get_or_404(doc_id)
    _assert_doc_access(doc)

    if doc.status == "pending_upload":
        existing_chunks = DocumentChunk.query.filter_by(document_id=doc.id).count()
        if existing_chunks:
            logger.info(
                "[documents] process-from-storage: doc %s already has %d chunk(s); "
                "recovery — marking draft",
                doc_id,
                existing_chunks,
            )
            doc.status      = "draft"
            doc.chunk_count = existing_chunks
            db.session.commit()
            return redirect(url_for("documents.preview", doc_id=doc_id))

    # Guard against accidental double-processing
    if doc.status not in ("pending_upload", "error"):
        return redirect(url_for("documents.preview", doc_id=doc_id))

    task_id         = request.form.get("task_id", "").strip() or None
    from_vessel     = request.form.get("from_vessel", "").strip() or None
    skip_enrichment = (
        request.form.get("skip_enrichment", "") == "on"
        or bool(getattr(doc, "skip_ai_enrichment", False))
    )

    if task_id:
        _pq_create(task_id)

    filename          = doc.filename
    ext               = doc.file_type
    storage_key       = doc.storage_key
    client_id         = doc.client_id
    document_category = doc.document_category

    # ── Download file from DO Spaces ──────────────────────────────────────────
    _pq_emit(task_id, "stage", "Retrieving file from storage…")
    _pq_emit(task_id, "info",  f"Downloading '{filename}' from object storage…", pct=10)

    try:
        file_bytes = download_file(storage_key)
    except Exception as e:
        doc.status        = "error"
        doc.error_message = f"Storage download failed: {e}"
        db.session.commit()
        _pq_emit(task_id, "warn", f"Failed to retrieve file from storage: {e}")
        redir = url_for("documents.library", client=client_id)
        _pq_done(task_id, redir)
        return redirect(redir)

    file_stream = io.BytesIO(file_bytes)

    # ── Extraction ────────────────────────────────────────────────────────────
    _pq_emit(task_id, "stage", "Extracting document content…")
    _pq_emit(task_id, "info",  f"File received: {filename}", pct=15)
    _pq_emit(task_id, "info",  f"Detecting document format ({ext.upper()})…", pct=18)
    _pq_emit(task_id, "info",  "Initialising parser…", pct=20)

    try:
        raw_chunks = extract(file_stream, filename, document_category=document_category)
    except Exception as e:
        doc.status        = "error"
        doc.error_message = str(e)
        db.session.commit()
        logger.error(f"[documents] Extraction error (from storage) for {filename}: {e}", exc_info=True)
        _pq_emit(task_id, "warn", f"Extraction failed for '{filename}': {e}")
        redir = url_for("documents.library", client=client_id)
        _pq_done(task_id, redir)
        return redirect(redir)

    if not raw_chunks or not any(c["body"].strip() for c in raw_chunks):
        doc.status        = "error"
        doc.error_message = "No text found (scanned image PDF?)"
        db.session.commit()
        _pq_emit(task_id, "warn", f"No text found in '{filename}' — may be a scanned PDF")
        redir = url_for("documents.library", client=client_id)
        _pq_done(task_id, redir)
        return redirect(redir)

    _pq_emit(task_id, "info", "Applying clause-aware chunking…", pct=32)
    _pq_emit(task_id, "ok",   f"Extraction complete — {len(raw_chunks)} chunks found ✓", pct=40)

    raw_chunks_snapshot = [{"body": c.get("body", "")} for c in raw_chunks]

    # ── AI enrichment ─────────────────────────────────────────────────────────
    if not skip_enrichment:
        _pq_emit(task_id, "stage", "Running AI enrichment…")
        _pq_emit(task_id, "info",  f"Sending {len(raw_chunks)} chunks to GPT-4o mini…", pct=45)
        try:
            from documents.ai_enrichment import enrich_chunks
            vessel_obj = Vessel.query.get(doc.vessel_id) if doc.vessel_id else None
            raw_chunks = enrich_chunks(
                raw_chunks,
                filename,
                vessel_name=vessel_obj.name if vessel_obj else None,
                document_category=document_category,
            )
            _pq_emit(task_id, "info", "Cleaning and normalising chunk titles…", pct=60)
            _pq_emit(task_id, "info", "Detecting and splitting compound clauses…", pct=68)
            _pq_emit(task_id, "ok",   "AI enrichment complete ✓", pct=75)
        except Exception as ae:
            logger.warning(
                f"[documents] AI enrichment failed (from storage) for '{filename}': {ae}"
            )
            _pq_emit(task_id, "warn", f"AI enrichment failed — using raw extraction: {ae}")
    else:
        logger.info(
            f"[documents] AI enrichment skipped for '{filename}' (skip_enrichment=True)"
        )
        _pq_emit(task_id, "warn", "AI enrichment skipped (user preference)", pct=62)

    # ── Section title prefix ──────────────────────────────────────────────────
    if document_category and document_category != FULL_DOCUMENT_CATEGORY:
        section_label = next(
            (lbl for slug, lbl in DOCUMENT_SECTIONS if slug == document_category),
            None,
        )
        if section_label:
            try:
                from models import DossierSectionConfig
                cfg = DossierSectionConfig.query.filter_by(
                    client_id=client_id, slug=document_category
                ).first()
                if cfg and cfg.label and cfg.label.strip():
                    section_label = cfg.label.strip()
            except Exception:
                pass
            if section_label:
                prefix = f"{section_label} - "
                for chunk in raw_chunks:
                    title = (chunk.get("title") or "").strip()
                    if title and not title.startswith(prefix):
                        chunk["title"] = prefix + title

    # ── Coverage check ────────────────────────────────────────────────────────
    _pq_emit(task_id, "stage", "Finalising document…")
    _pq_emit(task_id, "info",  "Running coverage check against source…", pct=80)
    coverage_pct = coverage_notes = None
    try:
        from documents.coverage import run_coverage_check
        file_stream.seek(0)
        cov = run_coverage_check(
            file_stream=file_stream,
            filename=filename,
            raw_chunks_before_enrichment=raw_chunks_snapshot,
            final_chunks=raw_chunks,
        )
        coverage_pct   = cov.get("coverage_pct")
        coverage_notes = _json.dumps(cov)
        if coverage_pct is not None:
            _pq_emit(task_id, "info",
                     f"Coverage check passed — {coverage_pct:.0f}% content retained", pct=84)
    except Exception as ce:
        logger.warning(f"[documents] Coverage check failed (from storage) for '{filename}': {ce}")

    # ── Persist chunks + update Document record ───────────────────────────────
    _pq_emit(task_id, "info", "Writing document record to database…", pct=87)

    doc.status         = "draft"
    doc.chunk_count    = len(raw_chunks)
    doc.coverage_pct   = coverage_pct
    doc.coverage_notes = coverage_notes
    doc.error_message  = None

    for pos, chunk in enumerate(raw_chunks):
        db.session.add(DocumentChunk(
            document_id=doc.id,
            position=pos,
            title=chunk.get("title") or "",
            body=chunk["body"],
        ))

    db.session.commit()
    _pq_emit(task_id, "ok", f"Document saved — {len(raw_chunks)} chunks ✓", pct=92)
    _pq_emit(task_id, "ok", "Original file already in object storage ✓", pct=97)

    logger.info(
        f"[documents] Processed large upload '{filename}' → doc {doc.id} "
        f"({len(raw_chunks)} chunks) for client '{client_id}'"
    )

    # ── Auto-fill vessel metadata ─────────────────────────────────────────────
    if doc.vessel_id:
        vessel_obj = Vessel.query.get(doc.vessel_id)
        if vessel_obj:
            try:
                from documents.vessel_extractor import fill_vessel_metadata
                fill_vessel_metadata(vessel_obj, raw_chunks)
                db.session.commit()
            except Exception as ve:
                logger.warning(
                    f"[documents] Vessel metadata extraction failed (from storage) "
                    f"for '{doc.group_name}': {ve}"
                )

    redir = url_for(
        "documents.preview",
        doc_id=doc.id,
        total=1,
        from_vessel=from_vessel or None,
    )
    _pq_emit(task_id, "ok", "Processing complete — loading chunk review…", pct=99)
    _pq_done(task_id, redir)
    return redirect(redir)


@documents_bp.route("/<int:doc_id>/preview", methods=["GET"])
@documents_required
def preview(doc_id: int):
    """Render the editable chunk card view for a document (draft or active)."""
    doc = Document.query.get_or_404(doc_id)
    _assert_doc_access(doc)

    chunks = (
        DocumentChunk.query
        .filter_by(document_id=doc_id)
        .order_by(DocumentChunk.position)
        .all()
    )

    # Multi-file queue state
    queue       = request.args.get("queue", "")
    total       = int(request.args.get("total", 1))
    queue_ids   = [i for i in queue.split(",") if i.strip().isdigit()]
    file_index  = total - len(queue_ids)   # e.g. total=3, 1 remaining → index=2

    # from_vessel drives the Back button and post-save redirect.
    # Prefer the explicit query param; fall back to the document's own vessel_id
    # so navigation stays consistent even after a page refresh.
    from_vessel = (
        request.args.get("from_vessel", "").strip()
        or (str(doc.vessel_id) if doc.vessel_id else None)
    )

    # Parse stored coverage notes for the preview banner
    import json as _json
    coverage = None
    if doc.coverage_notes:
        try:
            coverage = _json.loads(doc.coverage_notes)
        except Exception:
            pass

    pending_storage_processing = doc.status == "pending_upload"
    skip_enrich_process = bool(
        getattr(doc, "skip_ai_enrichment", False)
        or request.args.get("skip_enrich") == "1"
    )

    return render_template(
        "documents/preview.html",
        doc=doc,
        chunks=chunks,
        queue=queue,
        total=total,
        file_index=file_index,
        from_vessel=from_vessel,
        coverage=coverage,
        is_live=(doc.status == "active"),
        pending_storage_processing=pending_storage_processing,
        skip_enrich_process=skip_enrich_process,
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

    The document status is set (or returned) to 'draft'.  If the document was
    previously active/live, its Pinecone vectors are deleted so the knowledge
    base stays consistent with what is in the DB.

    Use this to review and refine extraction quality without affecting the
    knowledge base.  When the chunks are ready, use the Publish route to embed
    and index them.
    """
    doc = Document.query.get_or_404(doc_id)
    _assert_doc_access(doc)

    was_active = doc.status == "active"

    try:
        kept_chunks = _apply_chunk_edits(doc_id, request.form)
    except ValueError as e:
        flash(str(e), "error")
        return redirect(url_for("documents.preview", doc_id=doc_id))

    # If the document was live, remove its vectors from Pinecone so the
    # knowledge base stays consistent with the edited (draft) chunks.
    if was_active:
        from documents.embedder import delete_document_vectors
        client = ClientConfig.query.filter_by(client_id=doc.client_id).first()
        if client:
            try:
                delete_document_vectors(doc, client.pinecone_index, client.pinecone_namespace)
            except Exception as e:
                logger.warning(
                    f"[documents] Could not delete Pinecone vectors for doc {doc_id} "
                    f"during save_draft: {e}"
                )

    doc.status      = "draft"
    doc.chunk_count = len(kept_chunks)
    db.session.commit()

    if was_active:
        flash(
            f"'{doc.filename}' saved as draft and removed from the knowledge base — "
            f"{len(kept_chunks)} chunks. Re-publish when you're ready.",
            "success",
        )
    else:
        flash(
            f"'{doc.filename}' saved as draft — {len(kept_chunks)} chunks. "
            "Publish when you're ready to add it to the knowledge base.",
            "success",
        )

    # Multi-file queue: if there are more files to review, advance to the next
    queue       = request.form.get("queue", "").strip()
    total       = request.form.get("total", "1")
    from_vessel = request.form.get("from_vessel", "").strip() or None
    queue_ids   = [i for i in queue.split(",") if i.strip().isdigit()]

    if queue_ids:
        next_id   = queue_ids[0]
        remaining = ",".join(queue_ids[1:])
        return redirect(url_for(
            "documents.preview",
            doc_id=next_id,
            queue=remaining or None,
            total=total,
            from_vessel=from_vessel or None,
        ))

    # If this document came from the Vessel Dossier, return there after save draft
    if from_vessel:
        return redirect(url_for("documents.vessel_dossier", vessel_id=from_vessel))

    return redirect(url_for("documents.preview", doc_id=doc_id))


@documents_bp.route("/<int:doc_id>/save", methods=["POST"])
@documents_required
def save(doc_id: int):
    """
    1. Read edited chunk titles + bodies from the form.
    2. Update chunks in DB.
    3. If document was already active, delete its old Pinecone vectors first.
    4. Embed and upsert to Pinecone.
    5. Mark document as active.

    Works for both first-time publish (draft → active) and re-publish
    (active → active), so users can amend live chunks and push the update.
    """
    from documents.embedder import embed_and_upsert, delete_document_vectors

    doc = Document.query.get_or_404(doc_id)
    _assert_doc_access(doc)

    was_active = doc.status == "active"

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

    # ── If re-publishing, delete stale Pinecone vectors before re-embed ───────
    # Chunk positions may have changed (deletions, additions), so a plain upsert
    # would leave orphaned vectors for any removed or repositioned chunks.
    if was_active:
        try:
            delete_document_vectors(doc, client.pinecone_index, client.pinecone_namespace)
        except Exception as e:
            logger.warning(
                f"[documents] Could not delete old Pinecone vectors for doc {doc_id} "
                f"before re-publish: {e}"
            )

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
        f"[documents] Doc {doc_id} '{doc.filename}' {'re-published' if was_active else 'published'} — "
        f"{upserted} vectors in {client.pinecone_index}/{client.pinecone_namespace}"
    )
    if was_active:
        flash(
            f"'{doc.filename}' re-published — {upserted} chunks updated in the knowledge base.",
            "success",
        )
    else:
        flash(
            f"'{doc.filename}' is now live — {upserted} chunks added to the knowledge base.",
            "success",
        )

    # Multi-file queue: advance to next doc if there is one
    queue     = request.form.get("queue", "").strip()
    total     = request.form.get("total", "1")
    queue_ids = [i for i in queue.split(",") if i.strip().isdigit()]

    if queue_ids:
        next_id    = queue_ids[0]
        remaining  = ",".join(queue_ids[1:])
        from_vessel = request.form.get("from_vessel", "").strip() or None
        return redirect(url_for(
            "documents.preview",
            doc_id=next_id,
            queue=remaining or None,
            total=total,
            from_vessel=from_vessel or None,
        ))

    # If this document came from the Vessel Dossier, return there
    if doc.document_category and doc.vessel_id:
        return redirect(url_for("documents.vessel_dossier", vessel_id=doc.vessel_id))

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
    if doc.document_category and doc.vessel_id:
        return redirect(url_for("documents.vessel_dossier", vessel_id=doc.vessel_id))
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

    filename    = doc.filename
    vessel_id   = doc.vessel_id        # capture before delete
    storage_key = doc.storage_key      # capture before delete
    from_vessel = request.form.get("from_vessel", "").strip() or None

    db.session.delete(doc)
    db.session.commit()

    # ── Object storage: remove original file ─────────────────────────────────
    try:
        from documents.object_storage import is_configured, delete_file
        if is_configured() and storage_key:
            delete_file(storage_key)
    except Exception as _se:
        logger.warning(
            f"[documents] Object storage delete skipped for '{filename}': {_se}"
        )
    # ─────────────────────────────────────────────────────────────────────────

    logger.info(f"[documents] Deleted doc {doc_id} '{filename}' for client '{client_id_for_redirect}'")
    flash(f"'{filename}' has been deleted.", "success")

    # Return to the Vessel Dossier when the delete was triggered from there,
    # falling back to the document's own vessel_id, then the library.
    redirect_vessel = from_vessel or (str(vessel_id) if vessel_id else None)
    if redirect_vessel:
        return redirect(url_for("documents.vessel_dossier", vessel_id=redirect_vessel))
    return redirect(url_for("documents.library", client=client_id_for_redirect))

# ─────────────────────────────────────────────────────────────────────────────
# Vessel Dossier — structured 10-section upload view per vessel
# ─────────────────────────────────────────────────────────────────────────────

@documents_bp.route("/vessel/<int:vessel_id>/dossier")
@documents_required
def vessel_dossier(vessel_id: int):
    """
    Render the per-vessel dossier page with 10 collapsible document sections.

    Each section shows already-uploaded documents for that category plus a
    fresh drop zone for new uploads.  Section status (Not started / In progress
    / Verified) is derived live from the Document records in the DB.
    """
    vessel = Vessel.query.get_or_404(vessel_id)

    # Enforce client scope
    if not g.is_platform_admin and vessel.client_id != g.scoped_client_id:
        abort(403)

    # Full-document package uploads (top-level, not tied to any section)
    full_docs = (
        Document.query
        .filter_by(vessel_id=vessel.id, document_category=FULL_DOCUMENT_CATEGORY)
        .order_by(Document.uploaded_at)
        .all()
    )

    # Load per-client section customisations (rename / hide)
    from models import DossierSectionConfig
    section_configs = {
        cfg.slug: cfg
        for cfg in DossierSectionConfig.query.filter_by(client_id=vessel.client_id).all()
    }

    sections = []
    for cat_key, default_label in DOCUMENT_SECTIONS:
        cfg = section_configs.get(cat_key)

        # Skip sections the client has hidden
        if cfg and not cfg.active:
            continue

        # Use custom label if one has been set, otherwise fall back to default
        label = (cfg.label.strip() if cfg and cfg.label and cfg.label.strip()
                 else default_label)

        docs = (
            Document.query
            .filter_by(vessel_id=vessel.id, document_category=cat_key)
            .order_by(Document.uploaded_at)
            .all()
        )
        if not docs:
            status = "not_started"
        elif all(d.status == "active" for d in docs):
            status = "verified"
        else:
            status = "in_progress"

        sections.append({
            "key":       cat_key,
            "label":     label,
            "status":    status,
            "documents": docs,
        })

    verified_count = sum(1 for s in sections if s["status"] == "verified")
    total_sections = len(sections)
    progress_pct   = int(verified_count / total_sections * 100) if total_sections else 0

    return render_template(
        "documents/vessel_dossier.html",
        vessel=vessel,
        sections=sections,
        full_docs=full_docs,
        verified_count=verified_count,
        total_sections=total_sections,
        progress_pct=progress_pct,
        full_document_category=FULL_DOCUMENT_CATEGORY,
    )
