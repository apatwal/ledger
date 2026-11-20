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
  Syncs are **isolated per bank**: one unhealthy institution can't fail the run or
  stop the others, it's flagged in the wallet with the reason, and **Reconnect**
  reopens Plaid Link in update mode to repair it (re-auth + account re-selection).
  Syncs are also **serialized and exactly-once**: the cron, scheduler, webhooks,
  and manual syncs can't overlap, and a unique index guarantees one row per Plaid
  transaction, so a bank charge can never be imported (or counted) twice.
  $0 brokerage events (in-kind share transfers, stock splits) aren't money
  movement and are never imported.
  **One connection per bank** — to add an account at a bank you've already linked,
  use **+ Add accounts** on its card, which reopens Plaid's account picker against
  the same connection. Linking the bank a second time is refused, since that would
  duplicate the accounts you already have (and double-count them in net worth).
- **Dashboard** — KPI cards (income, expense, net, savings rate), spend-over-time
  area chart, category donut, and income-vs-expense bar chart, driven by a
  date-range and granularity selector, plus a per-account spending breakdown. A
  global multi-account selector scopes every view.
- **Transactions** — filterable, paginated ledger with add / edit / delete via a
  modal form. Filter by date range, type, category, account, or "needs review".
- **Budgets** — monthly per-category spending limits and savings goals, tracked
  against live spend. Can be created conversationally through the AI assistant.
- **AI assistant (OpenAI)** — on-demand auto-categorization, spending Q&A,
  spending insights, and natural-language budget/goal creation. Optional;
  requires an `OPENAI_API_KEY` (model defaults to `gpt-4o-mini`).
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
- **Readable errors** — when something fails, the page says what in plain English
  ("Couldn't load transactions") and keeps the technical record behind a
  **Details** toggle: the request, status, the real cause (e.g.
  `ResponseValidationError: row 0 (id 1662) amount: …`) and an **error ID**, with a
  Copy button. Search Render's logs for the error ID to find the full stack trace,
  which is never sent to the browser. Nothing is stored; it's on screen only. See
  *Errors* in [`docs/api-contract.md`](docs/api-contract.md).
- **CSV import (decommissioned)** — a bank-agnostic drag-and-drop importer that
  predates the Plaid integration. Now that the app is Plaid-first, its UI is
  **hidden behind the `CSV_IMPORT_ENABLED` flag** (off by default — see
  [`src/lib/features.ts`](src/lib/features.ts)). The code (frontend component,
  API client, and `csv_import`/`imports` backend routes) is kept intact; flip the
  flag and rebuild to restore the Import page for a one-off backfill.

## Tech stack

- **Backend** — FastAPI, SQLAlchemy 2.x, Pydantic 2.x. SQLite by default
  (zero-config); Postgres-ready via `DATABASE_URL` (e.g. Neon). Plaid
  (`plaid-python`) for bank sync, the OpenAI Python SDK (`openai`) for AI,
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

To turn on the AI assistant (auto-categorize, Q&A, insights, budget creation),
set `OPENAI_API_KEY` in `.env` to a key from
<https://platform.openai.com/api-keys>, and optionally `OPENAI_MODEL` (defaults to
`gpt-4o-mini`). Without a key the app runs normally and the assistant stays hidden.

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

- **AI** — `OPENAI_API_KEY` (create one at
  <https://platform.openai.com/api-keys>; AI endpoints return `503` when unset),
  `OPENAI_MODEL` (defaults to `gpt-4o-mini`), `APP_TIMEZONE` (the IANA zone the
  AI treats as "today", defaults to `America/New_York`; the server itself runs
  in UTC). OpenAI failures (bad key, rate
  limit or exhausted quota, timeout) come back as a readable `502`/`503` that the
  Assistant shows in its error notice.
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
pytest              # backend unit/integration (in-memory SQLite; OpenAI is mocked)
npm run e2e         # Playwright end-to-end
```

## API

All routes are under `/api`. Health check: `GET /api/health`. The full contract
and data model are documented in [`docs/api-contract.md`](docs/api-contract.md),
and a narrative of what was built in [`docs/build-summary.md`](docs/build-summary.md).
