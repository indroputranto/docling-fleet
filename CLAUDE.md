# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is **docling** — a whitelabel maritime AI chatbot SaaS platform. It ingests vessel documents (charter parties, fixture recaps, BIMCO addenda) and makes them queryable via a RAG-powered chat interface. Each client gets an isolated deployment with their own branded chatbot, database, Pinecone index, and (in production) a dedicated Digital Ocean Droplet.

**Always read `grand_plan.md` before making architectural decisions.** It is the canonical source of truth for the product vision, infrastructure decisions, and development roadmap.

---

## Running the App

```bash
# Install dependencies (use venv)
pip install -r requirements.txt

# Run locally
python app.py          # Flask dev server on port 8080

# Run CI smoke tests
python test_ci.py

# Apply DB schema migrations for local SQLite.
# (Neon migrations live in app.py's boot-time _pending_cols list — see
# Database Models section below. db.create_all() does NOT add columns to
# existing tables; it only creates missing tables.)
python migrate_db.py
```

There is no build step. No webpack, no npm. Frontend is vanilla HTML/CSS/JS in `templates/`.

---

## Environment Variables

Copy `env_example.txt` to `.env`. Key variables:

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | PostgreSQL connection string (Neon in staging). Omit to use local `platform.db` (SQLite). |
| `DEFAULT_CLIENT_ID` | Client slug for single-domain deployments (e.g. Vercel). Required on Vercel. |
| `JWT_SECRET` | JWT signing key |
| `OPENAI_API_KEY` | Embeddings + AI enrichment |
| `ANTHROPIC_API_KEY` | Chat LLM |
| `PINECONE_API_KEY` | Vector database |
| `OBJECT_STORAGE_ENDPOINT/ACCESS_KEY/SECRET_KEY/BUCKET` | DO Spaces (all four required; silently skipped if any is missing) |
| `ADMIN_EMAIL` / `ADMIN_PASSWORD` | Seeded platform admin on first boot |

SQLite (`platform.db`) is used locally. PostgreSQL is required on Vercel and all production Droplets.

---

## Architecture

### Blueprints and Routing

All routes are mounted on a single Flask `app` in `app.py`. Four blueprints:

| Blueprint | Prefix | File |
|---|---|---|
| `auth_bp` | `/auth` | `auth.py` |
| `chat_bp` | `/api` | `chat_routes.py` |
| `cms_bp` | `/cms` | `cms/routes.py` |
| `documents_bp` | `/documents` | `documents/routes.py` |

`app.py` also owns `/`, `/login`, `/logout`, `/health`, and legacy Langdock webhook routes.

### Client ID Resolution

`get_client_id_from_request()` in `app.py` determines which client is being served, in priority order:
1. `?client=` query param
2. Subdomain of the request host (skipped for known hosting domains like `vercel.app`)
3. `DEFAULT_CLIENT_ID` env var

This means Vercel deployments (e.g. `docling-fleet.vercel.app`) **must** set `DEFAULT_CLIENT_ID` in their env vars.

### Authentication

Two parallel auth paths coexist:

- **Bearer token** (`Authorization: Bearer <jwt>`) — used by API clients and the chat frontend's JS fetch calls
- **`cms_token` httpOnly cookie** — used for browser sessions in the CMS and chat UI

`auth.py` issues and validates both. `chat_routes.py`'s `_resolve_user_email()` checks Bearer first, then the cookie. CMS routes use `cms_required` / `platform_admin_required` decorators in `cms/routes.py` that read the cookie.

Three roles: `admin` (platform operator, full access), `client_admin` (scoped to own client), `user` (chat only, no CMS).

### RAG Pipeline (Chat)

`chat_routes.py` → `POST /api/chat`:
1. Embed the user query via OpenAI `text-embedding-3-small`
2. Query Pinecone for top-K chunks filtered by `client_id`
3. Build context block from retrieved chunks
4. Send messages + context to Anthropic Claude (model is per-client configurable)
5. Return reply, `conversation_id`, `session_id`, and `sources`

Chat sessions and messages are persisted in `ChatSession` / `ChatMessage` DB tables. The frontend loads history via `GET /api/sessions` and passes `session_id` in subsequent chat requests.

### Document Upload Pipeline

`documents/routes.py` → multi-step human-in-the-loop flow:

1. **Upload** (`POST /documents/upload`) — accepts `.docx`, `.pdf`, `.xlsx`; also uploads to DO Spaces via `documents/object_storage.py`
2. **Extract** (`documents/extractor.py`) — clause-aware chunking; PyMuPDF primary, pdfplumber fallback
3. **AI Enrichment** (`documents/ai_enrichment.py`) — optional gpt-4o-mini pass to clean titles and split clauses; falls back to raw extraction silently
4. **Preview/Edit** (`GET /documents/<id>/preview`) — human review step before embedding
5. **Save** (`POST /documents/<id>/save`) — embeds chunks via `documents/embedder.py`, upserts to Pinecone

The multi-file batch upload uses a `?queue=id1,id2&total=N` query string pattern passed through hidden form fields to sequence the review step per file.

### Database Models (`models.py`)

Key models: `User`, `ClientConfig`, `Document`, `DocumentChunk`, `Vessel`, `UsageLog`, `ChatSession`, `ChatMessage`.

`Document.storage_key` — path of the raw file in DO Spaces (`documents/{client_id}/{filename}`).  
`Document.document_category` — one of 10 fixed slugs (e.g. `charter_party`, `fixture_recap`) used for Vessel Dossier sections and future Pinecone filter queries.  
`ChatSession` → `ChatMessage` — one-to-many, cascade delete. `session_id` is returned in chat responses and passed back by the frontend.

When adding columns, three places must be updated together — `db.create_all()` only creates *missing tables*, it never adds *new columns* to existing ones, so Neon will 500 on the first SELECT until the ALTER runs:

1. **`models.py`** — declare the column on the SQLAlchemy model (so fresh Neon deployments and `db.create_all()` get it).
2. **`migrate_db.py`** — add an idempotent `ALTER TABLE` for local SQLite (developers run `python migrate_db.py`).
3. **`app.py`** — append the same column to the `_pending_cols` list inside the boot-time migration block (`with app.app_context(): … ALTER TABLE … ADD COLUMN IF NOT EXISTS …`). This is what actually adds the column on Neon and on already-running Droplets at next deploy.

---

## Deployment

### Staging (Vercel)

`vercel.json` uses `@vercel/python` builder. No `functions` block — that conflicts with `builds`. Key constraints: read-only filesystem, 60s timeout, no persistent workers, no local file writes.

### Production (DO Droplets)

Each client gets a dedicated Droplet. See `grand_plan.md` Section 8 and `CI_CD_SETUP.md` for the manual provisioning runbook. `deploy.sh` is the bootstrap script. `docling-flask.service` is the systemd unit.

### CI/CD

GitHub Actions runs `python test_ci.py` on every push to `main`. The test file checks that all modules are importable and key model classes are present — it does **not** require external services. Vercel auto-deploys on push to `main`.

---

## Key Design Patterns

- **Vercel filesystem is read-only**: never write files to disk in application code. Use `tempfile` for scratch space, DO Spaces for persistence, DB for metadata.
- **Object storage is best-effort**: `documents/object_storage.py` wraps all calls in try/except. A Spaces failure logs a warning but never blocks the upload pipeline.
- **`production_config.py` is Droplet-specific**: `ProductionConfig` hardcodes `/opt/docling` paths. On Vercel, `FLASK_ENV` should be `development` or left unset so `DevelopmentConfig` is used.
- **Legacy scripts**: `embedding_uploader*.py`, `process_vessel*.py`, and `process_agency.py` are the original CLI pipeline. They are gitignored from Vercel (`.vercelignore`) and not imported by the Flask app except via lazy `try/except ImportError` guards in `app.py`.
- **`migrate_db.py` is SQLite-only**: for Neon, schema changes go in the SQLAlchemy model and `db.create_all()` handles it on next boot.
