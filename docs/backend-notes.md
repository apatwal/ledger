# Backend Notes

## How to Run

```bash
# From project root
source .venv/bin/activate
uvicorn src.api.main:app --reload --port 8000
```

Or without activating the venv:

```bash
PYTHONPATH="/path/to/expense tracker" \
  .venv/bin/uvicorn src.api.main:app --reload --port 8000
```

The server listens on **http://localhost:8000**. All routes are under `/api`.

## Seeding (opt-in — DB starts EMPTY by default)

The app **starts with an empty database** for real user data. The startup
auto-seed is **opt-in**: sample transactions are only inserted when the
`SEED_DB` env var is truthy (`1`, `true`, `True`, or `yes`) AND the table is
empty. With `SEED_DB` unset, an empty DB stays empty across restarts — real
data is never overwritten or re-created.

To load the demo dataset (19 sample transactions across Jan–Mar 2026: salary,
rent, groceries, savings, investment, dining, transport, healthcare,
entertainment, freelance) into an empty DB:

```bash
SEED_DB=1 uvicorn src.api.main:app --reload --port 8000
```

The `seed_data()` function remains in `main.py` for dev/demo use; it is just no
longer called by default.

DB file: `expense_tracker.db` at project root. Override via `DATABASE_URL`
env var (standard SQLAlchemy connection string).

## Endpoint Summary

| Method | Path | Description |
|--------|------|-------------|
| GET | /api/health | `{"status":"ok"}` |
| GET | /api/categories | Distinct categories + defaults |
| GET | /api/accounts | Distinct non-empty accounts/cards (v4) |
| POST | /api/transactions | Create transaction (201); accepts optional `account` |
| GET | /api/transactions | List with filters (start_date, end_date, type, category, **account**, **needs_review**, limit, offset) |
| GET | /api/transactions/{id} | Single transaction |
| PUT | /api/transactions/{id} | Replace transaction; accepts optional `account`, `needs_review`, `review_reason` |
| DELETE | /api/transactions/{id} | Delete (204) |
| POST | /api/transactions/csv | Import CSV (multipart `file`, optional form fields `account`, `statement_type`=card\|bank) → `{imported, skipped, transfers, needs_review, batch_id, errors}` |
| GET | /api/transactions/csv/template | Download blank CSV template |
| GET | /api/stats/summary | Totals + savings rate; optional `account` filter |
| GET | /api/stats/by-category | Category breakdown with %; optional `account` filter |
| GET | /api/stats/over-time | Time-series (granularity=year\|month\|week\|day); optional `account` filter |
| GET | /api/stats/by-account | Per-card breakdown `[{account, income, expense, net, count}]` (v4) |
| POST | /api/rules | Create rule (201) (v5) |
| GET | /api/rules | List rules, priority asc; optional `enabled` filter (v5) |
| GET | /api/rules/{id} | Single rule \| 404 (v5) |
| PUT | /api/rules/{id} | Partial update \| 404 (v5) |
| DELETE | /api/rules/{id} | Delete (204) \| 404 (v5) |
| POST | /api/rules/apply | Re-apply enabled rules to existing txns → `{updated}` (v5) |
| POST | /api/rules/preview | Count existing txns a candidate rule would hit → `{matches}` (v5) |
| POST | /api/assistant/categorize-batch | AI-categorize a target set; clears needs_review when confident (v5; 503 if no key) |
| GET | /api/imports | Import batches, newest first (v5.2) |
| POST | /api/imports/{id}/reassign | Set account on batch + its txns → `{updated}` \| 404 (v5.2) |
| DELETE | /api/imports/{id} | Delete batch + its txns (undo import) → 204 \| 404 (v5.2) |

## Deviations from Contract

**None material.** Every endpoint, field name, response shape, status code, and
query parameter matches `docs/api-contract.md` exactly. Two pragmatic choices
the contract leaves open:

- A CSV row with no resolvable category gets `category = "Uncategorized"` instead
  of being skipped (real bank exports often lack a category and the user can edit
  it in the UI).
- `transfers` in the CSV response counts every imported row that ended up
  classified as `transfer` (per the contract's response note: "how many imported
  rows were classified as transfer"), regardless of which path classified it
  (explicit type column, payment/category token, or negative-sign refund).
- (v5) `needs_review` follows the contract's `(no rule) AND (Uncategorized OR
  ambiguous token)` rule. When both conditions hold, `review_reason` prefers the
  ambiguous-token reason over "Uncategorized" (it's the more actionable signal);
  the boolean flag is unchanged. `categorize-batch` applies a suggestion (and
  clears the flag) at confidence ≥ 0.6.

## Transaction Types (v2/v3, +refund v5.4)

Type enum is `income | expense | transfer | refund`.

`transfer` = money moving between the user's own accounts (NOT spending or
income). The motivating case: a credit-card statement CSV contains both the
purchases (expenses) AND the payment that pays them off ("INTERNET PAYMENT -
THANK YOU"). Counting that payment as an expense double-counts.

**Transfers are EXCLUDED from ALL stats** — `/api/stats/summary`
(total_income, total_expense, net, savings, savings_rate, and the row `count`),
`/api/stats/by-category`, and `/api/stats/over-time`. They are still stored and
returned by the CRUD/list endpoints (visible in the ledger), just never in
spending math. Implemented via a single `Transaction.type != "transfer"` clause
in the stats `_date_filter` helper, so it applies uniformly.

## CSV Import (bank-agnostic, v3)

Handles real bank exports (Discover, Chase, BofA, Capital One, Citi, Amex,
Wells Fargo), not just our own template.

- **Header mapping** via module-level `COLUMN_ALIASES` dict (in
  `routes/csv_import.py`). Headers are normalized (lowercase, strip
  whitespace/quotes, remove `.` and `_`, collapse inner whitespace), then
  matched by EXACT normalized equality first, with a `contains` fallback. First
  alias in list order wins; trans-date aliases are ordered before post-date so a
  transaction date is preferred. `IGNORE_HEADERS` (balance, card no, status,
  etc.) are never used as amount/date.
- **Amount**: a single signed `amount` column, OR separate `debit`/`credit`
  columns (debit = money out → expense; credit = money in → income, unless a
  transfer token matches). `$`, commas, and parenthesised negatives are handled.
- **Type/direction column (authoritative — wins over sign)**: bank values are
  mapped — `debit/dr/withdrawal/sale/purchase → expense`; `credit/cr/deposit →
  income`; `payment/xfer/transfer → transfer`; `return/refund/reversal →
  refund` (v5.4 — was transfer). Our literal `income/expense/transfer/refund`
  pass through. A recognized direction value WINS over sign inference;
  unrecognized values fall back to sign/token inference (no error).
  - **Banks use OPPOSITE sign conventions, so the labelled `Type` is
    authoritative.** Chase exports negative amounts for `Sale` rows (which are
    EXPENSES) and positive amounts for `Payment`/`Return` rows (which are
    TRANSFERS). Because the `Type` value wins, a Chase `Sale -4.79` becomes a
    $4.79 EXPENSE (never income), and a `Return 24.96` / `Payment 146.84`
    becomes a transfer. Discover (no `Type` column) keeps the single-signed
    convention below.
- **Date parsing**: accepts `MM/DD/YYYY` (Discover) and ISO `YYYY-MM-DD`; stored
  as ISO.
- **Credit-card sign convention** (no type column, single signed amount):
  positive = purchase (expense), negative = payment/credit. Amount stored as
  absolute magnitude; sign only sets the type.
- **Transfer auto-classification** (runs BEFORE sign inference; explicit type
  column wins): two module-level token lists — `TRANSFER_CATEGORIES`
  (`payments and credits`, `payments & credits`, `payment`, `transfer`,
  `credit card payment`) and `TRANSFER_DESCRIPTION_TOKENS` (`internet payment`,
  `thank you`, `autopay`, `payment - thank you`, `payment thank you`). Any
  leftover NEGATIVE non-payment row (a refund) is also classified `transfer` so
  it never inflates income.
- **Optional `account` form field (v4)**: pass `account=<card>` alongside
  `file` in the multipart body to tag EVERY imported row with that card/account
  (a CSV doesn't identify its own card). Omit it and rows get a null account.
- **Response**: `{ "imported": int, "skipped": int, "transfers": int,
  "errors": [{"row": int, "reason": str}] }`. `transfers` = how many imported
  rows ended up classified as `transfer` (any path), so the UI can tell the user
  "N payment/transfer rows were excluded from spending."
- **Failure**: if no date column or no amount/debit/credit column resolves, the
  whole file is rejected with a 400 that names the missing field and lists the
  headers actually seen.
- Missing category defaults to `Uncategorized` (bank exports often omit it)
  rather than rejecting the row.

**Verified against `tests/fixtures/discover_sample.csv`** (15 data rows,
no `Type` column): `imported: 15, transfers: 2, skipped: 0` → 13 expenses + 2
transfers. The two `INTERNET PAYMENT - THANK YOU` rows are stored as `transfer`.

**Verified against `tests/fixtures/chase_sample.csv`** (157 data rows, HAS a
`Type` column, opposite sign convention): `imported: 157, transfers: 24,
skipped: 0` → 133 expenses (all `Sale` rows, stored as positive magnitude), 24
transfers (13 `Payment` + 11 `Return`), and **0 income** (nothing misclassified).
Neither fixture's transfer rows appear in `/api/stats/by-category` or affect
`/api/stats/summary` totals.

## Accounts / Per-Card Metrics (v4)

- `transactions.account` is a nullable string (which card/account, e.g.
  `Discover`, `Chase`, `BofA`). Set in the add/edit form for manual rows; passed
  as the `account` form field on CSV import (applied to every row in the file).
- **Migration**: on startup, `_migrate_add_account_column()` introspects via
  `PRAGMA table_info(transactions)` and runs `ALTER TABLE transactions ADD
  COLUMN account VARCHAR` if the column is missing — so an existing
  (pre-v4) `expense_tracker.db` gains the column WITHOUT data loss. No-op on a
  fresh DB (create_all builds the full schema) or when the column already exists.
- **`account` filter** is accepted on `GET /api/transactions`,
  `/api/stats/summary`, `/api/stats/by-category`, and `/api/stats/over-time`:
  present → restrict to that card; absent → aggregate across ALL cards.
- **`GET /api/stats/by-account`** → `[{account, income, expense, net, count}]`
  descending by expense, excluding transfers. Null/empty account is reported as
  `"Unassigned"`.
- **`GET /api/accounts`** → distinct non-empty accounts (for dropdowns/filters).
- Note: the stats route functions are also called directly (not over HTTP) by
  the AI assistant, so the optional `account` params default to a plain `None`
  (not `Query(None)`) to keep those direct calls working.

## Rules Engine + Review + AI Batch (v5)

A user-controlled classification layer for messy debit/checking imports.

- **`rules` table** (`models.py` `Rule`): `id, name?, priority (default 100,
  lower runs first), enabled (default true), match_field
  (description|category|account|any), match_op (contains|equals|regex),
  match_value, amount_min?, amount_max?, set_type?, set_category?, set_account?,
  created_at`. **First enabled rule (priority asc, then id asc) wins.**
- **`transactions` gains** `needs_review` (bool default false) + `review_reason`
  (str nullable), added via `_migrate_add_needs_review_column()` (PRAGMA →
  ALTER TABLE, same pattern as v4 `account`; no data loss).
- **Rules engine** (`rules_engine.py`) is pure (no DB/HTTP/network):
  `apply_rules(rules, *, description, category, account, amount) -> RuleHit|None`
  returns the first enabled matching rule's non-null `set_*` actions.
  `rule_matches()` powers preview. `AMBIGUOUS_TOKENS` (venmo, zelle, cash app,
  atm, withdrawal, "check ", e-check, cash deposit, wire transfer, p2p, …) +
  `is_ambiguous()` power the review heuristic.
- **CSV import classification order** (per row): parse fields → **apply user
  rules** (override type/category/account) → if no rule, built-in inference
  (`_infer_type`, the v3/v4 logic) → flag `needs_review` when (no rule matched)
  AND (description matches an ambiguous token OR category=="Uncategorized"). The
  response gains a `needs_review` count.
- **Rules routes** (`routes/rules.py`, prefix `/rules`): full CRUD;
  `POST /rules/apply` `{account?, only_review?}` re-applies enabled rules to
  existing rows (updates type/category/account on a rule hit and clears the
  review flag) → `{updated}`; `POST /rules/preview` (RuleCreate body) →
  `{matches}` count of existing txns a candidate rule would hit.
- **AI batch** (`routes/assistant.py` `POST /assistant/categorize-batch`):
  body `{ids?, only_uncategorized?, account?, start_date?, end_date?, limit?}`;
  for each target txn calls `ai.suggest_category`, and when confidence ≥ 0.6
  updates `category` + clears `needs_review`; returns
  `{results: [{id, category, confidence}]}`. Gated by `GEMINI_API_KEY` (503
  when unset). Queries are built directly (no FastAPI `Query` sentinels passed
  into internal calls).

Verified (temp DB): a rule `description contains ROBINHOOD → set_type=transfer`
overrides inference (a positive-amount ROBINHOOD row that would infer `expense`
is stored `transfer`); a higher-priority rule wins first-match; `/rules/preview`
and `/rules/apply` count/update correctly; discover (15/2) and chase
(133 expense/24 transfer/0 income) are UNCHANGED when no rules exist; a
synthetic checking CSV flags venmo/zelle/atm/check rows `needs_review=true`;
`categorize-batch` clears review flags on confident suggestions and returns 503
without a key.

### Brokerage savings-vs-transfer prompt (v5.1)

A deposit from checking into a brokerage/investment platform is ambiguous: it
could be "savings" (should count toward savings rate) or a neutral "transfer".
`rules_engine.BROKERAGE_TOKENS` (robinhood, fidelity, vanguard, schwab, charles
schwab, e*trade, etrade, coinbase, wealthfront, betterment, merrill, sofi invest,
acorns, td ameritrade, webull) + `is_brokerage(description)` detect these.

On CSV import, ONLY when no user rule matched, a brokerage row is:
- defaulted to `type="transfer"` (safe — never inflates spend, overrides the
  built-in inference), and
- flagged `needs_review=True` with a DISTINCT
  `review_reason = "Brokerage: count as savings or keep as transfer? (<token>)"`.

This brokerage reason takes precedence over the generic ambiguous-token reason.
Brokerage rows count in the response `needs_review` total. A matching user rule
still overrides entirely, so once the user decides, future deposits are
auto-handled and NOT re-flagged.

No stats-math change: the frontend resolves the prompt by creating a rule via
the existing `POST /api/rules` — "savings" → `set_type=expense,
set_category=Investment` (Investment-category expenses already feed
`savings`/`savings_rate`); "transfer" → `set_type=transfer` — then
`POST /api/rules/apply`.

Verified (temp DB): importing `debit_sample.csv` with no rules flags the
ROBINHOOD row `type=transfer`, `needs_review=true`, reason starting "Brokerage:".
Creating the savings rule + apply → row becomes `expense`/`Investment`,
`needs_review` clears, and `/api/stats/summary` `savings` rises to 500 with
`savings_rate` 0.1399. The transfer-choice rule instead keeps it `transfer`
(savings stays 0). With a rule present, re-import does NOT re-flag the row.

### v5 note on needs_review

Per the contract the flag fires on `(no rule) AND (brokerage OR ambiguous token
OR Uncategorized)`. Reason precedence: Brokerage > ambiguous token >
Uncategorized. A confidently-inferred `transfer` whose
source row had an empty category column (e.g. Chase `Payment` rows) is still
flagged "Uncategorized" per the literal spec — the user/AI can resolve it or a
rule can pre-empt it.

## Import History + Reassign (v5.2)

Lets the user see past imports and reassign one to a card after the fact (or
undo it).

- **`import_batches` table** (`models.py` `ImportBatch`): `id, filename,
  account?, imported, skipped, transfers, needs_review, created_at`.
- **`transactions.batch_id`** (int, nullable) links each imported row to its
  batch; manual rows have null `batch_id`. Added via
  `_migrate_add_batch_id_column()` (PRAGMA → `ALTER TABLE ... ADD COLUMN
  batch_id INTEGER`; same pattern as account/needs_review; no data loss).
- **CSV import** creates ONE `ImportBatch` (filename from the UploadFile, the
  chosen `account`, final imported/skipped/transfers/needs_review counts), tags
  every created transaction with `batch_id`, and returns `batch_id` in the
  response. (Rows are collected during the loop, the batch is flushed to get its
  id, then rows are tagged before commit.)
- **Endpoints** (`routes/imports.py`, prefix `/imports`):
  - `GET /api/imports` → `ImportBatchOut[]` newest first.
  - `POST /api/imports/{id}/reassign` body `{account: str|null}` → sets account
    on the batch AND all its transactions (bulk UPDATE by batch_id) →
    `{updated: int}` | 404. Empty/null → Unassigned (null).
  - `DELETE /api/imports/{id}` → deletes the batch and all its transactions
    (undo an import) → 204 | 404.
- `batch_id` is on `CSVImportResponse` but intentionally NOT added to
  `TransactionOut` (the contract's Schemas section doesn't list it there;
  staying strictly additive).

Verified (temp DB): import discover (account=Discover) then chase (no account)
→ `GET /api/imports` lists 2 batches newest-first with correct counts;
`POST /api/imports/{chase}/reassign {account:"Chase"}` → `updated:157` and
`/api/stats/by-account` then shows Chase (2302.07, count 133); reassign to null
→ Unassigned; `DELETE /api/imports/{discover}` → 204 and the 15 rows are gone
(172→157). 404 on unknown batch id for both reassign and delete.

## Bank/Checking Statements (v5.3)

Handles real checking/savings exports (e.g. BofA), which differ from credit
cards in three ways:

- **Preamble skipping**: `_find_header_and_body()` scans the leading CSV rows
  and picks the REAL header = the FIRST row where BOTH a date alias AND an
  amount/debit/credit alias resolve, skipping any summary block (BofA's
  Beginning balance / Total credits / Total debits / Ending balance) and blank
  lines before `Date,Description,Amount,Running Bal.`. Fixes the 400
  `Could not resolve a required 'date' column. Headers seen: ['Description','','Summary Amt.']`.
  Error messages are preserved: a date column but no amount → the specific
  "amount column" 400 (naming the date-only header seen); no date column at all
  → the "date column" 400 (naming the first non-empty row).
- **`statement_type` form field = `card` (default) | `bank`**, threaded into
  `_infer_type`:
  - `card` single signed amount: `+`→expense, `−`→transfer (payment/credit).
    (unchanged v3 behavior)
  - `bank` single signed amount: **`−`→expense (outflow), `+`→income (inflow)**
    — fixes paychecks importing as expenses.
  - Precedence unchanged: a recognized `Type`/direction column value and separate
    `debit`/`credit` columns still win over sign; transfer tokens, brokerage
    detection, and user rules override in both modes.
  - `ImportBatch.statement_type` records the mode (migrated via
    `_migrate_add_statement_type_column`, PRAGMA→ALTER on `import_batches`).
- **Bank-side credit-card payments** (`rules_engine.CARD_PAYMENT_TOKENS` +
  `is_card_payment()`): `des:e-payment`, `des:epay`, `des:ccpymt`, `credit crd`,
  `wells fargo card`, `bilt card des:pmt`, `card des:pmt`,
  `online banking payment to crd`, `e-payment` → classified `transfer` (so paying
  a card from checking isn't counted as spend / doesn't double-count the card's
  purchases). Checked in `_infer_type` before the debit/credit + sign fallbacks.
  Genuinely ambiguous rows (Venmo, Zelle, Discover CONA net/mobile, cash) are NOT
  force-classified — they stay needs-review.

Verified (temp DB): a synthetic BofA CSV (summary preamble + blank line + real
header) imported with `statement_type=bank` → preamble skipped (no 400);
`MATHWORKS ... PAYROLL` +4403.87 → income (not expense); `EVERSOURCE ... WEB_PAY`
−168.29 → expense; `DISCOVER DES:E-PAYMENT` / `CHASE CREDIT CRD DES:EPAY` →
transfer (card-payment tokens); `SCHWAB BROKERAGE` → transfer + "Brokerage:"
needs-review; Venmo/Zelle → needs-review. `discover_sample.csv`/`chase_sample.csv`
with NO `statement_type` (default card) remain UNCHANGED (15/2 and
133 expense/24 transfer). (NOTE: under v5.4 the chase numbers become
133 expense / 11 refund / 13 transfer — see below.)

## Refunds & P2P Pass-through (v5.4)

Two user-chosen modeling changes.

**A) `refund` type — nets against category spend.** The type enum gains
`refund` (`income | expense | transfer | refund`), added to
`TransactionCreate/Out` and rule `set_type`. A refund is a NEGATIVE expense
(NOT excluded like transfer):
- Shared helper `_NET_EXPENSE` in `stats.py` = `+amount` for expense, `−amount`
  for refund, else 0. Summing it yields `Σ(expense) − Σ(refund)`.
- `summary.total_expense` = Σ(expense) − Σ(refund); `net` = income −
  total_expense; `total_income`/`savings`/`savings_rate` unaffected; refunds ARE
  counted in `count`.
- `by-category` (type=expense view): includes both expense+refund rows and sums
  `_NET_EXPENSE` per category (net total; pct on net totals). Other `type`
  values keep the plain per-type sum.
- `over-time`: period `expense` = Σ(expense) − Σ(refund); `net` adjusts.
- `by-account`: per-account `expense` nets refunds.
- `transfer` stays FULLY excluded everywhere; `_date_filter` still excludes only
  transfer, so refund rows are included in what stats see.
- **CSV mapping (changed from v5.1)**: Type-column `return`/`refund`/`reversal`
  now map to `refund` (was `transfer`). So Chase `Return` rows and labeled
  refunds net against their category.

**B) Peer-to-peer pass-throughs default to `transfer`.**
`rules_engine.P2P_TOKENS` (venmo, zelle, cash app, cashapp) + `is_p2p()`. On
import, when no user rule and no explicit Type-column value dictates otherwise,
a P2P row DEFAULTS to `type=transfer` (excluded — reimbursement pass-through)
and is flagged `needs_review` with reason
`"Assumed pass-through transfer (<token>) — reclassify if income/expense"`.
Checked before brokerage/ambiguous in the review-reason precedence.

Verified (temp DB): expense $100 + refund $30 (Shopping) → by-category Shopping
= 70, summary total_expense 70, income/savings unchanged, count 2. Importing
`chase_sample.csv` → 133 expense / 11 refund / 13 transfer; total_expense drops
2302.07 → **2014.72** (− the 287.35 of refunds); count 144 (transfers excluded,
refunds included). A bank CSV with Venmo + Zelle → both `transfer` +
needs_review pass-through reason, excluded from income/expense.

## Postgres / Neon (v6)

The app runs on **SQLite** (local dev + tests) and **Postgres** (Neon, for real
persistence + Render deploy) from the SAME code — the dialect is auto-selected by
`DATABASE_URL`. Nothing about the SQLite path changed.

- **Selection**: `DATABASE_URL` unset → the local SQLite file
  (`expense_tracker.db`). `postgresql://…` → Postgres. `database.py` branches on
  whether the URL starts with `sqlite`:
  - SQLite → `create_engine(url, connect_args={"check_same_thread": False})`.
  - Postgres → `create_engine(url, pool_pre_ping=True)` (no `check_same_thread`;
    `pool_pre_ping` re-validates connections that Neon's serverless layer drops
    when idle).
- **Driver**: `psycopg2-binary` is in `requirements.txt`. Nothing to configure
  beyond the URL.
- **Migrations are dialect-portable**: the `_migrate_add_*` startup helpers now
  detect existing columns via `sqlalchemy.inspect(engine).get_columns(table)`
  (works on both dialects — no SQLite-only `PRAGMA`), then run a portable
  `ALTER TABLE … ADD COLUMN` with portable DDL types (VARCHAR/BOOLEAN/INTEGER).
  On a fresh Postgres DB `create_all` builds every column, so the helpers find
  the columns present and are no-ops — they never crash on a freshly created
  table (the helper returns early if the table doesn't exist or the column is
  already there).
- **over-time period label is dialect-aware** (`stats.py` `_period_expr`):
  SQLite uses `strftime`, Postgres uses `to_char` — producing the SAME labels
  (`YYYY` / `YYYY-MM` / `YYYY-Www` via ISO week `IYYY-"W"IW` / `YYYY-MM-DD`), so
  the frontend is unaffected.

**Run / deploy:**
```bash
# Local SQLite (default) — unchanged
uvicorn src.api.main:app --reload --port 8000

# Postgres / Neon (set the connection string; do NOT commit it)
export DATABASE_URL='postgresql://<user>:<pass>@<host>/<db>?sslmode=require'
uvicorn src.api.main:app --port 8000
```
On Render, set `DATABASE_URL` to the Neon connection string in the service env;
`create_all` + the portable migrations run on startup. Tests always use in-memory
SQLite and are unaffected.

## Notes

- `savings` in stats = sum of expense rows where category is `Savings` or
  `Investment` (per contract definition).
- `savings_rate` = savings / total_income; returns `0.0` when income is zero.
- CSV import is BOM-tolerant UTF-8 with Latin-1 fallback; skips bad rows and
  reports them in `errors[]`.
- CORS is open to `http://localhost:3000` and `*` (dev mode).
- SQLite `check_same_thread=False` is set; safe for single-process dev use.
