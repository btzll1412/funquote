# FunQuote — Universal AI-Assisted Quoting System

A self-hosted, multi-tenant quoting system usable by any company. Each
organization (tenant) gets its own catalog, branding, users, customers, and
quotes — fully isolated from every other tenant. Fully usable as a manual
quoting tool; AI assist is an optional accelerator layered on top.

## Quick start (local)

```bash
pip install -r requirements.txt
cp .env.example .env          # set SECRET_KEY at minimum
uvicorn app.main:app --reload
```

Open http://localhost:8000, sign up to create your organization, and go.
With the default `AI_PROVIDER=mock` everything works offline — the AI-assist
screens use a deterministic parser. Set `AI_PROVIDER=openai` and
`OPENAI_API_KEY` to use real extraction.

## Quick start (Docker)

```bash
cp .env.example .env          # set SECRET_KEY
docker compose up --build
```

Data (SQLite DB + uploaded logos) lives in the `funquote-data` volume. For
PostgreSQL, set `DATABASE_URL` (see `docker-compose.yml`).

## What's included (Phase 1 MVP)

- **Multi-tenancy** — organization signup/login, admin/staff roles, per-org
  business profile and branding. Tenant isolation is enforced at the
  data-access layer (`app/repository.py`): every query through `OrgRepo` is
  filtered by `organization_id`, every insert is stamped with it, so a missed
  filter in a route handler cannot leak another tenant's data.
- **Catalog** — CRUD with cost/sell/markup (enter either; the other is
  computed), categories, and JSON **custom fields** so any industry can track
  its own attributes (resolution, PoE class, pipe diameter, …) without schema
  changes. Deletes are soft so quote snapshots stay intact.
- **Customers** — CRUD, searchable.
- **Manual quote builder** — pick a customer (or ad hoc), add lines from the
  catalog or custom lines, tax, expiration, notes; totals computed
  server-side; branded **PDF** download (logo, company info, footer terms).
  Line items snapshot SKU/description/price so old quotes stay accurate when
  the catalog changes. Every save records a full snapshot in
  `quote_versions` (ready for Phase 2 revision history).
- **AI-assist quote drafting** — paste customer text → `extract_quote_items`
  → editable draft in the same builder. Every SKU the AI returns is validated
  against the real catalog; unmatched items are highlighted for manual
  resolution; **pricing always comes from the database, never the AI**.
  Nothing is saved until the user reviews and clicks Save.
- **Bulk catalog import** — paste a price list + free-text instructions →
  `import_catalog_items` → review screen with duplicate detection (SKU match
  + fuzzy name match) → nothing written until confirmed.
- **AI task log** — every AI invocation (input, raw output, model, status,
  linked entity) recorded per organization in `ai_task_log`, with an
  admin-only viewer under Settings.
- **AI on/off toggle** — per-organization, under Settings. With AI off the
  system is a fully manual quoting tool.

## Architecture

- **Backend:** Python / FastAPI / SQLAlchemy 2.0
- **DB:** SQLite by default; PostgreSQL via `DATABASE_URL`
- **Frontend:** server-rendered Jinja2 templates + a small amount of vanilla
  JS (the quote builder). *This was the spec's open decision — templates were
  chosen for zero build tooling and easy self-hosting; the routes are plain
  HTTP endpoints, so a React front-end can be layered on later without
  backend changes.*
- **PDF:** ReportLab (pure Python)
- **AI:** all calls go through `app/ai/tasks.py` (`ai_task(task_type,
  payload, repo=...)`) — one narrow, stateless task per type with a fixed
  JSON schema, schema-validated output, and mandatory logging. The provider
  itself is behind `app/ai/provider.py` (`AIProvider` interface): OpenAI
  Chat Completions with Structured Outputs today; swapping to another vendor
  is a contained change. The AI never sees prices, never touches the
  database, and is only ever invoked explicitly by application code.

### AI task types

| Task | Purpose | Status |
|---|---|---|
| `extract_quote_items` | customer text → draft line items matched to catalog | Phase 1 |
| `import_catalog_items` | pasted price list → structured items for review | Phase 1 |
| `classify_quote_request` | is an email an RFQ? | defined; wired up in Phase 2 (email intake) |

## Tests

```bash
python -m pytest tests/ -q
```

Covers tenant isolation (cross-org 404s, repo-layer scoping), the manual
quote flow (totals, snapshots, per-org quote numbering, PDF, statuses),
and the AI flows (catalog validation, no pricing sent to the AI, logging,
review-before-write on import, org AI toggle, admin-only log viewer).

## Project layout

```
app/
  main.py           app wiring, middleware, static mounts
  config.py         env-based configuration
  database.py       engine/session setup
  models.py         all tables (every tenant table has organization_id)
  repository.py     OrgRepo — the tenant-scoping enforcement point
  auth.py           password hashing, session auth, role checks
  pdf.py            branded quote PDF (ReportLab)
  ai/
    provider.py     AIProvider interface, OpenAI + offline mock
    tasks.py        ai_task() — task registry, schemas, validation, logging
  routes/           one module per feature area
  templates/        Jinja2 pages
  static/           stylesheet
tests/              pytest suite
```
