# Expense Tracker

A personal, full-stack expense and finance tracker built to replace a manual,
per-month spreadsheet workflow. It connects real bank and credit-card accounts
through **Plaid**, so transactions flow in automatically. One persistent
transaction table backs every view, so any time window — month, quarter, year,
or custom range — is just a query, and year-end stats come for free.

## Features

- **Plaid bank sync (primary)** — connect real bank, credit-card, and brokerage
  accounts via Plaid Link. Transactions sync incrementally (`/transactions/sync`
  with a persisted cursor), categorized from Plaid's `personal_finance_category`,
  enriched with merchant logos, institution branding, account balances, and
  pending-vs-posted status. New links request up to **24 months** of history.
  Investments are supported when the institution offers them, but never block a
  card/bank-only link. Optional in-process auto-sync + a `/sync-all` cron endpoint.
- **Dashboard** — KPI cards (income, expense, net, savings rate), spend-over-time
  area chart, category donut, and income-vs-expense bar chart, driven by a
  date-range and granularity selector, plus a per-account spending breakdown. A
  global multi-account selector scopes every view.
- **Transactions** — filterable, paginated ledger with add / edit / delete via a
  modal form. Filter by date range, type, category, account, or "needs review".
- **Budgets** — monthly per-category spending limits and savings goals, tracked
  against live spend. Can be created conversationally through the AI assistant.
- **AI assistant (Google Gemini)** — on-demand auto-categorization, spending Q&A,
  and natural-language budget/goal creation. Optional; requires a `GEMINI_API_KEY`.
- **Rules engine + needs-review queue** — user-editable keyword/amount rules that
  set type, category, or account; ambiguous rows are flagged for review instead
  of being silently miscounted.
- **Statistics API** — summary, by-category, over-time (year/month/week/day), and
  by-account endpoints. Transfers (including credit-card payments) are excluded
  from spending stats so a payment never double-counts its purchases; refunds net
  against category spend.
- **Authentication (Clerk)** — optional Google sign-in via Clerk with server-side
  JWT verification and an email allowlist. Off by default locally; hard-required
  in production (see below).
- **CSV import (legacy)** — a bank-agnostic drag-and-drop importer remains for
  one-off backfills, but Plaid is now the primary path and CSV is being phased out.

## Tech stack

- **Backend** — FastAPI, SQLAlchemy 2.x, Pydantic 2.x. SQLite by default
  (zero-config); Postgres-ready via `DATABASE_URL` (e.g. Neon). Plaid
  (`plaid-python`) for bank sync, Google Gemini SDK (`google-genai`) for AI,
  PyJWT for Clerk session verification, APScheduler for auto-sync.
- **Frontend** — React 18, Vite 5, TypeScript, React Router, Recharts,
  lucide-react, `@clerk/react`, `react-plaid-link`.
- **Deploy** — single Docker image: FastAPI serves both `/api` and the built SPA
  from one origin. Render Blueprint (`render.yaml`) + Neon Postgres.
- **Tests** — pytest + httpx (isolated in-memory SQLite); Playwright e2e.

## Project layout

```
src/api/            FastAPI backend (main.py, database.py, models.py, schemas.py,
                    rules_engine.py, ai.py, plaid_client.py, plaid_mapping.py, routes/)
src/components/     React views (Dashboard, Transactions, Budget, Rules, PlaidConnect,
                    Assistant, Layout)
src/lib/            API client + shared TypeScript types
src/styles/         Global theme
tests/              pytest suite + fixtures
e2e/                Playwright end-to-end suite
docs/               api-contract.md, DEPLOY.md, build-summary.md, backend notes
requirements.txt    backend (Python) dependencies
package.json        frontend (Node) dependencies
```

## Prerequisites

- **Python 3.10+**
- **Node.js 18+** (with npm)

## Setup

Clone the repo and, from the project root, copy the example env file:

```bash
cp .env.example .env
# then fill in the keys you want (all are optional for a bare local run —
# see Configuration below)
```

`.env` and the SQLite database file are gitignored.

### Backend (port 8000)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn src.api.main:app --reload --port 8000
```

Interactive API docs are available at http://localhost:8000/docs.

### Frontend (port 3000)

In a second terminal, from the project root:

```bash
npm install      # first time only
npm run dev
```

Then open **http://localhost:3000**. The Vite dev server proxies `/api` to the
backend on port 8000, so both run on a single origin during development.

### Production build (frontend)

```bash
npm run build    # type-checks with tsc, then builds to dist/
npm run preview  # serve the production build locally
```

## Configuration

Environment variables (see [`.env.example`](.env.example) for the full list and
notes). Everything is optional for a bare local run; features light up as you
add keys.

- **AI** — `GEMINI_API_KEY` (AI endpoints return `503` when unset),
  `GEMINI_MODEL` (defaults to `gemini-2.5-flash`).
- **Database** — `DATABASE_URL`; unset uses local SQLite, set a `postgresql://…`
  URL for Postgres/Neon.
- **Plaid** — `PLAID_CLIENT_ID`, `PLAID_SECRET`, and `PLAID_ENV`
  (`sandbox` | `production`). With the keys unset, `/api/plaid/*` returns `503`
  and the rest of the app works. `PLAID_PRODUCTS`, `PLAID_COUNTRY_CODES`,
  `PLAID_REDIRECT_URI` (required for OAuth banks), and `PLAID_SYNC_TOKEN` /
  `PLAID_AUTOSYNC_INTERVAL_MINUTES` for scheduled syncing.
  **Note:** `PLAID_ENV` defaults to `sandbox`; a production secret needs
  `PLAID_ENV=production` or Plaid calls fail against the wrong host.
- **Auth (Clerk)** — set `CLERK_ISSUER` to turn auth ON (all `/api` routes then
  require a valid Clerk session JWT). `ALLOWED_EMAILS` restricts access;
  `CLERK_SECRET_KEY` enables server-side email lookup; `VITE_CLERK_PUBLISHABLE_KEY`
  is the frontend key. `REQUIRE_AUTH=true` (auto-set on Render) makes the app
  **refuse to boot** if auth is disabled, so a prod deploy is never wide-open.

## Deployment

Ships as one Docker service (FastAPI serves the API and the built SPA from a
single origin — no CORS in prod) deployed to Render with Neon Postgres. See
[`docs/DEPLOY.md`](docs/DEPLOY.md) for the full walkthrough.

## Running the tests

```bash
source .venv/bin/activate
pytest              # backend unit/integration (in-memory SQLite)
npm run e2e         # Playwright end-to-end
```

## API

All routes are under `/api`. Health check: `GET /api/health`. The full contract
and data model are documented in [`docs/api-contract.md`](docs/api-contract.md),
and a narrative of what was built in [`docs/build-summary.md`](docs/build-summary.md).
