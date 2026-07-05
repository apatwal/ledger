# Expense Tracker — Build Summary

A personal full-stack expense tracker to replace a manual, per-month Google Sheets workflow. Persistent data model, manual + CSV entry, and statistics over arbitrary time ranges (built for year-end reporting and spotting spending patterns).

Built by a 3-person agent team (Backend, Frontend, QA) against a single locked API contract (`docs/api-contract.md`).

---

## What was built

### Backend — FastAPI (`src/api/`)
- **`main.py`** — app, CORS, router includes, `/api/health`, `/api/categories`, startup seed (19 sample transactions if DB empty).
- **`database.py`** — SQLAlchemy engine/session, SQLite, `DATABASE_URL` env override.
- **`models.py`** — single `Transaction` table: `id, date, amount, type, category, description, source, created_at`.
- **`schemas.py`** — Pydantic request/response models incl. stats shapes.
- **`routes/transactions.py`** — full CRUD with filtering (date range, type, category) + pagination.
- **`routes/csv_import.py`** — tolerant CSV import (case-insensitive headers, per-row error reporting) + template download.
- **`routes/stats.py`** — `summary`, `by-category`, `over-time` (month/week/day).

### Frontend — React + Vite + TypeScript (`src/`, `src/components/`, `src/lib/`)
- **Dashboard** — KPI cards (income, expense, net, savings rate), spend-over-time area chart, category donut, income-vs-expense bar (Recharts). Date-range + granularity controls drive all stats; defaults to the current year.
- **Transactions** — filterable/paginated table with add/edit/delete via a modal form; category dropdown from the API.
- **CSV Import** — drag-drop upload with imported/skipped/error feedback and a template download link.
- **Layout** — sidebar nav + live backend health indicator. Dark, sleek theme (`src/styles/globals.css`).
- **`src/lib/api.ts`** — all 12 endpoints centralized; **`src/lib/types.ts`** — contract types.

### Tests — pytest (`tests/`)
- `conftest.py` (isolated in-memory SQLite, fixtures), `test_transactions.py`, `test_csv.py`, `test_stats.py`, plus an end-to-end integration test. See **`tests/report.md`** for pass/fail results.

---

## v2/v3 update — transfers & real bank imports
Added after the initial build, driven by real usage:
- **`transfer` transaction type** (`income | expense | transfer`). Transfers (e.g. paying your credit-card bill, moving money to savings) are recorded but **excluded from every stat** — so a credit-card payment never double-counts the purchases it pays off. The frontend shows transfers in the ledger with an "excluded from spend" tag.
- **Bank-agnostic CSV import.** A module-level `COLUMN_ALIASES` map + normalized (lowercase / strip / de-punctuate) exact-then-contains matching recognizes real-world headers from Discover, Chase, Capital One, Citi, Amex, BofA, Wells Fargo — single signed `Amount` **or** split `Debit`/`Credit` columns, `MM/DD/YYYY` or ISO dates, and bank `Type`/direction columns (`DEBIT`/`CREDIT`/`PAYMENT`). Credit-card sign convention: `+` = purchase (expense), `−` = payment/credit.
- **Auto-transfer detection on import:** rows whose category/description match payment tokens (`Payments and Credits`, `INTERNET PAYMENT`, `THANK YOU`, `AUTOPAY`, …) — and any non-payment negative (refund) — are classified `transfer`. The import response returns a `transfers` count so the UI can say "N payment rows excluded from spending." Tokens live in module-level lists, easy to extend.
- **Fail-fast import errors** name the missing field *and* list the headers actually seen (fixes the opaque "couldn't find date column").
- **`year` granularity** added to `/api/stats/over-time` (period label `YYYY`) for true year-end roll-ups.

Verified against the user's real Discover export (`tests/fixtures/discover_sample.csv`): 15 rows → 13 expenses + 2 transfers, payments excluded from spend totals.

## v4 update — per-card / per-account metrics
Driven by tracking multiple cards (Discover, Chase, BofA):
- Each transaction carries an optional **`account`** (set in the add/edit form, or chosen once per CSV import). Backward-safe startup migration adds the column.
- **`GET /api/stats/by-account`** + an `account` filter on every stat endpoint → see **total spend AND per-card spend**. Dashboard gets a card selector + a click-to-filter "Spending by card" breakdown; Transactions get an account column/filter.
- Verified on real exports: Chase $2,302.07 (133 txns) vs Discover $296.90 (13), combined $2,598.97.
- **Opposite-sign-convention fix:** Discover (`+`=purchase) and Chase (`Sale` rows negative but are purchases; `Payment`/`Return` positive) disagree on sign. A recognized `Type` column now authoritatively wins over sign, so a Chase `Sale -4.79` is a $4.79 expense, never income. `tests/fixtures/chase_sample.csv` is the regression guard.

## v5 update — rules engine, needs-review, AI categorization
The classification layer for messy debit/checking statements (where income, transfers, Venmo, cash, checks, and utilities all mix with cryptic descriptions and no category):
- **User-editable rules engine** (`rules` table + `src/api/rules_engine.py`): keyword/amount → set type/category/account. Rules run **before** the built-in importer inference and override it; first enabled rule by priority wins. CRUD at `/api/rules` + `/api/rules/preview` (count matches) + `/api/rules/apply` (re-apply to existing rows). Rules also grow from corrections — editing a transaction offers "always apply to transactions matching '<merchant>'".
- **Needs-review queue:** ambiguous rows (Venmo, Zelle, ATM, check, cash deposit, or anything left Uncategorized with no rule match) are flagged `needs_review` + `review_reason` on import. Transactions page has a deep-linkable "Needs review" filter + gold badges, so nothing is silently miscounted.
- **AI categorization (reuses the existing Gemini assistant):** rules run first (free, deterministic); `POST /api/assistant/categorize-batch` fills the rest on demand (updates category + clears review at confidence ≥ 0.6; 503 when `GEMINI_API_KEY` unset). The Transactions page surfaces an "Auto-categorize with AI" button.
- New Rules page (`/rules`) + nav. Reference fixture `tests/fixtures/debit_sample.csv` (14-row checking statement). Verified: a ROBINHOOD→transfer rule overrode inference; importing the debit fixture flagged 6 ambiguous rows for review.

## v5.1 update — brokerage savings-vs-transfer prompt
A brokerage deposit from checking is ambiguous (savings vs neutral transfer), so the app detects it (`BROKERAGE_TOKENS`: Robinhood, Fidelity, Vanguard, Schwab, Coinbase, …), defaults it safely to `transfer`, and flags it for review with a distinct "Brokerage: count as savings or keep as transfer?" reason. In the review queue the user picks once → a per-institution rule is created (`→ expense/Investment` to count toward savings rate, or `→ transfer` to stay neutral) and future deposits auto-handle. No stats-math change (Investment expenses already feed savings rate). Verified: the savings choice raised the rate 0→0.14.

## v5.2 update — import history + reassign/undo
Every CSV import now records an **`import_batches`** row (filename, chosen account, counts, timestamp) and tags its transactions with a `batch_id`. The Import page shows a **"Previous imports"** list where each batch can be **reassigned to a card** after the fact (`POST /api/imports/{id}/reassign` cascades the account to all its rows) or **undone** (`DELETE /api/imports/{id}` removes the batch + its rows). Fixes the "forgot to pick the card / imported as Unassigned" case.

_Also fixed: the CSV upload rejected uppercase `.CSV` filenames (case-sensitive extension check) — now case-insensitive, so bank exports like `Chase….CSV` import directly._

## v5.3 update — bank/checking statements
Checking exports differ from cards in ways that broke import: a **summary preamble** (Beginning/Total/Ending balance block) before the real header, and the **opposite sign convention**. Fixes:
- **Preamble auto-skip**: the importer scans for the real header row (first row resolving both a date and an amount/debit/credit column), so BofA-style summary blocks no longer cause a "date column" 400. Also fixed a case-sensitive `.csv` check that rejected uppercase `.CSV` files.
- **`statement_type` = card | bank**: bank mode flips the sign (`−` = spending, `+` = income) so paychecks import as income, not expenses. The import page auto-detects bank vs card (running-balance column ⇒ bank) with a user-overridable selector.
- **Bank-side card-payment detection**: `DISCOVER DES:E-PAYMENT`, `CHASE CREDIT CRD`, `WELLS FARGO CARD`, etc. auto-classify as transfers, so paying a card from checking never counts as spend or double-counts.

## v5.4 update — refunds & pass-throughs (user-chosen modeling)
- **Refunds net against category spend** (new `refund` type): a $30 refund reduces that category's spending by $30 (`total_expense = Σexpense − Σrefund`) across summary/by-category/over-time/by-account. Income/savings unaffected; transfers still fully excluded. CSV `Return`/`Refund`/`Reversal` rows import as refunds.
- **Peer-to-peer pass-throughs default to transfer**: Venmo/Zelle/Cash App rows default to `transfer` + Needs-review ("Assumed pass-through transfer — reclassify if income/expense"), so reimbursement money flowing through checking doesn't inflate income or spending. An explicit Type column or a user rule overrides.

## Key design decisions

1. **One `transactions` table, not per-month sheets.** Every row carries a real `date`, so any time window (month, quarter, year, custom) is just a query. This is the core fix for the old Google Sheets pain — nothing to "reset" each month, and year-end stats come for free.
2. **Savings & investment modeled as expenses** (categories `Savings`/`Investment`). Money leaving spendable cash is an outflow; the summary endpoint isolates these to compute a savings rate without a separate schema.
3. **Contract-first development.** The API contract was locked before any code, so backend, frontend, and tests were built to the same spec in parallel — minimal integration drift (only fix needed: frontend converts the decimal `savings_rate` to a percentage for display).
4. **SQLite + SQLAlchemy.** Zero-config locally, single file, and trivial to point at Postgres later via `DATABASE_URL` for Render/Vercel deployment.
5. **Vite dev proxy.** Frontend on :3000 proxies `/api` → :8000, so the app is a single origin in dev and the same fetch code works in production behind one host.

---

## How to run

Two processes. From the project root (`/Users/adityapatwal/Documents/projects/expense tracker`):

**1. Backend (port 8000):**
```bash
source .venv/bin/activate
uvicorn src.api.main:app --reload --port 8000
```
First run seeds 19 sample transactions into `expense_tracker.db`.

**2. Frontend (port 3000):**
```bash
npm install   # first time only
npm run dev
```

Then open **http://localhost:3000**. The backend must be running for data to load (the sidebar shows a live health indicator).

### CSV format
The importer is **bank-agnostic** — drop in most banks' exports directly (Discover, Chase, Capital One, Citi, Amex, BofA, Wells Fargo); columns are auto-detected and payment rows auto-excluded. The simplest accepted shape uses headers (case-insensitive): `date, amount, type, category, description`. Example:
```csv
date,amount,type,category,description
2026-06-01,1200,income,Freelance,June gig
2026-06-02,80,expense,Groceries,weekly shop
```
Download a template in-app from the Import page, or `GET /api/transactions/csv/template`.

---

## Verification (by orchestrator, end-to-end through the :3000 proxy)
- Frontend serves HTML at :3000; `/api` proxy reaches the backend. ✅
- `GET /api/stats/summary` → income 17250, expense 6865, net 10385, savings 1300, savings_rate 0.0754, count 19. ✅
- `by-category` (pct sums to 100) and `over-time` (monthly periods) correct. ✅
- Manual `POST /api/transactions` → 201; `DELETE` → 204. ✅
- CSV import → 2 imported / 1 malformed row skipped with row-level error. ✅
- Test rows cleaned up; seed restored to 19. ✅

---

## Deploying later (Render / Vercel)
- **Backend:** containerize or run `uvicorn` as a web service; set `DATABASE_URL` to a managed Postgres (swap the SQLite URL — SQLAlchemy models are unchanged).
- **Frontend:** `npm run build` → static `dist/`; deploy to Vercel/Netlify/Render static. Point the API base (or a rewrite/proxy) at the deployed backend URL instead of the dev proxy.
- Tighten CORS to the deployed frontend origin.

## Project layout
```
src/api/            FastAPI backend (Python)
src/components/     React views
src/lib/            API client + types
src/styles/         theme
tests/              pytest suite + report.md
docs/               api-contract.md, backend-notes.md, build-summary.md
requirements.txt    backend deps      package.json  frontend deps
expense_tracker.db  SQLite (seeded)
```
