# Expense Tracker

A personal, full-stack expense and finance tracker built to replace a manual,
per-month spreadsheet workflow. One persistent transaction table backs every
view, so any time window — month, quarter, year, or custom range — is just a
query, and year-end stats come for free.

## Features

- **Dashboard** — KPI cards (income, expense, net, savings rate), spend-over-time
  area chart, category donut, and income-vs-expense bar chart, all driven by a
  date-range and granularity selector. Includes a per-card ("by account") spending
  breakdown.
- **Transactions** — filterable, paginated ledger with add / edit / delete via a
  modal form. Filter by date range, type, category, account, or "needs review".
- **Bank-agnostic CSV import** — drag-and-drop upload that auto-detects columns
  from real exports (Discover, Chase, Capital One, Citi, Amex, BofA, Wells Fargo),
  handles single-signed or split debit/credit amounts, credit-card vs. bank sign
  conventions, and skips summary preambles. Payment/transfer rows are auto-excluded
  from spending so a credit-card payment never double-counts its purchases.
- **Rules engine + needs-review queue** — user-editable keyword/amount rules that
  set type, category, or account on import; ambiguous rows (Venmo, Zelle, ATM,
  checks, brokerage deposits, uncategorized) are flagged for review instead of
  being silently miscounted.
- **AI assistant (Google Gemini)** — on-demand auto-categorization of transactions
  that rules didn't cover. Optional; requires a `GEMINI_API_KEY`.
- **Import history** — every CSV import is recorded as a batch that can be
  reassigned to a card or undone after the fact.
- **Statistics API** — summary, by-category, over-time (year/month/week/day), and
  by-account endpoints. Transfers are excluded from all stats; refunds net against
  category spend.

## Tech stack

- **Backend** — FastAPI, SQLAlchemy 2.x, Pydantic 2.x. SQLite by default
  (zero-config, single file); Postgres-ready via a `DATABASE_URL` env var. Google
  Gemini SDK (`google-genai`) for the AI features.
- **Frontend** — React 18, Vite 5, TypeScript, React Router, Recharts, lucide-react.
- **Tests** — pytest + httpx (isolated in-memory SQLite).

## Project layout

```
src/api/            FastAPI backend (main.py, database.py, models.py, schemas.py,
                    rules_engine.py, ai.py, routes/)
src/components/     React views (Dashboard, Transactions, Import, Rules, Layout)
src/lib/            API client + shared TypeScript types
src/styles/         Global theme
tests/              pytest suite + fixtures
docs/               api-contract.md, build-summary.md, backend notes
requirements.txt    backend (Python) dependencies
package.json        frontend (Node) dependencies
```

## Prerequisites

- **Python 3.10+**
- **Node.js 18+** (with npm)

## Setup

Clone the repo and, from the project root, copy the example env file and add your
Gemini key (only needed for the AI auto-categorization feature — the rest of the
app runs without it):

```bash
cp .env.example .env
# then edit .env and set GEMINI_API_KEY=<your key from https://aistudio.google.com>
```

`.env` and the SQLite database file (`expense_tracker.db`) are gitignored.

### Backend (port 8000)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn src.api.main:app --reload --port 8000
```

The first run seeds ~19 sample transactions into `expense_tracker.db` if the
database is empty. Interactive API docs are available at
http://localhost:8000/docs.

### Frontend (port 3000)

In a second terminal, from the project root:

```bash
npm install      # first time only
npm run dev
```

Then open **http://localhost:3000**. The Vite dev server proxies `/api` to the
backend on port 8000, so both run on a single origin during development. The
backend must be running for data to load (the sidebar shows a live health
indicator).

### Production build (frontend)

```bash
npm run build    # type-checks with tsc, then builds to dist/
npm run preview  # serve the production build locally
```

## Configuration

Environment variables (see `.env.example`):

- `GEMINI_API_KEY` — Google Gemini API key. Required only for AI auto-categorization;
  those endpoints return `503` when it is unset. Get a free key at
  https://aistudio.google.com.
- `GEMINI_MODEL` — optional; defaults to `gemini-2.5-flash`.
- `DATABASE_URL` — optional. Unset uses a local SQLite file; set a
  `postgresql://…` URL to use Postgres (e.g. Neon) instead.

## Running the tests

```bash
source .venv/bin/activate
pytest
```

Tests run against an isolated in-memory SQLite database.

## API

All routes are under `/api`. Health check: `GET /api/health`. The full contract
and data model are documented in [`docs/api-contract.md`](docs/api-contract.md),
and a narrative of what was built in [`docs/build-summary.md`](docs/build-summary.md).
