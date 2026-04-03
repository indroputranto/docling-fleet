# Grand Plan — Functional Specification Document
**Project:** Maritime AI Chatbot SaaS Platform
**Codename:** docling
**Last Updated:** April 2026
**Status:** Phase 1 complete · Phase 2 in progress

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
- Roles: `admin` (CMS access), `user` (chat login only)

### 5.4 Admin CMS (`/cms`)
Internal dashboard for managing client deployments. Not client-facing.

- **Dashboard:** client list, user list, quick stats
- **Client form:** Identity, Branding, Chat UX, AI & Knowledge Base, System Prompt
  - Identity: client ID (slug), display name, company name, chatbot name, logo URL
  - Branding: primary/secondary/accent/text colors, dark mode color pickers
  - Chat UX: welcome message, suggested questions, default theme, show/hide toggle
  - AI config: Pinecone index, namespace, LLM model, embedding model, context chunks, history turns
  - System prompt: full editable prompt with `[PLACEHOLDER]` token system for whitelabeling
- **User management:** create users, assign to clients, activate/deactivate
- Auth: httpOnly cookie (`cms_token`), admin role required

### 5.5 System Prompt (`prompt.md`)
The instruction set defining how Claude behaves for a given client. Whitelabeled via placeholder tokens:

- `[CLIENT_NAME]` — the client's company/fleet name
- `[ZERONORTH_ONBOARDING_EMAIL]` — or equivalent contact
- `[CLIENT_VESSEL_ALIASES]` — vessel name variants to recognize
- `[SISTER_VESSEL_MAPPINGS]` — cross-vessel clause inheritance rules
- `[CLIENT_SPECIFIC_SEARCH_RULES]` — custom logic for this fleet's document structure

---

## 6. Development Phases

### Phase 1 — Chat API ✅ Complete
Single-tenant Flask app with hardcoded client config, full RAG pipeline, chat UI, JWT auth.

**Deliverables completed:**
- `app.py`, `chat_routes.py`, `auth.py`, `models.py`, `client_config.py`
- `templates/chat.html` — fully polished chat UI
- Subdomain-based client routing with IP detection fix
- Dark/light mode theming system

### Phase 2 — Admin CMS 🔄 In Progress
Database-driven client management. Same Flask app, new `/cms` blueprint.

**Deliverables completed:**
- `models.py` — `ClientConfig` and `User` SQLAlchemy models
- `cms/routes.py` — full CRUD for clients and users
- CMS templates: dashboard, client form (with Chat UX section), user form, login
- `client_config.py` updated to query DB first, fall back to hardcoded registry

**Remaining CMS items:**
- Usage counters (API calls per client, per day/month)
- Rate limiting (per-client configurable request caps)
- Embed code generator (iframe/script snippet for client's own website)
- Preview button (open a sandboxed chat window from within the CMS)
- Duplicate client (clone config as starting point for new client)
- Subdomain display with copy-to-clipboard
- "Powered by" control (show/hide platform attribution in chat UI)

### Phase 3 — Production Hardening & Automation 📋 Planned
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
                    │  *.platform.com│
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
| Font: Noto Serif vs. Inter? | Inter. Better readability for dense charter party text. | Apr 2026 |
