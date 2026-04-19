# Grand Plan — Functional Specification Document
**Project:** Maritime AI Chatbot SaaS Platform
**Codename:** TBD (let's call it docling for now) 
**Last Updated:** April 2026

---

## 1. Executive Summary

docling is a whitelabel AI chatbot platform purpose-built for the maritime industry. It ingests vessel documentation (charter parties, fixture recaps, BIMCO addenda, cargo schedules) and makes that knowledge queryable through a natural-language chat interface. Each client receives a fully isolated, branded chatbot deployment trained exclusively on their own fleet data.

The platform is designed to start small — a single developer can onboard the first few clients manually — and scale toward a fully automated provisioning pipeline where a new client can be live within minutes of signing up.

---

## 2. The Problem We're Solving

Maritime operations teams deal with enormous volumes of semi-structured documents: charter party agreements that run 80+ pages, trading exclusion addenda, BIMCO clauses, speed and consumption tables, cargo restriction schedules. Answering a single operational question ("can this vessel trade to Venezuela under the current CP?") requires a lawyer or senior operator to dig through multiple documents manually.

docling turns that corpus into an always-available, always-accurate knowledge assistant. The chatbot can answer nuanced contractual questions, surface specific clauses, compare trading limits, and flag risk — in seconds, not hours.

---

## 3. Target Market & Clients

**Primary:** Ship operators, fleet managers, and chartering desks at mid-to-large shipowning companies.
**Secondary:** Ship management companies running third-party managed fleets.
**Tertiary:** P&I clubs, maritime law firms needing rapid clause lookup across large document sets.

Client data is inherently sensitive: charter party terms are commercially confidential, and some vessel trading restrictions carry legal and sanctions implications. This sensitivity is a core driver of our infrastructure decisions.

---

## 4. Core Architecture — How It Works

The platform is a Retrieval-Augmented Generation (RAG) system.

**Document Ingestion Pipeline**
1. Raw documents (`.docx`, `.pdf`, `.xlsx`) are placed in a source folder.
2. `process_vessel_new.py` extracts and structures the text using `DynamicTextExtractor`.
3. `embedding_uploader_new.py` chunks the text, generates vector embeddings via OpenAI `text-embedding-3-small`, and uploads them to Pinecone, scoped to the client's index/namespace.

**Chat Query Pipeline**
1. User submits a natural-language question in the chat UI.
2. The question is embedded using the same OpenAI model.
3. The top-K most semantically relevant document chunks are retrieved from Pinecone.
4. The retrieved chunks + conversation history are sent to Anthropic Claude as context.
5. Claude generates a grounded, document-aware response.
6. The response is streamed back to the user with markdown rendering.

**Key Technical Stack**
- **Backend:** Python 3, Flask, SQLAlchemy, PyJWT
- **LLM:** Anthropic Claude (claude-opus-4-6 default, configurable per client)
- **Embeddings:** OpenAI text-embedding-3-small
- **Vector DB:** Pinecone
- **App DB:** SQLite (development) → PostgreSQL (production)
- **Frontend:** Vanilla HTML/CSS/JS, marked.js for markdown, Inter font
- **Auth:** JWT access tokens (8h) + refresh tokens (30d); httpOnly cookie for CMS

---

## 5. Product Components

### 5.1 Chat Interface (`chat.html`)
The end-user facing product. Fully whitelabeled per client.

- Branding: logo, company name, chatbot name, color scheme
- Dark mode (client's configured brand colors) and Light mode (papaya orange `#F8A269` + `#F2F4F1`)
- Dark/light mode toggle persisted in `localStorage` key `'theme'`; shared with the CMS so switching in one is reflected when opening the other
- Empty state: welcome message + clickable suggested question chips
- Conversation history (in-session, browser memory)
- Copy-to-clipboard on assistant messages
- Auth bar: Sign In / Switch to CMS ↗ / Sign Out (sidebar footer)
- Markdown rendering: tables, code blocks, bullet lists, bold, blockquotes
- **Login gate:** the root `/` route checks the `cms_token` cookie and redirects unauthenticated visitors to `/login` before serving the chat UI

### 5.2 Chat API (`/api`)
Flask Blueprint handling all chat logic.

- `POST /api/chat/<client_id>` — RAG pipeline, returns streaming response
- `GET /api/config/<client_id>` — public branding/UX config for the frontend
- Client isolation enforced by namespace/index at query time

### 5.3 Auth System (`/auth`)
Shared JWT auth serving both the chat login and the CMS.

- `POST /auth/login` — returns access token + refresh token
- `POST /auth/refresh` — rotates tokens
- `POST /auth/logout` — invalidates session
- `GET /auth/me` — current user info
- Roles: `admin` (full platform CMS), `client_admin` (scoped CMS), `user` (chat only)

**Platform-level login (`app.py`):**
- `GET/POST /login` — standalone login page (`login.html`) that accepts all roles (admin, client_admin, user); sets the `cms_token` httpOnly cookie on success; honours a `?next=` redirect param; includes its own dark/light toggle
- `GET /logout` — clears the `cms_token` cookie and redirects to `/login`
- `GET /` — `_check_chat_cookie()` validates the cookie for any role; unauthenticated requests are redirected to `/login?next=/`
- `login.html` includes a footer link ("CMS Admin access →") pointing to `/cms/login` for operators who need CMS access

### 5.4 CMS & User Access Model

The platform uses a **3-tier role system** serving two distinct groups through one shared CMS codebase with data scoping enforced at the route layer.

#### Tier 1 — Platform Admin (`role: admin`)
The platform operator. Full, ungated access to everything.

- Sees all clients in the dashboard; can create, edit, activate, deactivate, and delete any client
- Controls all client settings including AI & Pinecone config, LLM model selection, system prompt, and the Active flag
- Sees all users across all clients; can promote users to any role
- The only role that can create new clients or assign Platform Admin access to others

#### Tier 2 — Client Admin (`role: client_admin`)
The client's internal CMS operator — a ship manager or IT contact at the client company. Access is scoped strictly to their own client record.

- Sees only their own client in the dashboard; cannot view or access other clients
- Can edit branding (colors, logo, chatbot name) and Chat UX (welcome message, suggested questions, default theme)
- Cannot modify AI/Pinecone configuration, system prompt, or the Active flag — those are platform-controlled
- Can create and manage users for their own client only; cannot create Client Admins or Platform Admins
- Sees only their own client's users ("Your Team" view)

#### Tier 3 — User (`role: user`)
End users of the chat interface. No CMS access at all.

- Authenticate via `POST /auth/login` using a Bearer token
- Access is scoped to the chat interface for their assigned `client_id`
- Cannot log into the CMS (`/cms/login` rejects non-admin/non-client_admin roles)
- Managed by Platform Admins and Client Admins via the CMS user panel

#### Technical Implementation
Scoping is enforced in `cms/routes.py` via three request-context helpers carried on Flask's `g` object:

- `cms_required` decorator: validates the `cms_token` httpOnly cookie, loads the user, sets `g.is_platform_admin` and `g.scoped_client_id`
- `platform_admin_required` decorator: wraps `cms_required`, aborts to dashboard with error flash if not platform admin
- `_assert_client_access(client)`: called inside client edit routes; raises HTTP 403 if a client_admin tries to access another client's record

All database queries in the dashboard and user management routes are branched on `g.is_platform_admin`. The `_save_client()` function splits editable fields into two groups: branding/Chat UX (editable by both tiers) and AI config/system prompt/active flag (platform admin only), ignoring the second group if the request comes from a client admin.

#### CMS Route Summary

| Route | Platform Admin | Client Admin |
|---|---|---|
| `GET /cms/` — Dashboard | All clients + all users | Own client + own team |
| `GET/POST /cms/clients/new` | ✅ Create any client | 🚫 Blocked |
| `GET/POST /cms/clients/<id>/edit` | ✅ All fields | ✅ Branding + Chat UX only |
| `POST /cms/clients/<id>/toggle` | ✅ Activate/deactivate | 🚫 Blocked |
| `POST /cms/clients/<id>/delete` | ✅ Delete | 🚫 Blocked |
| `GET/POST /cms/users/new` | ✅ Any role, any client | ✅ User role, own client only |
| `POST /cms/users/<id>/toggle` | ✅ Any user | ✅ Own client's users only |

### 5.5 Admin CMS — Dashboard & Forms (`/cms`)

- **Dark/light mode toggle** in the CMS topbar (moon/sun icon); reads and writes the same `localStorage` key `'theme'` as the chat UI so the theme is shared across both. `[data-theme="light"]` block applies a light sidebar (`#f8f9fb` background, dark text) with dark-overlay hover states.
- **Cross-link** in sidebar footer: "Switch to Chat ↗" opens the chat root in a new tab; the chat sidebar shows "Switch to CMS ↗" reciprocally.
- **Dashboard:** client list with status badges, user list with role badges, quick stats (total/active clients, total users)
- **Client form:** Identity, Branding, Chat UX (all roles); AI & Knowledge Base, System Prompt (platform admin only)
  - Identity: client ID (slug, immutable after creation), display name, company name, chatbot name, logo URL
  - Branding: primary/secondary/accent/text colors with live color pickers
  - Chat UX: welcome message, suggested questions (one per line → stored as JSON array), default theme, show/hide mode toggle
  - AI config: Pinecone index, namespace, LLM model, embedding model, context chunks, history turns
  - System prompt: full editable prompt with `[PLACEHOLDER]` token system
- **User form:** Role and client assignment (platform admin full control); read-only for client admins
- Auth: httpOnly `cms_token` cookie, 8h session, redirects to `/cms/login` when expired

### 5.6 Document Upload Pipeline (`/documents`)

A human-in-the-loop upload interface for building and maintaining each client's knowledge base. Accessible to Platform Admins and Client Admins; end users (chat-only) cannot upload.

**Workflow:**

1. **Upload** — drag-and-drop or file-browse; accepts `.docx`, `.pdf`, `.xlsx`; supports multiple files in a single session. An optional Group / Vessel Name label ties related files together in the library. A **Skip AI Enrichment** toggle (see step 2b) is available on both the library uploader and the Vessel Dossier full-document zone.
2. **Extract** — `documents/extractor.py` parses each file into discrete chunks using clause-aware detection (matches `CLAUSE N`, `N.`, `PART II`, `ANNEX A` patterns common in charter parties). Each chunk gets a title and body.
2b. **AI Enrichment** (`documents/ai_enrichment.py`) — after extraction, raw chunks are sent to `gpt-4o-mini` via a structured JSON prompt that instructs the model to assign concise 2–6 word titles, split dash-separated clause lists into individual chunks, and group specification parameters into logical units. The model returns `{"chunks": [...]}` and the enriched list replaces the raw extraction output. If enrichment fails for any reason, the system falls back gracefully to the raw extractor output. **Skip enrichment** flag: when the `skip_enrichment` form field is `on`, this step is bypassed entirely — recommended for large, structured integrated documents (hundreds of chunks) where enrichment would add significant latency and the extraction is already clean.
3. **Review & Edit (sequential)** — the user reviews each file one at a time. A "File X of N" banner tracks progress through the batch. They can correct titles, fix parser errors, delete junk chunks, or add chunks manually. Nothing is sent to Pinecone until this step is approved per file.
4. **Save & Continue** — `documents/embedder.py` embeds each chunk via OpenAI `text-embedding-3-small` and upserts to the client's Pinecone namespace. After saving, the system automatically advances to the next file in the queue. The save button reads "Save & Review Next →" until the last file, then "Save to Knowledge Base →".
5. **Library** — shows all documents grouped by their Group / Vessel Name label. Each group shows as a folder-style header. Status per file: Draft / Live / Processing / Error. Delete removes both the DB record and all associated Pinecone vectors.

**DB models:**
- `Document` — one row per uploaded file; tracks filename, type, status, chunk_count, group_name (optional grouping label), vessel_id (FK to Vessel), document_category (slug key for Vessel Dossier sections, e.g. `fixture_recap`, `charter_party`, `full_document`), uploaded_by, uploaded_at, activated_at
- `DocumentChunk` — one row per embeddable chunk; stores title, body, position, and pinecone_id after embedding

**Pinecone vector metadata (per chunk):**
```python
{
  "client_id":         document.client_id,
  "document_id":       document.id,
  "filename":          document.filename,
  "chunk_title":       chunk.title or "",
  "chunk_position":    chunk.position,
  "document_category": document.document_category or "",
  "vessel_id":         document.vessel_id or 0,
  "group_name":        document.group_name or "",
  "text":              chunk_text,
}
```
`document_category` and `vessel_id` are stored in metadata to enable future category-scoped or vessel-scoped retrieval filtering at query time.

**Multi-file queue mechanism:**
The upload route accepts `files[]` (multiple), extracts all files server-side, creates a `Document` record per file, then redirects to the first preview with a `?queue=id1,id2&total=N` query string. Each preview passes queue/total through hidden form fields into the save POST. After a successful save, the save route checks the queue and redirects to the next doc's preview or, when empty, to the library. This keeps the per-file review flow unchanged while enabling batch uploads.

**PDF extraction & encoding:**
- Primary extractor: PyMuPDF (`fitz`) using `get_text("dict")` — reads every text span with its font size and bold flag, enabling accurate section header detection for both contract and vessel-description PDFs.
- Fallback extractor: pdfplumber — clause-regex detection only (no font metadata), used if PyMuPDF is not installed.
- Post-extraction cleaning (`_clean_pdf_text()`): applied per line before chunking; two-pass:
  1. **NFKC normalisation** — decomposes standard Unicode ligatures (ﬁ→fi, ﬂ→fl, ﬀ→ff, ﬃ→ffi, ﬄ→ffl) into ASCII sequences.
  2. **BIMCO substitution map** — corrects wrong-codepoint ligature mappings specific to BIMCO SmartCon and similar commercial charter-party fonts whose ToUnicode CMap incorrectly maps ti/ft/tt ligature glyphs to Latin Extended codepoints (Ɵ→ti, Ō→ft, Ʃ→tt). The map is a module-level `dict` (`_BIMCO_LIGATURE_MAP`) for easy extension if new artifacts are discovered.

**Chunking strategy (DOCX):**
Header detection uses three signals, checked in order:
1. **Heading style** (`"heading" in style_name`) — the preferred template format (AI_EVA_MARIE.docx uses `Heading 1` for every section).
2. **Clause regex** (`_CLAUSE_RE`) — numbered clauses in charter party contracts.
3. **Subsection label** (`_is_docx_subsection_label`) — short (≤ 80 chars) lines that end with `:` and have no value after the colon, start uppercase, and contain no `: ` key-value separator. Catches section headers in vessel-description DOCX files that use `paragraph` style with labels like `"General Information:"`, `"Tonnage:"`, `"Dimensions:"`. Does not match content data lines like `"Call Sign: PEVT"`.

**Chunking strategy (PDFs) — column-aware:**

`_extract_pdf_fitz()` uses a two-stage approach: layout detection then semantic chunking.

**Stage 1 — Column detection (`_detect_column_split`)**
Divides the page width into 20 pt bins and counts elements per bin. Searches the central 15–80 % of the page for the longest contiguous run of "sparse" bins (≤ 2 % of total elements). A run of ≥ 3 bins (≥ 60 pt) is treated as a column gap; its midpoint becomes the split x-coordinate. A single isolated element (e.g. a centred title "Ship's particulars") sitting in the gap is counted as sparse and does not break the detection. Returns `None` for single-column documents.

**Stage 2 — Row grouping + header detection (`_column_to_chunks`)**
Elements are sorted by (y, x). Elements whose y-coordinates are within 3 pt of each other are grouped into the same visual row (sub-pixel baseline jitter). For each row, any one of these signals triggers a new chunk boundary:
1. **Bold + short + uppercase-initial** — all spans bold, row < 120 chars, starts with an uppercase letter. This is the primary signal for vessel spec sheets where headers and body text share the same font size (10 pt) but headers are bold.
2. **Font size** ≥ body_size × 1.12 — catches visually enlarged headers in contract PDFs.
3. **Clause regex** (`_CLAUSE_RE`) — numbered clauses in charter parties (`CLAUSE N`, `N. Title`, `PART II`, `ANNEX A`). Requires a space then a letter after the numeric separator to prevent decimal measurements (6.438, 16.0 m) from matching.

**Noise filter — MIN_FONT = 7 pt**
Spans with a max font size below 7 pt are dropped before layout analysis. This removes sub-millimetre text from cargo plan diagrams (pages 2–7 of OCEAN7-style spec PDFs) while preserving useful 8–10 pt annotation text (tween deck / tanktop hold heights).

**Junk chunk filter (`_is_junk_body`):**
Chunks are discarded before saving if:
- < 3 total words, OR
- "> 60 % of lines are ≤ 2 characters (catches slot-plan / hold-diagram position labels A B C … 1 2 3 … extracted from graphical vector drawings)."

**Validated output (MV ADRIATIC — OCEAN7 PDF, page 1):**
The bin-density column detector finds the split at x ≈ 240 pt and produces 11 semantically correct chunks matching the `AI_ADRIATIC.docx` single-file pipeline output: Registration, Tonnage, Dimensions, Hold/hatch sizes, Container capacity, Deck loads, Hatch covers/Tween deck, RoRo feature, Propulsion/Maneuvering, Bunkers/Ballast capacity — each with correct key-value body content. Later pages (cargo plans) yield additional tween-deck/tanktop dimensional chunks plus some residual noise that users can delete in the Review & Edit step.

**Key design decisions:**
- Extraction is synchronous per file (fast enough for charter parties; no async/SSE needed in Phase 2)
- The review step is the differentiator — operators can catch parser misses before they corrupt the knowledge base
- Multi-file is handled as a sequential review queue rather than a parallel batch, keeping the review UI simple and focused
- Grouping is an optional label, not a strict schema — operators can upload ungrouped files, or group by vessel, charter party package, or any other logical unit
- Deletion is clean: vector IDs are deterministic (`{client_id}:doc:{doc_id}:chunk:{pos}`) and stored on each chunk row, enabling targeted Pinecone deletes
- Platform Admins see a client-switcher dropdown; Client Admins are auto-scoped to their own client

### 5.7 Vessel Dossier (`/documents/vessel/<id>/dossier`)

A structured per-vessel document management page, accessible from the Vessel Library via the "Manage Docs" button on each row. Provides a single location to manage all documents for a vessel, organized into fixed categories.

**Layout:**
- **Vessel header card** — avatar, name, type, IMO, flag, year/GT/DWT/LOA chips; "Edit Vessel" link to the Vessel Library drawer
- **Progress ring** — SVG ring (stroke-dasharray) showing the percentage of the 10 sections with status `verified`; counter "N / 10 sections verified"
- **Full Document Package zone** (top-level, above accordion) — for uploading an integrated vessel document containing all sections. Shows previously-uploaded full-document files with status badges and Review links. Includes the Skip AI Enrichment toggle (defaults to **on** for this zone since full packages are typically large and structured). Stored with `document_category = "full_document"`.
- **10-section accordion** — collapsible rows for each document category; opens automatically if status is `in_progress`

**The 10 fixed sections (slug → label):**

| Slug | Label |
|---|---|
| `vessel_specifications` | Vessel Specifications |
| `addendum` | Addendum |
| `fixture_recap` | Fixture Recap |
| `charter_party` | Charter Party |
| `delivery_details` | Delivery Details |
| `speed_consumption` | Speed & Consumption |
| `inventory` | Inventory |
| `lifting_equipment` | Lifting Equipment |
| `hseq_documents` | HSEQ Documents |
| `vessel_owners_details` | Vessel & Owners Details |

**Section status** (derived live from DB, not stored separately):
- `not_started` — no documents for this category
- `in_progress` — at least one document exists but not all are `active`
- `verified` — all documents for this category are `active`

**Section body (expanded):**
- Existing documents list: filename, chunk count, Live/Draft/Error badge, Review link for drafts
- Per-section upload drop zone: drag-and-drop or click-to-browse; auto-submits on file selection; passes `document_category`, `vessel_id`, `from_vessel` as hidden fields

**Back-navigation:** after Save Draft or Publish from the Review step, the user is returned to the Dossier (not the library) when `from_vessel` is set. The preview topbar shows "← Back to Vessel Dossier" in place of "← Back to Library".

**`document_category` slug** is stored on the `Document` record and in Pinecone vector metadata, enabling future category-scoped retrieval queries (e.g. "search only within charter_party documents for this vessel").

### 5.8 Usage Logging & Rate Limiting


Every chat request is logged to a `usage_logs` table for billing, monitoring, and abuse prevention.

**Data captured per request:**
- `client_id` — which client the request belongs to
- `user_email` — extracted from the Bearer JWT if present (null for anonymous)
- `timestamp` / `date` — wall-clock time and indexed date column for fast daily queries
- `tokens_in` / `tokens_out` — prompt and completion token counts from the Anthropic response object
- `model` — the LLM model used (per-client configurable)
- `response_ms` — end-to-end latency in milliseconds

**Rate limiting:**
Each client can be assigned a `daily_request_limit` (0 = unlimited) via the CMS client form (platform admin only). Before processing a chat request, the API checks today's `UsageLog` row count for that client. If the limit is exceeded, it returns HTTP 429 with a user-readable message.

**Key design decisions:**
- A separate indexed `date` column is used instead of date-truncating `timestamp`, because SQLite cannot index computed expressions. This makes `COUNT(*) WHERE date = today` a fast index scan.
- Token counts are read directly from `response.usage.input_tokens` / `response.usage.output_tokens` on the non-streaming Anthropic response object.
- The daily limit check uses the same `date` column, making it O(1) with the index rather than a full table scan.
- The user email is extracted from the JWT silently (no auth required for chat) — if no token is present, `user_email` is stored as NULL.

### 5.8 Analytics Dashboard (`/cms/analytics`)

A dedicated analytics page providing time-series and ranked usage data. Access is role-scoped: platform admins see all clients, client admins see only their own.

**Summary cards (month-to-date):**
- Total requests
- Tokens in (prompt tokens consumed)
- Tokens out (completion tokens generated)
- Average response time (ms)

**Charts (last 30 days, Chart.js):**
- Daily requests — bar chart, one bar per day
- Daily tokens — area/line chart with K-formatted y-axis
- Requests by client — horizontal bar chart (platform admin only), coloured per client

**Top users table (month-to-date):**
Ranked list of up to 10 users by request count, showing email, total requests, tokens consumed, and average response time. Anonymous (unauthenticated) requests are grouped under "(anonymous)".

**Route:** `GET /cms/analytics` — `@cms_required`, data scoped via `g.is_platform_admin` / `g.scoped_client_id`.

**Key design decisions:**
- All chart data is passed from the route as JSON-safe Python lists; Chart.js is loaded from cdnjs CDN with no build step.
- Missing days (zero-activity gaps) are filled in Python before the template renders, so the x-axis always shows a continuous 30-day range.
- Token values are formatted client-side (K/M suffix) in Chart.js tick callbacks and in a reusable Jinja macro (`fmt_tokens`) in the template.

### 5.10 System Prompt (`prompt.md`)
The instruction set defining how Claude behaves for a given client. Whitelabeled via placeholder tokens:

- `[CLIENT_NAME]` — the client's company/fleet name
- `[ZERONORTH_ONBOARDING_EMAIL]` — or equivalent contact
- `[CLIENT_VESSEL_ALIASES]` — vessel name variants to recognize
- `[SISTER_VESSEL_MAPPINGS]` — cross-vessel clause inheritance rules
- `[CLIENT_SPECIFIC_SEARCH_RULES]` — custom logic for this fleet's document structure

---

## 6. Development Phases

### Phase 1 — Chat API 
Single-tenant Flask app with hardcoded client config, full RAG pipeline, chat UI, JWT auth.

**Deliverables completed:**
- `app.py`, `chat_routes.py`, `auth.py`, `models.py`, `client_config.py`
- `templates/chat.html` — fully polished chat UI
- Subdomain-based client routing with IP detection fix
- Dark/light mode theming system

### Phase 2 — Admin CMS 
Database-driven client management. Same Flask app, new `/cms` blueprint.

**Deliverables completed:**
- `models.py` — `ClientConfig`, `User`, `Document`, `DocumentChunk`, `Vessel` SQLAlchemy models; `Document.document_category` column for Dossier section scoping
- `cms/routes.py` — full CRUD for clients and users; 3-tier role scoping
- CMS templates: dashboard, client form (with Chat UX section), user form, login
- `client_config.py` updated to query DB first, fall back to hardcoded registry
- `documents/` Blueprint — full document upload pipeline (see Section 5.6)
- `documents/ai_enrichment.py` — gpt-4o-mini post-processing pass for title assignment and clause-level splitting; graceful fallback; skip-enrichment flag (see Section 5.6 step 2b)
- `documents/embedder.py` — updated Pinecone metadata to include `document_category`, `vessel_id`, `group_name` per chunk (see Section 5.6)
- `documents/vessel_dossier.html` — 10-section accordion Vessel Dossier UI with per-section upload zones, SVG progress ring, and Full Document Package top-level zone (see Section 5.7)
- `migrate_db.py` — idempotent schema migration script for adding columns to existing DBs
- `UsageLog` model — per-request logging (client, user, tokens, latency)
- Rate limiting — per-client daily request cap with 429 enforcement (see Section 5.8)
- Analytics dashboard — `/cms/analytics` with Chart.js charts and top-users table (see Section 5.9)
- User edit / password reset — `/cms/users/<id>/edit` for admin-side credential management
- Role-scoped sidebar — nav items hidden (not just disabled) based on user role
- Dark/light mode toggle in CMS — synchronized with Chat via shared `localStorage` key; light-mode sidebar fully themed (see Section 5.5)
- Login gate for root `/` route — `login.html` standalone page, `/login` and `/logout` routes, all-role cookie validation (see Section 5.3)
- Vessel Library "Manage Docs" button — links each vessel row directly to its Dossier page
- Skip AI Enrichment toggle — on both the library uploader and the Dossier Full Document Package zone

**Remaining CMS items:**
- Embed code generator (iframe/script snippet for client's own website)
- Preview button (open a sandboxed chat window from within the CMS)
- Duplicate client (clone config as starting point for new client)
- Subdomain display with copy-to-clipboard
- "Powered by" control (show/hide platform attribution in chat UI)
- Category-scoped retrieval — use `document_category` filter in Pinecone queries (groundwork is in place; query logic not yet implemented)
- Batch upload placement for Vessel Dossier sections (UX decision pending)

### Phase 3 — Production Hardening & Automation
Gunicorn + systemd deployment, nginx routing, automated provisioning.

**Deliverables planned:**
- Gunicorn WSGI server config with systemd unit file
- nginx reverse proxy config with subdomain routing and SSL (Let's Encrypt)
- Switch from SQLite to PostgreSQL (connection string change in `.env`)
- Automated provisioning script (Phase 3B — see Section 8)
- Ops monitoring dashboard

---

## 7. Infrastructure — Environments

### 7.1 Local Development

```
Developer Machine
└── Flask dev server (port 8080)
    ├── / → chat.html (served for all client subdomains via ?client= param in dev)
    ├── /api → chat_routes.py (RAG pipeline)
    ├── /auth → auth.py (JWT)
    └── /cms → cms/routes.py (admin dashboard)

External Services (shared, dev)
├── Pinecone — single index, namespace-per-client
├── OpenAI API — embeddings
└── Anthropic API — LLM

Database: SQLite (platform.db) — local file, not committed to git
```

### 7.2 Staging / Demo Environment (Current — April 2026)

A shared, low-ops deployment used for internal testing, feature validation, and demonstrating the product to prospective clients before they sign. **Not suitable for real client data** — see constraints below.

```
Vercel (Serverless, @vercel/python)
└── app.py — single serverless function serving all routes
    ├── maxDuration: 60s, memory: 1024 MB
    ├── / → chat.html
    ├── /api → chat_routes.py (RAG pipeline)
    ├── /auth → auth.py (JWT)
    ├── /cms → cms/routes.py (admin dashboard)
    └── /documents → documents/routes.py (upload pipeline)

GitHub → Vercel CI/CD
├── Push to main → automatic Vercel redeploy (< 30s)
└── Two Vercel projects connected to same repo:
    ├── docling-fleet         — primary staging instance
    └── docling-fleet-blue    — secondary / blue-green slot

External Services (shared across all staging clients)
├── Neon PostgreSQL — single managed database, client-scoped by application logic
├── DigitalOcean Spaces — single bucket, objects namespaced as documents/{client_id}/
├── Pinecone — single index, namespace-per-client
├── OpenAI API — embeddings (shared API key)
└── Anthropic API — LLM (shared API key)
```

**Environment variables (set in Vercel dashboard):**

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | Neon PostgreSQL connection string |
| `DEFAULT_CLIENT_ID` | Client slug for the deployment URL (e.g. `test-client`) |
| `OBJECT_STORAGE_ENDPOINT` | DO Spaces endpoint URL |
| `OBJECT_STORAGE_ACCESS_KEY` | DO Spaces access key |
| `OBJECT_STORAGE_SECRET_KEY` | DO Spaces secret key |
| `OBJECT_STORAGE_BUCKET` | DO Spaces bucket name |
| `PINECONE_API_KEY` | Pinecone API key |
| `OPENAI_API_KEY` | OpenAI API key |
| `ANTHROPIC_API_KEY` | Anthropic API key |
| `JWT_SECRET` | JWT signing secret |
| `ADMIN_EMAIL` / `ADMIN_PASSWORD` | Seeded platform admin credentials |

**Client routing on staging:**
- Vercel deployment URLs (e.g. `docling-fleet-blue.vercel.app`) fall through to `DEFAULT_CLIENT_ID` — the subdomain extractor skips known hosting-platform domains.
- Custom subdomains (e.g. `demo.vesfleet.ai`) still route via subdomain detection as intended.
- Explicit `?client=xxx` query param always overrides both.

**Known constraints of the staging environment:**

| Constraint | Detail |
|---|---|
| Shared database | All test clients share one Neon DB — isolation is application-level only, not infrastructure-level |
| Shared object storage | All uploaded files land in one DO Spaces bucket, namespaced by client_id |
| Serverless cold starts | First request after inactivity may take 2–5 s to boot the Python runtime |
| 60 s function timeout | Long document processing (large PDFs + AI enrichment) may time out |
| Read-only filesystem | No local file writes — all persistence must go through the DB or object storage |
| No persistent workers | Background jobs, scheduled tasks, and long async pipelines are not supported |
| Not suitable for real client data | Charter parties and sanctions-relevant documents must not be uploaded here |

**Intended uses:**
- Internal feature testing and QA after each push to `main`
- Live product demos for prospective clients using synthetic or anonymised vessel data
- Onboarding flow validation before provisioning a client's dedicated Droplet

---

## 8. Infrastructure — Target State (Production)

This is the non-negotiable architectural direction. Every client gets fully isolated infrastructure. No data commingling.

### 8.1 Per-Client Stack

Each onboarded client receives:

| Resource | Details |
|---|---|
| Digital Ocean Droplet | Dedicated VPS (e.g. 2 vCPU / 4 GB RAM to start) running the Flask app via Gunicorn + systemd |
| PostgreSQL Database | DO Managed Database (or on-droplet for smaller clients) |
| Pinecone Index | One index per client (not just a namespace) |
| DO Spaces Bucket | Object storage for raw source documents (future) |
| Subdomain + SSL | `clientname.platform.com` with Let's Encrypt cert via nginx |
| `.env` file | All API keys and secrets isolated to their Droplet |

**Why per-Droplet instead of containers on a shared host:**
Maritime clients handle commercially sensitive and potentially sanctions-relevant data. Full infrastructure isolation is both a security requirement and a sales argument. When a client asks "where does my data go?", the answer is: to a server that only you share with nobody.

### 8.2 Ops Control Plane (separate Droplet)

A single "ops" Droplet that the platform operator (you) manages. It is not client-facing.

- Monitoring dashboard: uptime, API call volume, Pinecone index size, last activity
- Alerts: droplet unreachable, high error rate, approaching API cost thresholds
- Client registry: maps `client_id` → Droplet IP, subdomain, DB connection string
- Provisioning scripts (Phase 3B)

The ops Droplet polls the Digital Ocean API and each client's `/health` endpoint. It does not have access to any client data or API keys.

### 8.3 Phase 3B — Automated Provisioning

When the CMS operator clicks "Provision" for a new client, a script runs that:

1. Creates a new Droplet via the DO API
2. Runs a bootstrap script (SSH): installs Python, nginx, clones the app repo
3. Populates `.env` with the client's API keys (Pinecone, OpenAI, Anthropic, JWT secret)
4. Creates the client's Pinecone index
5. Provisions the PostgreSQL database and runs migrations
6. Configures nginx with the client's subdomain
7. Issues an SSL certificate via Certbot
8. Starts the Gunicorn systemd service
9. Registers the client in the ops control plane

**First clients: do this manually.** Run the steps yourself for the first 2–3 clients. Document every step. The script is written from that runbook, not the other way around.

### 8.4 LLM Resilience — Local Model Fallback (Droplet Phase)

**Problem:** The platform's core functionality depends on two external AI services — OpenAI (embeddings + enrichment) and Anthropic (chat LLM). If either goes down, the service degrades or stops entirely.

**Solution:** Run [Ollama](https://ollama.com) as a background service on each client Droplet. Ollama serves open-source models (Llama 3, Mistral, Qwen) via an **OpenAI-compatible API** at `http://localhost:11434/v1`. The application code calls the primary service and, on failure, retries against the local endpoint — no vendor lock-in, minimal code change.

**Why this only makes sense on Droplets (not Vercel):**
Vercel is serverless — there is no persistent process to run a model server. This feature is exclusively a Droplet-phase concern.

**Recommended models per use case:**

| Use Case | Primary | Fallback Model | Droplet RAM needed |
|---|---|---|---|
| Chat (RAG Q&A) | Anthropic Claude | `llama3.1:8b` (~5 GB, 4-bit) | 8 GB |
| Document enrichment | OpenAI gpt-4o-mini | `mistral:7b` (~5 GB, 4-bit) | 8 GB |
| Embeddings | OpenAI text-embedding-3-small | `nomic-embed-text` (~1.5 GB) | 4 GB |

A standard 8 GB RAM Droplet (~$48/mo) runs the chat and enrichment fallback models comfortably on CPU. Inference is slower than the hosted APIs (~10–20 tok/s vs ~100+ tok/s) but acceptable — enrichment is a background task, and chat users can tolerate slightly longer response times during an outage.

**Implementation sketch (circuit breaker pattern):**
```python
def call_chat_llm(messages, client_config):
    """Try Anthropic first; fall back to local Ollama if unreachable."""
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        # ... existing chat call ...
    except Exception as e:
        logger.warning(f"Anthropic unavailable ({e}), switching to local fallback")
        import openai
        local = openai.OpenAI(base_url="http://localhost:11434/v1", api_key="ollama")
        return local.chat.completions.create(model="llama3.1:8b", messages=messages)

def call_enrichment_llm(prompt):
    """Try OpenAI gpt-4o-mini first; fall back to local Mistral."""
    try:
        import openai
        client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        # ... existing enrichment call ...
    except Exception as e:
        logger.warning(f"OpenAI unavailable ({e}), switching to local fallback")
        local = openai.OpenAI(base_url="http://localhost:11434/v1", api_key="ollama")
        # ... same call, different base_url ...
```

**Ollama setup on a new Droplet (add to provisioning script in Phase 3B):**
```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull llama3.1:8b
ollama pull mistral:7b
ollama pull nomic-embed-text
systemctl enable ollama
```

**Key decisions deferred to implementation:**
- Whether to use a hard fail-fast (try once, fall back immediately) or a retry-with-backoff before fallback
- Whether to surface the fallback status to the user ("Responding in offline mode — answers may be less precise")
- Whether embeddings fallback is worth the complexity (mismatched embedding spaces between OpenAI and nomic-embed-text would require re-indexing the entire Pinecone namespace; this may be impractical mid-operation)

**Priority:** Implement during or after the Droplet migration (Phase 3). Do not attempt on the Vercel staging environment.

---

## 9. Security Architecture

### 9.1 Data Isolation
- No shared databases. No shared Pinecone indexes.
- Namespace-level isolation is a fallback only; production uses separate indexes.
- Client API keys (Anthropic, OpenAI, Pinecone) are stored in per-Droplet `.env` files, never in a shared database.

### 9.2 Third-Party Data Processors
The following services receive client document data as part of the RAG pipeline:

| Service | What it receives | Mitigation |
|---|---|---|
| OpenAI Embedding API | Plain text chunks from client documents | API terms prohibit training on API data; execute DPA with client |
| Pinecone | Vector embeddings + metadata | Metadata should contain minimal raw text; review `embedding_uploader_new.py` |
| Anthropic Claude API | Retrieved chunks + user query | Same as OpenAI; Anthropic has zero data retention on API |

**Action required before first paid client:** Establish Data Processing Agreements (DPAs) with OpenAI, Pinecone, and Anthropic. Present sub-processor list to clients for sign-off. If a client has data residency requirements, evaluate self-hosted embedding (e.g. `nomic-embed-text`) and self-hosted vector store (Qdrant or Weaviate).

### 9.3 Application Security
- Passwords: bcrypt hashed, never stored in plaintext
- Tokens: JWT with short-lived access (8h) + refresh (30d), httpOnly cookies for browser sessions
- Input: all query parameters server-validated; client ID never user-supplied in production (derived from subdomain)
- Rate limiting: per-client configurable caps (Phase 2 CMS feature)
- Database: PostgreSQL in production with encrypted connections and user-level auth

### 9.4 Infrastructure Security
- Each Droplet: SSH key auth only, no password login, UFW firewall (allow 80, 443, 22)
- nginx: terminates SSL, proxies to Gunicorn on localhost only
- `.env` files: never committed to git, rotated on staff departure
- Ops Droplet: not client-accessible, admin auth required

---

## 10. Whitelabeling System

The platform is designed so that clients never see the platform brand unless you choose to surface it.

**Runtime whitelabeling (per client, CMS-controlled):**
- Chatbot name and company name
- Logo (URL or initials fallback)
- Primary, secondary, accent, and text colors (dark mode)
- Light mode always uses the platform's papaya orange palette
- Custom welcome message
- Custom suggested questions (up to 4 chips)
- Default theme (dark/light)
- Show/hide mode toggle
- "Powered by" attribution (CMS toggle, Phase 2)

**System prompt whitelabeling:**
- Each client's prompt is customized via `[PLACEHOLDER]` tokens in the CMS
- The prompt encodes client-specific vessel aliases, sister vessel mappings, ZeroNorth contacts, and domain-specific search rules

---

## 11. Billing Model (Proposed)

Clients are billed for their infrastructure and API usage. The platform operator passes through costs with a margin.

| Cost Component | Rough Monthly | Billed To |
|---|---|---|
| DO Droplet (2 vCPU / 4 GB) | ~$24 | Client (passed through) |
| DO Managed PostgreSQL | ~$15 | Client (passed through) |
| Pinecone Serverless | Usage-based, ~$0.10–$2/month for typical fleet | Client (passed through) |
| OpenAI Embeddings | Negligible after initial upload | Client (passed through) |
| Anthropic Claude API | ~$5–$50/month depending on query volume | Client (passed through) |
| Platform SaaS fee | TBD — suggested $200–$500/month | Client |

The platform margin on infrastructure pass-through is justified by the operational overhead and the managed service guarantee.

---

## 12. What "Done" Looks Like

The platform is production-ready when:

- [ ] A new client can be onboarded in under 30 minutes (Phase 3A manual, Phase 3B automated)
- [ ] Each client has a fully isolated infrastructure stack
- [ ] DPAs are executed with OpenAI, Pinecone, and Anthropic
- [ ] The ops monitoring dashboard shows uptime and API usage for all clients
- [ ] The CMS can update a client's system prompt and branding without touching the server
- [ ] SSL, subdomain routing, and Gunicorn are configured and stable
- [ ] The platform has been tested end-to-end with at least one real vessel document corpus

---

## 13. Open Questions & Decision Log

| Question | Decision | Date |
|---|---|---|
| Shared vs. per-client Droplet? | Per-client Droplet. Data isolation requirement outweighs cost and complexity. | Apr 2026 |
| Shared vs. per-client Pinecone index? | Per-client index. Namespace-only isolation has cross-leak risk on bugs. | Apr 2026 |
| SQLite vs. PostgreSQL? | SQLite for dev only. PostgreSQL for all production deployments. | Apr 2026 |
| Self-hosted embeddings? | OpenAI for now. Evaluate self-hosted if a client has data residency requirements. | Apr 2026 |
| Container orchestration (K8s/Docker Swarm)? | Deferred. Direct Droplet per client is simpler to operate at current scale. Revisit at 20+ clients. | Apr 2026 |
| Single CMS vs. separate admin/client portals? | Single CMS codebase with route-level data scoping. Platform Admin and Client Admin share the same UI; capabilities differ by role. Simpler to maintain, no duplicated templates. | Apr 2026 |
| How many CMS role tiers? | Three: Platform Admin (full access), Client Admin (scoped to own client), User (chat only, no CMS). Client Admins can manage branding and their team but cannot touch AI config or system prompts. | Apr 2026 |
| AI enrichment — which documents, which model? | All documents, automatically, using gpt-4o-mini. Cost is not a concern at current scale. The enrichment step is a post-processing pass on raw extractor output, not a replacement. Fallback to raw output on any failure. | Apr 2026 |
| AI enrichment — opt-out mechanism? | Skip AI Enrichment toggle (checkbox) on both the library uploader and the Dossier Full Document Package zone. Defaults to off (enrichment on) in the library; defaults to on (skip) in the Dossier full-document zone since those uploads are typically large and already well-structured. | Apr 2026 |
| Vessel Dossier section count and structure? | 10 fixed sections defined as `DOCUMENT_SECTIONS` in `documents/routes.py`. Sections are not user-configurable at this stage. A `full_document` slug exists outside the 10 sections for integrated packages. | Apr 2026 |
| document_category storage strategy? | Stored as a slug string on `Document.document_category` and in Pinecone vector metadata. Not a FK to a categories table — the 10 slugs are defined as a constant, keeping the schema simple and migration-free when sections are added. | Apr 2026 |
| Login gate scope? | All routes — Chat root `/`, CMS `/cms`, and Documents `/documents` — require a valid `cms_token` cookie. End users (role: user) are accepted at `/login` and can reach the chat UI. Only admin/client_admin roles can access the CMS. | Apr 2026 |
| Staging environment — Vercel vs DO App Platform vs Droplet? | Vercel chosen for staging/demo only. Zero ops overhead, instant deploys from GitHub, sufficient for synthetic data demos. Acknowledged limitations: shared infrastructure, serverless constraints, not suitable for real client data. Per-client Droplets remain the non-negotiable production target. | Apr 2026 |
| Staging database — shared Neon vs per-client? | Single shared Neon PostgreSQL for staging. Acceptable because staging only holds synthetic/test data. Production always gets a dedicated database per client. | Apr 2026 |
| Object storage — staging vs production bucket strategy? | Single DO Spaces bucket for staging with `documents/{client_id}/` namespacing. Each production client gets their own bucket as a line item. The `object_storage.py` module is already wired — only the bucket env var changes per deployment. | Apr 2026 |
| LLM resilience — self-hosted fallback? | Yes, via Ollama on each client Droplet. Serves Llama 3.1 8B (chat) and Mistral 7B (enrichment) via an OpenAI-compatible API at localhost:11434. Circuit breaker pattern: try primary API, fall back to local on failure. Not viable on Vercel (serverless). Implement during or after Droplet migration (Phase 3). Embedding fallback deferred — mismatched vector spaces would require full Pinecone re-indexing. | Apr 2026 |
