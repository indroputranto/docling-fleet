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
- Empty state: welcome message + clickable suggested question chips
- Conversation history (in-session, browser memory)
- Copy-to-clipboard on assistant messages
- Auth bar: Sign In / Open CMS / Sign Out (sidebar footer)
- Markdown rendering: tables, code blocks, bullet lists, bold, blockquotes

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

1. **Upload** — drag-and-drop or file-browse; accepts `.docx`, `.pdf`, `.xlsx`; supports multiple files in a single session. An optional Group / Vessel Name label ties related files together in the library.
2. **Extract** — `documents/extractor.py` parses each file into discrete chunks using clause-aware detection (matches `CLAUSE N`, `N.`, `PART II`, `ANNEX A` patterns common in charter parties). Each chunk gets a title and body.
3. **Review & Edit (sequential)** — the user reviews each file one at a time. A "File X of N" banner tracks progress through the batch. They can correct titles, fix parser errors, delete junk chunks, or add chunks manually. Nothing is sent to Pinecone until this step is approved per file.
4. **Save & Continue** — `documents/embedder.py` embeds each chunk via OpenAI `text-embedding-3-small` and upserts to the client's Pinecone namespace. After saving, the system automatically advances to the next file in the queue. The save button reads "Save & Review Next →" until the last file, then "Save to Knowledge Base →".
5. **Library** — shows all documents grouped by their Group / Vessel Name label. Each group shows as a folder-style header. Status per file: Draft / Live / Processing / Error. Delete removes both the DB record and all associated Pinecone vectors.

**DB models:**
- `Document` — one row per uploaded file; tracks filename, type, status, chunk_count, group_name (optional grouping label), uploaded_by, uploaded_at, activated_at
- `DocumentChunk` — one row per embeddable chunk; stores title, body, position, and pinecone_id after embedding

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

> **Note on PDF vessel spec sheets**: Multi-column brochure PDFs (e.g. OCEAN7 spec sheets) use a grid layout that PyMuPDF cannot reconstruct into logical reading order — columns interleave and the output is not useful for semantic chunking. Always upload the `AI_*.docx` version of a vessel description when available. Use the raw PDF only for charter party contracts and single-column documents.

**Chunking strategy (PDFs):**
Header signals — any one of the following triggers a new chunk boundary:
1. **Font size** ≥ body_size × 1.12 — lines visually larger than body text (section titles in vessel specs, chapter headings in contracts).
2. **Bold + short** — all spans bold, line < 120 chars, does not start with a digit (bold body prose is excluded; bold data values starting with digits are excluded).
3. **Clause regex** — legacy pattern for numbered clauses in charter parties (`CLAUSE N`, `N. Title`, `PART II`, `ANNEX A`). Regex requires a space followed by a letter after the numeric separator to prevent decimal numbers (6.438, 16.0 m) and measurements from matching.

**Junk chunk filter (`_is_junk_body`):**
Chunks are discarded before saving if:
- < 5 total words, OR
- > 60 % of lines are ≤ 2 characters (catches slot-plan / hold-diagram position labels A B C … 1 2 3 … extracted from graphical vector drawings).

**Key design decisions:**
- Extraction is synchronous per file (fast enough for charter parties; no async/SSE needed in Phase 2)
- The review step is the differentiator — operators can catch parser misses before they corrupt the knowledge base
- Multi-file is handled as a sequential review queue rather than a parallel batch, keeping the review UI simple and focused
- Grouping is an optional label, not a strict schema — operators can upload ungrouped files, or group by vessel, charter party package, or any other logical unit
- Deletion is clean: vector IDs are deterministic (`{client_id}:doc:{doc_id}:chunk:{pos}`) and stored on each chunk row, enabling targeted Pinecone deletes
- Platform Admins see a client-switcher dropdown; Client Admins are auto-scoped to their own client

### 5.7 Usage Logging & Rate Limiting

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

### 5.9 System Prompt (`prompt.md`)
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
- `models.py` — `ClientConfig`, `User`, `Document`, `DocumentChunk` SQLAlchemy models
- `cms/routes.py` — full CRUD for clients and users; 3-tier role scoping
- CMS templates: dashboard, client form (with Chat UX section), user form, login
- `client_config.py` updated to query DB first, fall back to hardcoded registry
- `documents/` Blueprint — full document upload pipeline (see Section 5.6)
- `migrate_db.py` — idempotent schema migration script for adding columns to existing DBs
- `UsageLog` model — per-request logging (client, user, tokens, latency)
- Rate limiting — per-client daily request cap with 429 enforcement (see Section 5.7)
- Analytics dashboard — `/cms/analytics` with Chart.js charts and top-users table (see Section 5.8)
- User edit / password reset — `/cms/users/<id>/edit` for admin-side credential management
- Role-scoped sidebar — nav items hidden (not just disabled) based on user role

**Remaining CMS items:**
- Embed code generator (iframe/script snippet for client's own website)
- Preview button (open a sandboxed chat window from within the CMS)
- Duplicate client (clone config as starting point for new client)
- Subdomain display with copy-to-clipboard
- "Powered by" control (show/hide platform attribution in chat UI)

### Phase 3 — Production Hardening & Automation
Gunicorn + systemd deployment, nginx routing, automated provisioning.

**Deliverables planned:**
- Gunicorn WSGI server config with systemd unit file
- nginx reverse proxy config with subdomain routing and SSL (Let's Encrypt)
- Switch from SQLite to PostgreSQL (connection string change in `.env`)
- Automated provisioning script (Phase 3B — see Section 8)
- Ops monitoring dashboard

---

## 7. Infrastructure — Current State (Development)

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
```

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

### 8.3 Network Diagram (3 Active Clients)

```
                        Internet
                            │
                    ┌───────▼────────┐
                    │   DNS / nginx  │
                    │  (wildcard     │
                    │ *.platform.com)│
                    └──┬──────┬──┬───┘
                       │      │  │
          ┌────────────▼─┐  ┌─▼──────────┐  ┌─▼──────────────┐
          │  Droplet A   │  │  Droplet B  │  │   Droplet C    │
          │  acme.p.com  │  │  bcl.p.com  │  │  delta.p.com   │
          │  Flask app   │  │  Flask app  │  │  Flask app     │
          │  PostgreSQL  │  │  PostgreSQL │  │  PostgreSQL    │
          └──────┬───────┘  └──────┬──────┘  └───────┬────────┘
                 │                 │                   │
          ┌──────▼──────┐   ┌──────▼──────┐   ┌───────▼───────┐
          │ Pinecone    │   │ Pinecone    │   │ Pinecone      │
          │ Index: acme │   │ Index: bcl  │   │ Index: delta  │
          └─────────────┘   └─────────────┘   └───────────────┘

                    ┌──────────────────────┐
                    │   Ops Droplet        │
                    │   Monitoring dash    │
                    │   Provisioning tools │
                    │   Client registry    │
                    └──────────────────────┘
```

### 8.4 Phase 3B — Automated Provisioning

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
