# Expense Tracker — API Contract & Data Model (v9)

This is the **shared source of truth** for Backend, Frontend, and QA. Build to this exactly.
Backend confirms it is live by messaging Frontend once the server runs.

## Stack & Ports
- Backend: FastAPI + SQLAlchemy + SQLite. Runs on **http://localhost:8000**. All routes under `/api`.
- Frontend: React + Vite + TypeScript. Dev server on **http://localhost:3000**, proxies `/api` → `:8000`.
- DB file: `expense_tracker.db` at project root (configurable via `DATABASE_URL` env var).

## Data Model — `transactions` table
| field        | type                         | notes |
|--------------|------------------------------|-------|
| id           | int, PK, autoincrement       | |
| date         | date (ISO `YYYY-MM-DD`)      | the day the money moved |
| amount       | float, > 0                   | always positive magnitude |
| type         | enum: `income` \| `expense` \| `transfer` | direction of money |
| category     | string                       | e.g. Salary, Rent, Groceries, Investment, Dining, Transport |
| description  | string, nullable             | free text |
| account      | string, nullable             | which card/account, e.g. `Discover`, `Chase`, `BofA` (v4) |
| source       | enum: `manual` \| `csv`      | how it was entered; default `manual` |
| created_at   | datetime, server-set         | |

Savings/investment are modeled as `expense` with category `Savings` / `Investment` (money leaving spendable cash).
This keeps the model simple while letting stats compute savings rate.

**`transfer` type (v2): money moving between the user's own accounts — NOT spending or income.**
The motivating case: a credit-card statement CSV contains both the purchases (expenses) AND the
payment that pays them off ("INTERNET PAYMENT - THANK YOU", category "Payments & Credits"). Counting
that payment as an expense would double-count the purchases. So `transfer` rows are recorded (visible
in the ledger) but **EXCLUDED from ALL stats**: total_income, total_expense, net, savings, savings_rate,
by-category, and over-time. They never appear in spending breakdowns.

**`account` field (v4): which card/account a transaction belongs to.**
A bank CSV does NOT identify itself (a Discover export has no "Discover" column), so the account is set at
entry time: chosen in the add/edit form for manual rows, and passed as a form field on CSV import (applied to
every row in that file). Free-text string, nullable; treat null/empty as "Unassigned" in the UI. Distinct
accounts are discoverable via `GET /api/accounts` (same pattern as categories). Every list/stats endpoint
accepts an optional `account` filter; a new `GET /api/stats/by-account` gives per-card spending. This lets the
user see total spending AND spending broken down by card.

## Pydantic Schemas
- `TransactionCreate`: `{ date, amount, type, category, description? , source?, account? }`
- `TransactionOut`: all columns including `id`, `created_at`, `account`.
- Input rules (`amount > 0`, non-empty `category`) are enforced on
  `TransactionCreate` (POST and PUT bodies) **only**. `TransactionOut` shares
  the fields via a validator-free `TransactionBase` and never re-applies them:
  response validation runs on every row a read returns, so one stored row that
  breaks an input rule would otherwise 500 the whole list. Stored data never
  fails a read (regression: $0 Plaid brokerage rows 500'd `GET /api/transactions`).

## Endpoints

### Transactions (CRUD)
- `POST   /api/transactions` → body `TransactionCreate` → `201 TransactionOut`
- `GET    /api/transactions` → query: `start_date?`, `end_date?`, `type?`, `category?`, `account?`, `limit?=100`, `offset?=0` → `200 TransactionOut[]` (newest first)
- `GET    /api/transactions/{id}` → `200 TransactionOut` | `404`
- `PUT    /api/transactions/{id}` → body `TransactionCreate` → `200 TransactionOut` | `404`
- `DELETE /api/transactions/{id}` → `204` | `404`

### CSV Import
- `POST /api/transactions/csv` → `multipart/form-data` field `file` (text/csv), plus optional form field `account` (string, v4) — when provided, every imported row is tagged with that account/card. (A CSV doesn't identify its own card, so the UI prompts for it at import.)

  **Header mapping (v3) — REQUIRED. Real bank CSVs do NOT use our exact header names. Be broad and bank-agnostic.**
  Normalize each header (lowercase, strip surrounding whitespace/quotes, remove `.` and `_`, collapse inner whitespace to single spaces) then match against alias lists. Match by EXACT normalized equality first, then fall back to `contains` (so `transaction posting date` still maps to date). First field-alias that resolves (in list order) wins. Put every alias list in a module-level dict (`COLUMN_ALIASES`) so it's trivial to extend.
    - **date** ← `date`, `transaction date`, `trans date` (covers `Trans. Date`), `transaction posted date`, `posted date`, `post date`, `posting date`, `date posted`, `effective date`, `booking date`, `value date`, `process date`, `activity date`, `completed date`, `run date`. **Prefer a transaction/trans date over a post/posting date** — order the list so trans-date aliases come first.
    - **amount** (single signed column) ← `amount`, `amt`, `transaction amount`, `value`, `net amount`.
    - **debit** (money out, separate column) ← `debit`, `debit amount`, `withdrawal`, `withdrawals`, `money out`, `paid out`, `outflow`, `payments`.
    - **credit** (money in, separate column) ← `credit`, `credit amount`, `deposit`, `deposits`, `money in`, `paid in`, `inflow`.
    - **description** ← `description`, `transaction description`, `original description`, `extended description`, `memo`, `payee`, `name`, `merchant`, `details`, `narration`, `particulars`, `reference`, `notes`.
    - **category** ← `category`, `transaction category`, `classification`.
    - **type / direction indicator** ← `type`, `transaction type`, `dr/cr`, `debit/credit`, `cr/dr`, `direction`. (See "type column handling" below — bank `Type` values are usually `DEBIT`/`CREDIT`/`SALE`/`PAYMENT`, NOT our `income/expense/transfer`.)
    - **ignore** (never used as amount/date) ← `balance`, `running balance`, `running bal`, `available balance`, `card no`, `card number`, `check or slip #`, `status`.
  If no date alias resolves OR no amount/debit/credit resolves, the WHOLE file fails fast with a clear, specific error naming which required column wasn't found AND listing the headers actually seen (so the user knows what to add to the alias list).

  **Amount resolution:**
    - If a single signed **amount** column resolved → use it (sign handled by the credit-card convention below).
    - Else if **debit** and/or **credit** columns resolved → a non-empty `debit` → `expense` (magnitude = |debit|); a non-empty `credit` → inflow (`income`, unless a payment/transfer token matches → `transfer`). Treat blank/`0`/empty cells as "not this side."

  **Type / direction column handling:** if a `type`/direction column resolved, map its VALUE (normalized) — `debit`/`dr`/`withdrawal`/`sale`/`purchase` → expense; `credit`/`cr`/`deposit` → income; `payment`/`xfer`/`transfer`/`acct_xfer` → transfer; `return`/`refund`/`reversal` → transfer (a refund; excluded from spend, editable per-row). Our literal `income`/`expense`/`transfer` values also pass through. **An explicit, recognized direction value WINS over sign inference — critical because banks disagree on sign.** Unrecognized → fall back to sign/token inference (don't error).

  **⚠️ Banks use OPPOSITE sign conventions — the `Type` column is the source of truth when present:**
    - **Discover** (NO type col): `+` = purchase (expense), `−` = payment. Use the credit-card sign convention below.
    - **Chase** (HAS `Type` col `Sale`/`Payment`/`Return`): `Sale` rows are NEGATIVE amounts but are EXPENSES; `Payment`/`Return` are POSITIVE. So you MUST trust `Type` over sign here: `Sale -4.79` → a **$4.79 expense** (store `abs(amount)`), `Payment 146.84` → transfer, `Return 24.96` → transfer (refund). Never let the negative sign on a Chase `Sale` flip it to income.
    - In all cases: store `amount` as positive magnitude (`abs`); type is decided by (1) recognized `type`-column value, else (2) transfer tokens, else (3) the single-signed-amount credit-card convention.

  **Date parsing:** accept `MM/DD/YYYY` (Discover) and ISO `YYYY-MM-DD`. Store ISO.

  **Sign convention when there is NO `type` column and a single signed `amount` (credit-card statements like Discover):**
    - This is **credit-card convention: positive amount = a purchase (`expense`), negative amount = a payment/credit.**
    - amount is stored as positive magnitude (`abs`); the sign only determines type.

  **Transfer auto-classification (runs BEFORE sign inference; explicit `type` column always wins):** classify a row as `transfer` if EITHER:
    - `category` (normalized) matches any of: `payments and credits`, `payments & credits`, `payment`, `transfer`, `credit card payment`, OR
    - `description` contains (case-insensitive) any of: `internet payment`, `thank you`, `autopay`, `payment - thank you`, `payment thank you`.
    Keep the category tokens and description tokens in TWO module-level lists so they're easy to extend.
    Any remaining **negative-amount** row that is NOT a payment (i.e. a refund) → also classify as `transfer` (so it never inflates income or offsets spend incorrectly); user can edit the type per-row in the UI. Log/return these in the `transfers` count too.

  - Response `200`: `{ "imported": <int>, "skipped": <int>, "transfers": <int>, "errors": [ {"row": <int>, "reason": <str>} ] }`
    (`transfers` = how many imported rows were classified as transfer, so the UI can tell the user "N payment/transfer rows were excluded from spending".)
  - **Reference fixtures (real exports):**
    - `tests/fixtures/discover_sample.csv` (Discover, 15 rows): **13 expenses** (Restaurants/Merchandise), **2 transfers** (INTERNET PAYMENT), 0 skipped. The 2 −payment rows must not appear in spending stats.
    - `tests/fixtures/chase_sample.csv` (Chase, has `Type` column, ~160 rows, dates back to Jan 2026): every `Sale` (negative amount) → an EXPENSE with positive magnitude; every `Payment` → transfer; every `Return` → transfer. 0 skipped. No `Sale` may be classified as income despite its negative sign. This fixture is the regression guard for the opposite-sign-convention bug.
  - A downloadable template is available at `GET /api/transactions/csv/template` → `text/csv`.

### Statistics (this is what powers patterns & year-end stats)
**All stats endpoints accept an optional `account` filter (v4): when present, restrict to that card/account. When absent, aggregate across ALL accounts (total spending). All stats MUST exclude `type = transfer` rows.**
- `GET /api/stats/summary` → query `start_date?`, `end_date?`, `account?` →
  `{ "total_income": float, "total_expense": float, "net": float, "savings": float, "savings_rate": float, "count": int }`
  (`savings` = sum of expense rows in categories Savings+Investment; `savings_rate` = savings / total_income)
- `GET /api/stats/by-category` → query `start_date?`, `end_date?`, `type?=expense`, `account?` →
  `[ { "category": str, "total": float, "count": int, "pct": float } ]` (descending by total). Excludes transfers.
- `GET /api/stats/over-time` → query `granularity=year|month|week|day` (default `month`), `start_date?`, `end_date?`, `account?` →
  `[ { "period": <label>, "income": float, "expense": float, "net": float, "savings": float } ]` (ascending). Excludes transfers.
  period label: `year`→`YYYY`, `month`→`YYYY-MM`, `week`→`YYYY-Www`, `day`→`YYYY-MM-DD`.
- `GET /api/stats/by-account` (v4) → query `start_date?`, `end_date?` →
  `[ { "account": str, "income": float, "expense": float, "net": float, "count": int } ]` (descending by expense). Excludes transfers. Null/empty account reported as `"Unassigned"`. This is the per-card breakdown.
- `GET /api/categories` → `string[]` distinct categories seen, plus sensible defaults.
- `GET /api/accounts` (v4) → `string[]` distinct non-empty accounts seen (for dropdowns/filters).

## Rules Engine + Review (v5)
A user-controlled classification layer for messy statements (esp. debit/checking). User rules run **before** the built-in importer inference and override it; whatever stays `Uncategorized`/ambiguous can be AI-categorized on demand and/or flagged for review.

### Data model additions
- **New `rules` table**: `id` (PK), `name` (str, optional), `priority` (int, lower runs first; default 100), `enabled` (bool, default true), `match_field` (`description`|`category`|`account`|`any`), `match_op` (`contains`|`equals`|`regex`), `match_value` (str), `amount_min`/`amount_max` (float, nullable), `set_type` (`income`|`expense`|`transfer`, nullable = keep inferred), `set_category` (str, nullable), `set_account` (str, nullable), `created_at`. **First matching enabled rule (ordered by priority, then id) wins.**
- **`transactions` gains** `needs_review` (bool, default false) + `review_reason` (str, nullable). Added via the same PRAGMA-introspect → `ALTER TABLE` startup-migration pattern as `account`.

### Classification order (CSV import, per row)
1. Parse date/amount/category/description/account (existing).
2. **Apply user rules** (`apply_rules`): first match sets `type`/`category`/`account` per the rule's non-null actions — overrides built-in.
3. If no rule matched → existing built-in inference (explicit Type col → transfer tokens → debit/credit → sign → default).
4. **Flag `needs_review`** = (no rule matched) AND (`category=="Uncategorized"` OR description matches a module-level `AMBIGUOUS_TOKENS` list: venmo, zelle, cash app, atm, withdrawal, check, e-check, cash deposit, …); set a short `review_reason`.

### Brokerage savings-vs-transfer prompt (v5.1)
A deposit to a brokerage/investment account from checking is ambiguous: it could count toward the user's **savings rate** (modeled as an `expense` in category `Investment`/`Savings`) OR be a neutral **transfer** (excluded from everything). We don't guess globally — we detect it, default safe, and let the user decide once per institution (the choice becomes a rule).
- Module-level `BROKERAGE_TOKENS` (e.g. `robinhood`, `fidelity`, `vanguard`, `schwab`, `charles schwab`, `e*trade`/`etrade`, `coinbase`, `wealthfront`, `betterment`, `merrill`, `sofi invest`, `acorns`, `td ameritrade`, `webull`). Helper `is_brokerage(description)`.
- During import (step 3, only when **no user rule matched**): if the description matches a brokerage token, default the row to `type=transfer` (safe — never inflates spend) AND flag `needs_review=true` with a DISTINCT `review_reason` of the form `"Brokerage: count as savings or keep as transfer? (<token>)"`. A user rule always overrides (so once they choose, future deposits are auto-handled and not re-flagged).
- **No stats-math change**: choosing "savings" creates a rule `→ set_type=expense, set_category=Investment` (already counted by `savings`/`savings_rate`); choosing "transfer" creates `→ set_type=transfer`. The frontend recognizes the `Brokerage:` review_reason and offers the two-way choice inline in the review queue, calling the existing `POST /api/rules` + `POST /api/rules/apply`.

### Import history + reassign (v5.2)
So a user can see past imports and reassign one to a card after the fact (e.g. forgot to pick the card, or imported as Unassigned).
- **New `import_batches` table**: `id` (PK), `filename` (str), `account` (str, nullable), `imported` (int), `skipped` (int), `transfers` (int), `needs_review` (int), `created_at`. 
- **`transactions` gains `batch_id`** (int, nullable; references `import_batches.id`) via the same PRAGMA-introspect → `ALTER TABLE` migration pattern. Manual transactions have null `batch_id`.
- On CSV import: create one `import_batches` row, tag every imported transaction with its `batch_id`. `CSVImportResponse` gains `batch_id: int`.
- Endpoints:
  - `GET /api/imports` → `[{ id, filename, account, imported, skipped, transfers, needs_review, created_at }]`, newest first.
  - `POST /api/imports/{id}/reassign` → body `{ account: str|null }` → sets the account on the batch AND all its transactions → `{ "updated": int }` | `404`. (empty/null account → Unassigned.)
  - `DELETE /api/imports/{id}` → deletes the batch and all its transactions (undo an import) → `204` | `404`.

### Bank/checking statements: preamble + sign convention (v5.3)
Checking/savings exports differ from credit cards in three ways the importer must handle:
- **Preamble skipping.** Many bank CSVs (e.g. BofA) prefix a summary block before the real transaction table:
  ```
  Description,,Summary Amt.
  Beginning balance …/Total credits …/Total debits …/Ending balance …
  <blank>
  Date,Description,Amount,Running Bal.   ← the real header
  ```
  The importer must **scan the leading lines and pick the real header = the first row where BOTH a `date` alias AND an amount/`debit`/`credit` alias resolve**, skipping everything before it. (Fixes the 400 `Could not resolve a required 'date' column. Headers seen: ['Description','','Summary Amt.']`.) If no such row exists in the file, fail as before.
- **Statement type / sign convention.** Import gains an optional form field **`statement_type` = `card` (default) | `bank`**:
  - `card` (single signed amount): `+` → expense, `−` → payment/credit. (unchanged v3 behavior)
  - `bank` (checking/savings): **`−` → expense (outflow), `+` → income (inflow).** The opposite — this is the fix for paychecks importing as expenses.
  - The FRONTEND pre-selects `bank` when a running-balance/balance column is detected (auto-default), user-overridable via a Credit card / Bank account selector on the import page.
  - Precedence unchanged: a recognized `Type`/direction column and separate `debit`/`credit` columns still win over sign; transfer tokens, brokerage detection, and user rules override in both modes.
- **Bank-side credit-card payment detection.** Module-level `CARD_PAYMENT_TOKENS` (e.g. `des:e-payment`, `des:epay`, `des:ccpymt`, `credit crd`, `wells fargo card`, `bilt card des:pmt`, `online banking payment to crd`, `card des:pmt`) → classify as `transfer`, so paying a card from checking isn't counted as spend and doesn't double-count the card's purchases. Extensible list. Genuinely ambiguous rows (Venmo, Zelle, `Discover (CONA) NET/MOBILE` bank moves, cash) stay in **needs-review** rather than being force-classified.
- `ImportBatch` may record `statement_type` (optional, for history clarity).

### Refunds & pass-through (v5.4) — user-chosen modeling
- **Refunds NET against category spend (chosen).** Add a 4th type: `refund` (enum now `income | expense | transfer | refund`). A refund reduces spending in its own category rather than being excluded or counted as income. Stats treat it as a NEGATIVE expense:
  - `summary.total_expense` = Σ(expense) − Σ(refund); `net` = income − (expense − refund); `savings`/`total_income` unaffected; refunds included in `count`.
  - `by-category`: a category's total = Σ(expense in cat) − Σ(refund in cat) (net; may be small/zero; pct on net totals).
  - `over-time`: subtract refunds from the period's expense.
  - Transfers still fully excluded.
  - **CSV mapping**: `return`/`refund`/`reversal` (Type column) and clearly-labeled refund rows → `refund` (NOT transfer as in v5.1). So Chase `Return` rows and an `APPLE.COM/BILL ... REFUND` net against their category.
  - Frontend: `refund` option in the add/edit type control; display it distinctly; it reduces category spend.
- **Peer-to-peer pass-throughs default to `transfer` (chosen).** Venmo / Zelle / Cash App / peer transfers should DEFAULT to `type = transfer` (excluded — reimbursement pass-through, not income/spend) while STILL being flagged `needs_review` so the user can reclassify a genuine income/expense one. (Previously these were flagged review but took the sign-inferred income/expense type.) Implement by treating the P2P tokens as a transfer-default in `_infer_type` when no rule/Type-column dictates otherwise, with `review_reason` noting it's an assumed pass-through.

### Schemas
- `RuleCreate`/`RuleOut`/`RuleUpdate`. `TransactionCreate`/`TransactionOut` gain `needs_review`, `review_reason`. `CSVImportResponse` gains `needs_review: int` (+ `batch_id: int`, v5.2). `ImportBatchOut` (v5.2): the fields above. `type` enum includes `refund` (v5.4).

### Endpoints
- `POST /api/rules` → `201 RuleOut`; `GET /api/rules?enabled?` → `RuleOut[]` (priority asc); `GET /api/rules/{id}` → `RuleOut`|404; `PUT /api/rules/{id}` → `RuleOut`|404; `DELETE /api/rules/{id}` → `204`|404.
- `POST /api/rules/apply` → body `{account?, only_review?}` → re-apply enabled rules to existing transactions → `{ "updated": int }`.
- `POST /api/rules/preview` → body `RuleCreate` → `{ "matches": int }` (how many existing transactions this rule would hit; powers the "apply to N existing" confirm when learning from an edit).
- `GET /api/transactions` gains `needs_review?` filter (true/false). Existing `PUT` persists `needs_review`/`review_reason`/`category`/`type`.
- `POST /api/assistant/categorize-batch` → body `{ids?: int[], only_uncategorized?: bool, account?, start_date?, end_date?}` → for each target txn, reuse `ai.suggest_category(...)`; update `category` and clear `needs_review` when confident → `{ results: [{id, category, confidence}] }`. Gated by `OPENAI_API_KEY` (503 when unset), like other AI endpoints.

### Reference fixture
- `tests/fixtures/debit_sample.csv` — synthetic checking statement covering: paycheck (income), credit-card payment (transfer), brokerage deposit, Venmo, Zelle, ATM withdrawal, cash deposit, utilities, check. Regression guard for debit handling + needs_review flagging.

## Duplicate Detection (v7)
Surfaces likely-duplicate charges so the user can review and dismiss them. One
**dynamic** rule catches BOTH real merchant double-charges AND accidentally
re-imported / overlapping-statement rows — a re-import produces rows with an
identical `date` + `amount` + merchant + account, which is exactly what this rule
groups on. No separate "import dedupe" pass is needed; re-imports are covered by
the same rule.

### Data model addition
- **`transactions` gains `dup_dismissed`** (bool, default false). Added via the
  same `inspect(engine).get_columns(...)` → `ALTER TABLE ADD COLUMN` startup-migration
  pattern as `needs_review` (`_migrate_add_dup_dismissed_column`, portable DDL
  `BOOLEAN NOT NULL DEFAULT FALSE` — works on SQLite and Postgres). `TransactionCreate`/
  `TransactionOut` gain `dup_dismissed: bool = False` (round-trips through `PUT` like
  `needs_review`).

### Duplicate-group rule
A **duplicate group** = 2+ transactions where ALL of these match:
- `type == "expense"` (income / transfer / refund are ignored)
- exact same `date`
- exact same `amount` rounded to 2 decimals
- same **normalized description**: `" ".join((description or "").split()).lower()`
- same **normalized account**: `(account or "").strip().lower()`

Rows with `dup_dismissed == True` are **excluded from grouping entirely**, so a
group only forms among non-dismissed rows (count >= 2). Grouping is computed
**in Python** (portable — no reliance on DB string functions).

### Schemas
- `DuplicateGroup`: `{ group_key: str, date: date, amount: float, description: str|null, account: str|null, count: int, total_extra: float, transactions: TransactionOut[] }`
  - `group_key` = stable string `f"{date}|{amount:.2f}|{normdesc}|{normacct}"`.
  - `total_extra` = `round((count - 1) * amount, 2)` — the wasted spend if all but one are true duplicates.
  - `transactions` = the actual rows in the group, **newest first**.
- `DismissDuplicatesRequest`: `{ ids: int[] }`.

### Endpoints
- `GET /api/duplicates` → query `start_date?`, `end_date?`, `account?` (same style as stats) →
  `200 DuplicateGroup[]`. Considers only `type == "expense"` AND `dup_dismissed == false` rows
  passing the filters; emits only groups with `count >= 2`; **sorted by `total_extra` descending**.
- `POST /api/duplicates/dismiss` → body `DismissDuplicatesRequest` `{ ids: int[] }` → sets
  `dup_dismissed = true` on every existing transaction whose id is in `ids` →
  `{ "dismissed": <int rows updated> }` (ids that don't exist are ignored; empty list → `0`).

## Persistence / Postgres (v6)
The app must run on **SQLite (local dev/tests) AND Postgres (Neon, for real persistence + Render deploy)** from the same code, selected by `DATABASE_URL`.
- `DATABASE_URL` unset → SQLite file (current default). `postgresql://…` → Postgres.
- **Dialect-portable code required:**
  - `database.py`: only pass `connect_args={"check_same_thread": False}` for SQLite; for Postgres use no such arg and set `pool_pre_ping=True` (Neon serverless connections drop). Add the `psycopg2-binary` driver to requirements.
  - Startup column migrations (`_migrate_add_*`) must NOT use SQLite `PRAGMA`; use SQLAlchemy `inspect(engine).get_columns(...)` to detect existing columns (works on both), then `ALTER TABLE ADD COLUMN` (portable). On a fresh Postgres DB `create_all` builds everything, so migrations are no-ops.
  - `stats.py` over-time period label uses SQLite-only `func.strftime`. Make it dialect-aware: SQLite → `strftime`; Postgres → `to_char(date, 'YYYY' | 'YYYY-MM' | 'IYYY-"W"IW' | 'YYYY-MM-DD')`. Same output labels per the contract.
- Tests keep using in-memory SQLite (unchanged, must stay green). Postgres verified manually against Neon.

## Errors
- Validation → `422` (FastAPI default). Not found → `404` `{ "detail": "..." }`.
  Every deliberate `HTTPException` (4xx, and the Plaid 502/503s) keeps FastAPI's
  default `{ "detail": ... }` shape — unchanged by the 500 handling below.
- **Unhandled server error → `500` JSON** (`src/api/errors.py`). Any exception a
  route doesn't catch, and any `ResponseValidationError` (the route returned data
  its `response_model` rejects, e.g. a stored row that breaks a schema rule),
  comes back as:
  ```json
  {
    "detail": "Server error: ResponseValidationError: row 0 (id 1662) amount: Input should be greater than 0 (got 0.0) (error ID 3f9c2a1b)",
    "error": {
      "id": "3f9c2a1b",
      "type": "ResponseValidationError",
      "message": "row 0 (id 1662) amount: Input should be greater than 0 (got 0.0)",
      "status": 500,
      "method": "GET",
      "path": "/api/transactions"
    }
  }
  ```
  - `detail` is always a human-readable string, so detail-only clients still work.
  - `error.id` is 8 hex chars, fresh per failure. The **full stack trace is logged
    server-side only** (logger `expense_tracker.errors`, line
    `Unhandled <Type> [error_id=<id>] <METHOD> <path>: <message>`). Search the
    Render logs for the ID. The browser never receives a traceback.
  - `error.type` + `error.message` are the exception class and message (capped at
    300 chars). For a `ResponseValidationError` the message is the first pydantic
    error as `row <index> (id <row id>) <field>: <msg> (got <input>)`, plus
    `(+N more)` when there are others. The row part is omitted for a single-object
    response, and `(id …)` when the row has no recoverable id.
  - Handled in a pure-ASGI middleware that sits **inside** `CORSMiddleware`, so the
    500 carries CORS headers and a cross-origin browser can read the body (a plain
    `@app.exception_handler(Exception)` runs outside CORS and would not).
  - The frontend turns every failure into an `ApiError` (`status`, `method`,
    `path`, `errorId`, `type`, `cause`), including network failures (`status: null`,
    "Can't reach the server…"). `<ErrorNotice>` shows a plain-English headline with
    these in a collapsible **Details** block with a Copy button.

## AI assistant (OpenAI)
All `/api/assistant/*` AI features run on the OpenAI API (official `openai` SDK,
Chat Completions) from `src/api/ai.py`.
- **Config:** `OPENAI_API_KEY` (required; unset → every AI endpoint returns **503**
  `"… Set OPENAI_API_KEY on the server."` and `GET /api/assistant/status` returns
  `{enabled: false}`), `OPENAI_MODEL` (optional, default `gpt-4o-mini`). Each
  request has a 30 s timeout and 1 SDK retry (connection errors, 429, 5xx).
- **Endpoints:** `GET /status` → `{enabled}`; `POST /chat` `{messages:[{role,
  content}]}` → `{reply: str, tool_calls: str[]}` (deduped names of the tools the
  model ran, e.g. `create_category_budget`); `POST /categorize` → `{category,
  confidence}`; `POST /categorize-batch` (above); `POST /budget` (below);
  `GET /insights?start_date&end_date` → `{insights: str}`.
- **Chat** is a tool-calling loop (`tools` / `tool_calls`, max 8 rounds) over
  `ai.execute_tool()`: read tools (`get_financial_summary`,
  `get_spending_by_category`, `get_income_expense_over_time`,
  `search_transactions`) and create tools (`create_categorization_rule`,
  `create_savings_goal`, `create_category_budget`). A tool exception (or bad
  arguments) is returned to the model as `{"error": "..."}` so it can recover.
  History roles `assistant`/`model` → `assistant`; anything else → `user`.
- **Categorize / budget** use strict JSON-schema structured output parsed into
  pydantic models; a model refusal → **502** with the refusal text.
- **OpenAI failures** come back as a readable string `detail` starting
  `"AI request failed: "` (never a traceback, never any part of the key):

  | Cause | Status |
  |---|---|
  | Key rejected (401), no access to the model (403), model not found (404) | 503 |
  | Rate limit, or account out of quota/credits (429) | 503 |
  | Timeout, connection failure, OpenAI 5xx / other API error, refusal, reply cut off | 502 |

## CORS
- Allow origin `http://localhost:3000` (and `*` is fine for dev).

## Health
- `GET /api/health` → `{ "status": "ok" }`

## Authentication (Clerk)

All `/api/*` routes are protected by Clerk session-JWT verification plus a
single-user email allowlist — but this is **GATED**. With no Clerk env set, auth
is **DISABLED**: the app behaves exactly as before, every `/api` route is open,
local dev works, and the full test suite passes. Auth turns **ON** only when
configured. (Same "gated by env var" pattern as the AI assistant and Plaid.)

### What enables it
Auth is enabled when **`CLERK_ISSUER`** (or **`CLERK_JWKS_URL`**) is set. Until
then, `is_auth_enabled()` is `False` and the `require_user` dependency is a no-op.

### Config (environment — see `.env.example`)
| var | default | purpose |
| --- | --- | --- |
| `CLERK_ISSUER` | — | Clerk Frontend API URL, e.g. `https://<subdomain>.clerk.accounts.dev`. Presence enables auth; the JWT `iss` must equal it. |
| `CLERK_JWKS_URL` | `${CLERK_ISSUER}/.well-known/jwks.json` | Signing-key endpoint (RS256). Set only to override. |
| `CLERK_SECRET_KEY` | — | Optional `sk_...`; enables a server-side email lookup (`GET https://api.clerk.com/v1/users/{sub}`) when the token carries no `email` claim. |
| `ALLOWED_EMAILS` | — | Comma-separated allowlist (case-insensitive). Empty = any authenticated Clerk user is allowed. Single-user: set to your email. |
| `PLAID_SYNC_TOKEN` | — | Shared secret (also used by `sync-all`). A matching `X-Plaid-Sync-Token` header is exempt from Clerk auth. |
| `VITE_CLERK_PUBLISHABLE_KEY` | — | **Frontend** key (`pk_...`), consumed by the React app, not the API. Set on the frontend build/host. |

### Verification steps (`require_user`)
When enabled, each protected request must send `Authorization: Bearer <clerk-session-jwt>`. The dependency:
1. If auth disabled → allow (return `None`), no checks.
2. If a valid `X-Plaid-Sync-Token` matches `PLAID_SYNC_TOKEN` → allow (cron exemption).
3. Missing/malformed `Authorization` header → **401**.
4. Verify the JWT with PyJWT (RS256) using a cached `PyJWKClient(CLERK_JWKS_URL)`: signature, `exp`/`nbf`, and `iss == CLERK_ISSUER`. Invalid/expired/network error → **401** (fails closed). `azp` is not hard-failed.
5. Resolve email: prefer an `email` claim; else, if `CLERK_SECRET_KEY` is set, look it up via the Clerk API (cached per `sub`).
6. If `ALLOWED_EMAILS` is non-empty and the email is missing or not in the list → **403**.
7. Returns `{sub, email}`.

### 401 vs 403
- **401 Unauthorized** — no/malformed token, or the token fails verification (bad signature, expired, wrong issuer). *"You are not authenticated."*
- **403 Forbidden** — the token is valid but the user's email is not on `ALLOWED_EMAILS` (or can't be resolved while an allowlist is set). *"You are authenticated but not allowed."*

### Exemptions (always open)
- `GET /api/health` — never protected (Render health checks).
- The static SPA / catch-all — serves the login page.
- `POST /api/plaid/sync-all` — reachable via `X-Plaid-Sync-Token` (Render cron), no Clerk user needed.

### How to configure
1. Create a Clerk application at <https://dashboard.clerk.com>.
2. Copy the **Frontend API URL** → `CLERK_ISSUER` (JWKS URL is derived; override with `CLERK_JWKS_URL` if needed).
3. Copy the **Secret key** (`sk_...`) → `CLERK_SECRET_KEY` (optional; only needed for the email-lookup fallback).
4. Set `ALLOWED_EMAILS` to your email (single-user allowlist).
5. Set `VITE_CLERK_PUBLISHABLE_KEY` (`pk_...`) on the frontend so the React app can sign the user in and attach the session token.

## Plaid Integration (v8)

Pull transactions + investment transactions directly from banks via Plaid Link,
instead of (or alongside) CSV import. **Everything network-touching is gated by
`PLAID_CLIENT_ID`/`PLAID_SECRET`**: with them unset the app boots normally, all
existing tests pass, and every `/api/plaid/*` network endpoint returns `503`
(mirrors the AI assistant's gating). `GET /api/plaid/status` and
`GET /api/plaid/items` always work (they only read the DB).

### Config (environment — see `.env.example`, read via `os.getenv`)
| var | default | purpose |
|-----|---------|---------|
| `PLAID_CLIENT_ID` | — | Plaid client id (required to enable) |
| `PLAID_SECRET` | — | Plaid secret (required to enable) |
| `PLAID_ENV` | `sandbox` | `sandbox` \| `development` \| `production` → API host |
| `PLAID_PRODUCTS` | `transactions,investments` | comma list |
| `PLAID_COUNTRY_CODES` | `US` | comma list |
| `PLAID_REDIRECT_URI` | — | optional OAuth redirect URI |
| `PLAID_SYNC_TOKEN` | — | optional shared secret guarding `POST /plaid/sync-all` |
| `PLAID_AUTOSYNC_INTERVAL_MINUTES` | — | optional; positive int enables in-process auto-sync |

`plaid-python` (pip resolves **40.1.0**) and `apscheduler>=3.10` are in
`requirements.txt`. The **access_token is stored server-side only** on the
`plaid_items` table and is **NEVER returned** by any endpoint.

### Data model additions
- New table `plaid_items`: `id`, `item_id` (unique, indexed), `access_token`
  (server-side only), `institution_id`, `institution_name`, `accounts_json`
  (JSON `{account_id: {name, mask, type, subtype, app_account}}`), `cursor`
  (for `/transactions/sync`), `status` (default `active`), `last_synced_at`,
  `created_at`.
- `transactions` gains: `plaid_transaction_id` (indexed, nullable),
  `plaid_account_id` (nullable), `plaid_item_id` (nullable). Plaid rows set
  `source="plaid"`. Manual/CSV rows leave all three null. `create_all` builds
  `plaid_items`; `_migrate_add_plaid_columns()` (portable inspect()-based
  `ALTER TABLE`) adds the three columns to legacy transactions tables.

### Endpoints (all under `/api/plaid`)
- `GET /status` → `PlaidStatus { configured: bool, env: str, products: string[], items: PlaidItemOut[] }`. **Works without keys** (`configured=false`). Never 503.
- `POST /link-token` body `{ item_id?: int|null }` (body optional) → `{ link_token, expiration }`. With no `item_id`, creates a normal new-link token (products/country codes from env, `client_user_id="local-user"`, optional redirect_uri). With an `item_id`, creates an **update-mode** token instead: bound to that item's `access_token`, `update.account_selection_enabled=true`, and **no `products`** (Plaid rejects products alongside an access_token). **503** if unconfigured; **404** if the `item_id` doesn't exist.
- `POST /exchange` body `{ public_token }` → `PlaidItemOut`. Exchanges for an access_token, fetches accounts + best-effort institution name, stores/updates a `PlaidItem` (account label = `"{name} ••{mask}"`). Clears any recorded `error_code`/`error_message`. **503** if unconfigured; **409** `INSTITUTION_ALREADY_LINKED` when that bank is already connected (see *One Item per institution*).
- `POST /sync` body `{ item_id?: int|null }` → `PlaidSyncResult { items_synced, added, modified, removed, errors }`. Syncs one item, or ALL items when `item_id` is null. **503** if unconfigured; **404** if a given `item_id` doesn't exist; **409** if another sync is already running (see *Sync serialization*).
- `POST /sync-all` (header `X-Plaid-Sync-Token` required only when `PLAID_SYNC_TOKEN` set) → `PlaidSyncResult`. Cron/scheduler friendly; **401** on token mismatch, **409** if a sync is already running, **503** if unconfigured.
- `GET /items` → `PlaidItemOut[]`. **Works without keys.**
- `DELETE /items/{id}` → `204`. Best-effort Plaid `item/remove`, then **deletes that item's imported transactions** and the `PlaidItem` row. **404** if not found.

`PlaidItemOut { id, item_id, institution_id?, institution_name?, accounts: PlaidAccount[], status, error_code?, error_message?, last_synced_at?, created_at }`; `PlaidAccount { account_id, name?, mask?, type?, subtype?, app_account? }`. **No `access_token` field anywhere.**

### Per-item error isolation (v13)
A sync **never fails as a whole because one bank is broken.** Each item syncs in
its own `try`/`except` inside `sync_items()`: a failing item is rolled back,
marked `status="error"` with Plaid's `error_code`/display message persisted on
the row, and reported in `PlaidSyncResult.errors`; every other item still syncs
and commits. So:

- `POST /sync` and `POST /sync-all` return **200 even when some items failed** —
  clients must inspect `errors`, not rely on the request throwing. A `502` now
  means the sync couldn't start at all (e.g. `get_client()` failed), not that one
  institution is unhealthy. This keeps a nightly cron from alerting on a
  condition only a human re-link can clear.
- `PlaidSyncError { item_id, institution?, error_code?, message }`. `error_code`
  is Plaid's own (`NO_ACCOUNTS`, `ITEM_LOGIN_REQUIRED`, …) and is `null` for
  network/unexpected errors, where `message` falls back to the trimmed exception.
- A failed item keeps its previous `last_synced_at` (it reads as the last
  *successful* sync) **and its previous `cursor`** — the rollback discards the
  un-advanced cursor, so a retry re-reads the same page instead of skipping
  transactions.
- The next successful sync clears `status`/`error_code`/`error_message`.

Before v13 the loop had no per-item guard: the first failing item raised straight
out of `sync_items()`, every item ordered after it was silently skipped, and the
route mapped the exception to a `502` that named no institution.

### One Item per institution (v13)
Running Link again on an already-connected bank does **not** refresh it — Plaid
mints a brand-new Item with a new `item_id`, so the upsert-by-`item_id` in
`/exchange` misses and a second `PlaidItem` gets stored covering accounts the
first one already had. Adding one new Schwab brokerage account therefore
re-added the existing Roth IRA, and because `GET /stats/net-worth` and
`compute_holdings()` both iterate **every** item, that account's balance and
holdings were counted twice. Its transactions came back too, under fresh
per-Item ids that the `plaid_transaction_id` uniqueness rule cannot detect.

`/exchange` now refuses it: if the exchanged item's `institution_id` matches an
existing `PlaidItem` (and it isn't the same `item_id`), the route calls Plaid
`item/remove` to release the Item it just minted — an unused Item is still
billable — and returns **409**:

```json
{"detail": {
  "code": "INSTITUTION_ALREADY_LINKED",
  "item_id": 3,
  "message": "Charles Schwab is already connected. Use “Add accounts” on its card …"
}}
```

The supported way to add an account at a linked bank is **update mode**:
`POST /plaid/link-token {item_id}` → Link reopens with account selection against
the SAME Item, so accounts already linked cannot be added twice. The wallet
exposes it as **+ / Add accounts** on each card (and as **Reconnect** on a card
in the error state — same endpoint, same guarantee). A re-link that Plaid hands
back with the *same* `item_id` is a genuine refresh and still upserts in place.

### Sync serialization + duplicate prevention (v13)
`transactions.plaid_transaction_id` carries a **UNIQUE partial index**
(`ux_transactions_plaid_transaction_id ... WHERE plaid_transaction_id IS NOT
NULL`) — one row per Plaid transaction, enforced by the DB. Manual/CSV rows are
unaffected: their `plaid_transaction_id` is `NULL`, and NULLs never collide.

Four things can start a sync — the Render cron, the in-process APScheduler,
Plaid webhooks (which arrive in bursts), and manual syncs from the wallet.
`sync_items()` now takes a **process-wide lock** so they can't overlap; a caller
that waits more than 60s raises `SyncInProgress` →
- `POST /sync` / `POST /sync-all` → **409**,
- `POST /webhook` → `200 {"status": "busy"}` (a retry would only re-queue behind
  the same lock),
- the APScheduler job logs and skips.

Both layers matter: the lock stops the race on a single Render instance, and the
index is the backstop if the service ever runs more than one. `_upsert_transaction`
also self-heals — it takes the oldest row and deletes any others sharing the id,
so a database that predates the index recovers instead of failing forever.

**Migration.** `_migrate_dedupe_plaid_transactions()` (startup, idempotent)
deletes duplicate rows keeping `MIN(id)` per `plaid_transaction_id`, logs exactly
what it removed, then creates the unique index and drops the old non-unique one.

Without this, two concurrent syncs both missed on the same
`plaid_transaction_id` and both inserted it — double-counting the charge in every
stat and budget, and thereafter breaking that item's sync outright: the upsert's
`scalar_one_or_none()` raised `MultipleResultsFound`, which the route wrapped
into a misleading `502 "Plaid request failed: Multiple rows were found when one
or none was required"`.

### Idempotent sync
`POST /sync` loops `/transactions/sync` with the item's stored `cursor` until
`has_more` is false, then **UPSERTs by `plaid_transaction_id`**: `added` → insert
(if new), `modified` → update, `removed` → delete. It also pulls
`/investments/transactions/get` over a rolling ~730-day window and upserts those
by `investment_transaction_id`. The new cursor + `last_synced_at` are persisted,
so re-running sync never duplicates rows (safe to call repeatedly / on a
schedule). Investments are best-effort — an item without the investments product
is skipped, not failed.

### Zero-amount rows are never imported
Plaid reports brokerage **non-cash** events — in-kind share transfers
(`NVIDIA CORP - XFER`), forward and reverse stock splits — with `amount = 0`.
They arrive on the investments path (`/investments/transactions/get`), but the
rule lives in `_upsert_transaction`, so it covers every Plaid import path
(`/transactions/sync` `added` + `modified`, the investments pull, and the
savings-toggle re-sync):
- a $0 row that isn't stored yet is **skipped** (not counted in `added`/`modified`);
- a $0 event for a row that already exists (Plaid modified it to $0, or a
  cursor-reset replay) **deletes** it, every copy, like a `removed` event, and
  counts in `removed`.
This keeps the `amount > 0` invariant for Plaid rows.

### Categorization — trust Plaid (no rules engine / needs-review / AI)
Plaid rows map `personal_finance_category` **directly**; they never run the
rules engine, are never flagged `needs_review`, and are never AI-categorized.
`amount` is stored as the absolute value; `type` is derived from
`personal_finance_category.primary` (PFC) + the sign of Plaid's amount (Plaid
amount is **positive when money leaves** the account):

| PFC primary | type | category label |
|-------------|------|----------------|
| `INCOME` | `income` | Income |
| `TRANSFER_IN` | `transfer` | Transfer |
| `TRANSFER_OUT` | `transfer` | Transfer |
| `LOAN_PAYMENTS` | `transfer` | Payments & Credits |
| `FOOD_AND_DRINK` | expense/refund* | Food & Drink |
| `GENERAL_MERCHANDISE` | expense/refund* | Shopping |
| `TRANSPORTATION` | expense/refund* | Transportation |
| `TRAVEL` | expense/refund* | Travel |
| `RENT_AND_UTILITIES` | expense/refund* | Bills & Utilities |
| `ENTERTAINMENT` | expense/refund* | Entertainment |
| `MEDICAL` | expense/refund* | Health |
| `PERSONAL_CARE` | expense/refund* | Personal Care |
| `GENERAL_SERVICES` | expense/refund* | Services |
| `GOVERNMENT_AND_NON_PROFIT` | expense/refund* | Government |
| `BANK_FEES` | expense/refund* | Fees |
| (missing / unknown PFC) | expense/refund* | Uncategorized |

\* type rule for non-income/non-transfer PFCs: `amount < 0` (money in) →
`refund`, else → `expense`. `description` = `merchant_name` else `name`;
`date` = `authorized_date` else `date`.

Investment transactions always use category `Investment`, amount = `abs()`,
`plaid_transaction_id` = `investment_transaction_id`. To avoid inflating gross
income/expense with IRA/brokerage buy-sell churn, portfolio moves are
**neutralized** as `transfer` (excluded from stats) and only real cash flow is
surfaced. Type is decided from the investment txn's `type` (then `subtype` for
`cash`), normalized to lowercase:

| `type` | `subtype` | mapped type | rationale |
|--------|-----------|-------------|-----------|
| `buy` | — | `transfer` | portfolio churn, neutral |
| `sell` | — | `transfer` | portfolio churn, neutral |
| `transfer` | — | `transfer` | neutral |
| `cancel` | — | `transfer` | neutral |
| `fee` | — | `expense` | real cost |
| `cash` | contains `dividend` or `interest` | `income` | real income received |
| `cash` | contains `contribution` or `deposit` | `expense` | money in → counts toward savings |
| `cash` | contains `withdrawal` or `distribution` | `transfer` | neutral, don't inflate income |
| `cash` | other / absent | `transfer` | neutral |
| unknown / absent | — | `transfer` | neutral default |

`contribution`/`deposit` map to `expense` because savings in this app is
computed from expense rows in Savings/Investment categories, so a contribution
correctly counts as money set aside rather than as spending against income.

**Double-count still solved:** classifying credit-card payments
(`LOAN_PAYMENTS`) and account moves (`TRANSFER_IN`/`TRANSFER_OUT`) as `transfer`
keeps them excluded from income/expense/savings stats — the same guarantee the
CSV path provides — so a card payment isn't counted as both a checking-account
outflow and a card charge.

### Scheduling on Render
The in-process APScheduler job (enabled when `PLAID_AUTOSYNC_INTERVAL_MINUTES`
is a positive int **and** Plaid is configured) works on **always-on** instances.
On Render's **free tier the instance sleeps**, so the in-process scheduler won't
fire reliably — instead create a **Render Cron Job** that runs
`POST /api/plaid/sync-all` with the `X-Plaid-Sync-Token` header (set
`PLAID_SYNC_TOKEN` on both the web service and the cron job). The scheduler is
guarded so it never starts in tests / when unconfigured, and a scheduler failure
never breaks startup.

## Richer Plaid data + multi-account filtering (v9)

Builds on v8: richer per-transaction Plaid metadata, per-account balances,
institution branding, an on-demand holdings endpoint, and a comma-separated
`accounts` multi-select filter across the list/stats/duplicates endpoints. All
network-touching endpoints stay gated by `PLAID_CLIENT_ID`/`PLAID_SECRET` (503
when unset); the app still boots and all existing tests pass with no keys.

### Data model additions
- `transactions` gains (all null for manual/csv rows; populated for Plaid rows):
  `merchant_name` (VARCHAR), `logo_url` (VARCHAR), `pending` (BOOLEAN NOT NULL
  DEFAULT FALSE), `pending_transaction_id` (VARCHAR), `category_icon_url`
  (VARCHAR). Added by `_migrate_add_v9_enrichment_columns()` (portable
  inspect()-based `ALTER TABLE`).
- `plaid_items` gains `institution_logo` (TEXT, base64 PNG) + `institution_color`
  (VARCHAR, hex) — best-effort, null when Plaid returns no branding. Same
  migration helper (never returned as part of the access_token; branding is safe
  to surface).
- `plaid_items.accounts_json` now stores three extra keys per account alongside
  `name/mask/type/subtype/app_account`: `available`, `current`, `currency`
  (`iso_currency_code`). Refreshed on `exchange` and on **every** sync.

### Transaction enrichment mapping (`map_transaction`)
Existing type/category/description/date logic is unchanged. Added return keys:
`merchant_name` (Plaid `merchant_name`), `logo_url` (Plaid `logo_url`, else
`counterparties[0].logo_url`, else null), `pending` (bool), `pending_transaction_id`,
`category_icon_url` (Plaid `personal_finance_category_icon_url`).
`map_investment_transaction` is unchanged (no logo/pending); its rows persist
`pending=false` and null enrichment. Pending→posted reconciliation is unchanged:
Plaid sends the pending id under `removed` and the posted one under
`added`/`modified`, and the existing upsert-by-`plaid_transaction_id` handles it.

`TransactionOut` gains: `merchant_name?`, `logo_url?`, `pending` (bool, default
false), `pending_transaction_id?`, `category_icon_url?`.

### Balances + institution branding
On `exchange` the `accounts/get` response's `balances.available/current/iso_currency_code`
are stored per account; `institutions/get_by_id` is called with
`include_optional_metadata=true` to capture `logo` + `primary_color` (best-effort —
absent/failed metadata leaves the fields null and never breaks exchange). Every
`sync` re-fetches balances (`accounts/get`) before syncing transactions (also
best-effort — a balance failure never fails the sync).

Updated schemas:
- `PlaidAccount { account_id, name?, mask?, type?, subtype?, app_account?, available?: float, current?: float, currency?: str }`
- `PlaidItemOut { id, item_id, institution_id?, institution_name?, institution_logo?: str (base64 PNG), institution_color?: str (hex), accounts: PlaidAccount[], status, last_synced_at?, created_at }`. **Still no `access_token` anywhere.**

### Endpoints
- `GET /api/plaid/holdings` → `HoldingOut[]`. For each item with the investments
  product, calls `investments/holdings/get`, joins holdings→securities, returns
  `HoldingOut { account?, institution?, security_name?, ticker_symbol?, quantity?,
  price?, value?, currency? }` (price = `institution_price`, value =
  `institution_value`). Computed on demand (no storage). **503** if unconfigured;
  **empty list** if there are no investment accounts (items without investments
  are skipped, not failed).

### Multi-account filter — `accounts` param
`GET /api/transactions`, `GET /api/stats/summary`, `GET /api/stats/by-category`,
`GET /api/stats/over-time`, and `GET /api/duplicates` accept an optional
`accounts` query param (comma-separated, e.g. `accounts=Chase ••1234,Amex ••9999`).
Semantics (shared helper `account_filter.account_filter_condition`):
- When `accounts` has ≥1 non-empty token → filter `Transaction.account IN (list)`.
- The legacy single `account` param still works; if **both** are given,
  `accounts` **wins**.
- Absent/blank `accounts` (and no `account`) → **all accounts** (no filter).
`GET /api/stats/by-account` is unchanged (it is the per-account breakdown).

### Exclusion filters — `exclude_types` / `exclude_categories` (`GET /api/transactions`)
`GET /api/transactions` accepts two optional comma-separated exclusion params that
HIDE matching rows:
- `exclude_types` — transaction types (`income`/`expense`/`transfer`/`refund`) to hide,
  e.g. `exclude_types=transfer,refund` → `Transaction.type NOT IN (list)`.
- `exclude_categories` — categories to hide, e.g. `exclude_categories=Investment` →
  `Transaction.category NOT IN (list)`.
Tokens are trimmed and empty tokens ignored (same parsing as `accounts`, via
`account_filter.parse_accounts`). Each applies only when it has ≥1 non-empty token;
absent/blank = no exclusion. They AND with every other filter (start/end/type/
category/account/accounts/needs_review).

## Budgets + Assistant budget-creation (v9b)

Two independent, holistic budget kinds (all accounts, independent of the UI's
account selection). Two brand-new tables created by `create_all()` on startup —
no migration helper needed (create_all builds the full schema; the v6
`_add_column_if_missing` helpers are only for adding columns to existing tables).

### Data model
- `category_budgets`: `id`, `category` (VARCHAR, required), `limit_amount` (FLOAT,
  required), `period` (VARCHAR, default `"monthly"`), `created_at`. Category is
  treated as unique-ish — `POST` upserts by category.
- `savings_goals`: `id`, `name` (VARCHAR, required), `target_amount` (FLOAT,
  required), `target_date` (DATE, nullable), `account` (VARCHAR, nullable — the
  `app_account` label of the designated connected account), `starting_balance`
  (FLOAT, default 0 — captured at creation from that account's current Plaid
  balance), `created_at`.

Computed progress fields are derived on read and never stored.

### Category-limit endpoints (`/api/budgets/categories`)
- `GET /api/budgets/categories` → `CategoryBudgetOut[]`, each:
  `{ id, category, limit_amount, period, created_at, spent, remaining, pct, over }`.
  - `spent` = current CALENDAR-MONTH net expense for that category
    (`Σ expense − Σ refund`, reusing stats.py's `_NET_EXPENSE`; rows with
    `type IN (expense, refund)` and `date` in `[first-of-month, first-of-next-month)`).
    Calendar-month reset, NO rollover.
  - `remaining = limit_amount − spent`; `pct = spent/limit_amount*100`;
    `over = spent > limit_amount`.
- `POST /api/budgets/categories` `{ category, limit_amount, period? }` → **201**,
  UPSERT by category (updates `limit_amount`/`period` if a budget already exists
  for that category, else inserts). `limit_amount` must be > 0.
- `PUT /api/budgets/categories/{id}` — partial update (404 if missing).
- `DELETE /api/budgets/categories/{id}` → **204** (404 if missing).

### Savings-goal endpoints (`/api/budgets/goals`)
- `GET /api/budgets/goals` → `SavingsGoalOut[]`, each:
  `{ id, name, target_amount, target_date, account, starting_balance, created_at,
     current_balance, saved, pct, remaining, monthly_needed, on_track }`.
  - `current_balance` = the designated account's latest balance (look up
    `PlaidItem.accounts_json` by `app_account == goal.account`; use `current`,
    fallback `available`; `null` if no account / not found).
  - `saved = max(0, current_balance − starting_balance)` (0 when balance unknown).
  - `pct = saved/target_amount*100`; `remaining = max(0, target_amount − saved)`.
  - `monthly_needed` = `remaining` ÷ whole months from today to `target_date`
    (clamped ≥ 1); `null` when there is no `target_date`.
  - `on_track` = `saved >= target_amount * elapsed_fraction`, where
    `elapsed_fraction` is today's position along the creation→target_date timeline
    (clamped 0..1); `null` when there is no `target_date`.
- `POST /api/budgets/goals` `{ name, target_amount, target_date?, account? }` →
  **201**. On create, the designated account's current Plaid balance is looked up
  and stored as `starting_balance` (0 when unknown / no account). `target_amount`
  must be > 0.
- `PUT /api/budgets/goals/{id}` — partial update (does NOT recompute
  `starting_balance`; 404 if missing).
- `DELETE /api/budgets/goals/{id}` → **204** (404 if missing).

Shared create helpers in `routes/budgets.py` — `create_category_budget(db,
category, limit_amount, period="monthly")` and `create_savings_goal(db, name,
target_amount, target_date=None, account=None)` — back BOTH the routes and the
Assistant path so persistence is identical. `_account_balance(db, app_account)`
is reused by goal creation + progress.

### Assistant budget-creation — `POST /api/assistant/budget`
Creates budgets/goals from a natural-language request **immediately** (no approval
step), then returns a confirmation. Request mirrors `/assistant/chat`:
`{ messages: [{ role, content }, ...] }`. Gated on `OPENAI_API_KEY` (**503** when
unset, **502**/**503** on OpenAI errors — see *AI assistant (OpenAI)* — **400** on
empty messages) like the other AI endpoints.

Flow: the endpoint passes the KNOWN CATEGORIES (distinct `Transaction.category`)
and connected account labels (`app_account` from every `PlaidItem.accounts_json`)
to `ai.plan_budget(messages, known_categories, account_labels)`, which uses OpenAI
**structured output** (strict JSON schema via `client.chat.completions.parse`) to return:
```
{ actions: [ { kind: "goal", name, target_amount, target_date?, account? }
           | { kind: "category_limit", category, limit_amount } ],
  reply: str }
```
(For a trip/savings goal the model MAY also propose a few `category_limit` actions
that form a monthly savings plan.) Each action is then persisted via the shared
create helpers (goals capture `starting_balance` from the designated account),
and the endpoint returns:
```
{ reply: str,
  created: { goals: SavingsGoalOut[], category_limits: CategoryBudgetOut[] } }
```
When no budget intent is detected the model returns empty `actions` — nothing is
created and only `reply` is populated (plain-chat behavior preserved).

---

## Investments visualizations + account nicknames (v11)

Adds portfolio value-over-time tracking, investment cash-flow charting,
cost-basis/gain enrichment on holdings, and user-editable account nicknames. Two
NEW tables (`account_nicknames`, `portfolio_snapshots`) are created automatically
by `Base.metadata.create_all` on startup — no ALTER migration needed. All new
routers are gated by `require_user` like the other feature routers.

### New tables

**`account_nicknames`** — a user-supplied friendly name for a connected account.
`id` (PK), `label` (unique, not-null, indexed — the canonical account label /
`app_account` string stored on transactions + holdings), `nickname` (not-null),
`is_savings` (Bool, not-null, default `false` — v11b: when true the account is a
designated savings destination), `created_at`, `updated_at` (`onupdate`). Upsert
is keyed by `label`. (The `is_savings` column is created by `create_all` — the
table was new in v11 and not yet deployed, so no ALTER migration is needed.)

**`portfolio_snapshots`** — a point-in-time investment portfolio value. `id` (PK),
`captured_at` (DateTime, indexed, default `utcnow`), `account` (nullable, indexed —
**NULL means the grand total across all accounts**; a non-null value is that
account's label), `value` (Float, not-null), `currency` (nullable). At most one
row per `(account, calendar day)`.

### Account nicknames — `prefix=/api/accounts`

Coexists with the standalone `GET /api/accounts` endpoint (nicknames live under
`/api/accounts/nicknames`, so there is no path collision).

- `GET /api/accounts/nicknames` → `AccountNicknameOut[]`
  (`{ label, nickname, is_savings }`), ordered by label.
- `PUT /api/accounts/nicknames` — body `AccountNicknameIn` `{ label, nickname }`.
  Upsert by `label`; the nickname is **trimmed**. **If the nickname is blank /
  whitespace-only, the row is DELETED** (revert to the raw label) — **UNLESS the
  row is a designated savings account (`is_savings` true)**, in which case the row
  is kept with the nickname cleared so the savings flag is never wiped (v11b).
  Returns **200** with the FULL current `AccountNicknameOut[]` list (a blank-delete
  is reflected by that label's absence from the list).
- `PUT /api/accounts/savings` — body `SavingsToggleIn` `{ label, is_savings }`
  (v11b). Upserts the row's `is_savings` (creating a row with `nickname=""` if
  none exists). If the result is `is_savings` false **and** the nickname is blank,
  the row is deleted. Returns **200** with the FULL current `AccountNicknameOut[]`
  list. **Backfill:** after the toggle, best-effort (try/except, never raises; only
  when Plaid is configured) it re-maps that account's transaction history so
  existing rows pick up the new savings logic — for each `PlaidItem` whose
  `accounts_json` contains an account with `app_account == label` it resets the
  item cursor and re-runs that item's `/transactions/sync`. Because upserts match
  on `plaid_transaction_id` and overwrite `type`/`category`, the re-map is
  idempotent (no duplicates).
- `DELETE /api/accounts/nicknames/{label}` → **204**, idempotent (204 whether or
  not the label existed).

**Savings-contribution mapping rule (v11b):** at sync time the set of designated
savings labels is loaded ONCE (`SELECT label FROM account_nicknames WHERE
is_savings = true`) and `map_transaction(txn, label, is_savings_account=...)` is
called per row. When `is_savings_account` is true **and the row is an inflow into
that account** (PFC primary `TRANSFER_IN`, i.e. money moving IN), the row is
recategorized as a savings contribution: `type="expense"`, `category="Savings"`.
This mirrors how investment cash contributions are handled, so bank-savings
inflows now count in the savings rate (`savings = Σ expense in
Savings/Investment`). Interest INCOME rows and OUTFLOWS (`TRANSFER_OUT`) are left
untouched. `"Savings"` is added to the canonical category vocabulary.

### Holdings enrichment — `GET /api/plaid/holdings`

`HoldingOut` gains four optional fields (all `null` when Plaid omits the source
data):
- `cost_basis` — from the Plaid holding's `cost_basis`.
- `security_type` — from the joined security's `type`.
- `unrealized_gain` = `value − cost_basis` (only when both present).
- `unrealized_gain_pct` = `unrealized_gain / cost_basis * 100` (only when
  `cost_basis > 0`).

Existing fields (account, institution, security_name, ticker_symbol, quantity,
price, value, currency) are unchanged.

### Investments — `prefix=/api/investments`

- `POST /api/investments/snapshot` — capture NOW. Computes current holdings,
  aggregates value per account + a grand total, and upserts TODAY's rows: one per
  account **plus one total row** (`account=NULL`). At most one point per
  `(account, calendar day)` — re-recording the same day updates the value in
  place. Returns `{ captured: int, total_value: float }`. **503** when Plaid is
  unconfigured.
- `GET /api/investments/snapshots?account=&days=` → `SnapshotOut[]`
  (`{ captured_at, account, value, currency }`) ordered by `captured_at` asc.
  With **no** `account` param it returns the **TOTAL series** (`account IS NULL`);
  `?account=<label>` returns that account's series. `days` limits to the last N
  days. Returns `[]` when there are no snapshots.

A snapshot is also recorded **best-effort on every Plaid sync** — the recorder
`record_portfolio_snapshot(db)` is called at the end of `sync_items()` (which
backs both `POST /api/plaid/sync` and `POST /api/plaid/sync-all`, plus the
in-process scheduler) inside a try/except, so a snapshot failure never breaks a
sync.

### Investment flows — `GET /api/stats/investment-flows`

`?granularity=month&start_date=&end_date=` → `InvestmentFlowPoint[]`
(`{ period, contributions, dividends }`). `granularity` supports the same values
as `/api/stats/over-time` (`year` → `YYYY`, `month` → `YYYY-MM`, `week` →
`YYYY-Www`, `day` → `YYYY-MM-DD`). Grouped by period, ordered ascending, and
(mirroring `/over-time`) **only periods with data are returned — no zero-fill**.

**Definitions** (derived from `plaid_mapping.py`, where every investment
transaction is assigned `category="Investment"`):
- **`contributions`** = money directed INTO investments = `Σ amount` for rows
  where `category = "Investment"` AND `type = "expense"`. Per the mapping, cash
  contributions/deposits map to `expense` (they count toward savings); manual
  `Investment`/expense rows (e.g. an ETF purchase) are included too.
- **`dividends`** = investment income = `Σ amount` for rows where
  `category = "Investment"` AND `type = "income"` (dividend / interest cash rows).
- Portfolio buy/sell churn (mapped to `transfer`) is intentionally counted in
  neither series.

### Net worth — `GET /api/stats/net-worth`

→ `NetWorthOut` `{ net_worth, assets, liabilities, currency, as_of }`. Derived
from each linked `PlaidItem`'s `accounts_json` balances plus live holdings
(`compute_holdings(db)`).

- **assets** = Σ depository `current` (fallback `available`) **+** the total
  holdings value.
- **liabilities** = Σ `current` for credit / loan accounts.
- **net_worth** = `assets − liabilities`. `currency` defaults `"USD"` (first
  account currency seen otherwise). `as_of` = now.
- Returns **200 with zeros** (NOT 503) when Plaid is unconfigured or there are no
  linked items, so the UI tile can show a graceful empty state.

**No-double-count rule:** an investment/brokerage account's cash `current` balance
usually already equals the sum of that account's holdings, so we pick ONE source
per account type — depository accounts use their cash `current`
(fallback `available`); investment/brokerage accounts **ignore** their cash
`current` and are represented **only** by the holdings-derived total (added once,
in full); credit/loan `current` is a liability; any other type is ignored. The
holdings total is best-effort (a Plaid error yields `0` rather than failing the
endpoint).

---

## Real-time duplicate alerts (v12)

When a **new** duplicate expense is detected during any sync, the backend pushes a
push notification via [ntfy.sh](https://ntfy.sh). This works for all three sync
paths — Plaid webhooks, the manual "Sync now" button, and the cron/scheduler —
because every path routes through `sync_items()`, which now calls
`detect_and_notify_new_duplicates(db)` at the end (best-effort; a notify failure
never breaks a sync).

### Duplicate grouping is shared
`src/api/routes/duplicates.py::compute_duplicate_groups(rows)` is the single source
of truth for grouping expense rows into `DuplicateGroup`s by
`(date, amount@2dp, normalized description, normalized account)`. Both
`GET /api/duplicates` and the notifier call it, so alert grouping is identical to
the on-screen Duplicates view.

### No-spam mechanism (`dup_notified`)
- `Transaction.dup_notified` (`BOOLEAN NOT NULL DEFAULT FALSE`, mirrors
  `dup_dismissed`) records that a row's duplicate group has already been alerted.
- `detect_and_notify_new_duplicates(db)` queries non-dismissed expense rows, groups
  them, and for each group where **at least one** member has `dup_notified == False`
  it sends **exactly one** ntfy notification, then flips `dup_notified = True` on
  **every** member of that group and commits.
- Result: a standing duplicate is alerted **once**. A genuinely new duplicate charge
  (a fresh row that forms or joins a group) re-introduces an un-notified member, so
  it triggers a fresh alert on the next sync. A **dismissed** group is excluded from
  grouping entirely, so it never alerts.

### Retro-backfill (no first-deploy blast)
The migration `_migrate_add_dup_notified_column()` in `main.py` follows the existing
introspect → `ALTER TABLE ... ADD COLUMN` pattern. Because every pre-existing row
would otherwise have `dup_notified = FALSE` and fire retroactively on the first
post-deploy sync, the migration performs a **one-time backfill**: immediately after
adding the column it runs `UPDATE transactions SET dup_notified = TRUE`, marking all
rows that existed **at migration time** as already-notified. The backfill is guarded
on the column being absent beforehand, so it runs only on the deploy that first adds
the column — never again. Only rows created **after** the migration can trigger alerts.

### ntfy config
- `NTFY_TOPIC` — the ntfy topic to publish to. **Unset ⇒ notifications disabled**
  (`send_ntfy()` is a no-op returning `False`; no network call). Subscribe to this
  topic in the ntfy mobile app / web.
- `NTFY_SERVER` — optional self-hosted ntfy base URL (default `https://ntfy.sh`).
- `NTFY_TOKEN` — ntfy access token (`tk_...`). Unset ⇒ **anonymous** publishing
  (unchanged behavior). See below — on a shared-IP host this is effectively required.
- `APP_URL` — optional app base URL; used for the alert's `Click` header
  (deep-links to `{APP_URL}/dashboard`).

`send_ntfy(title, message, *, click, tags, priority)` POSTs `message` as the body to
`{NTFY_SERVER}/{NTFY_TOPIC}` with ntfy's `Title`/`Click`/`Tags`/`Priority` request
headers, using `urllib` only (no new dependency; mirrors the Clerk lookup in
`auth.py`, including a real `User-Agent`). It **never raises** — the actual HTTP send
is isolated in `notify._http_post` so tests can monkeypatch it.

### Authentication — why `NTFY_TOKEN` is needed on shared-IP hosts (HTTP 429)
ntfy.sh rate-limits **anonymous** publishing **per source IP**. Render's outbound IPs
are shared across many tenants, so anonymous sends from production returned
`HTTP Error 429: Too Many Requests` because of *other people's* traffic — nothing the
app itself could throttle its way out of. For **authenticated** requests ntfy applies
its limits **per-account**, so publishing with a token removes the shared-IP problem.

- Setup: free ntfy.sh account → **Account → Access tokens** → generate → set
  `NTFY_TOKEN` (`sync: false` in `render.yaml`).
- When set, `send_ntfy_verbose` adds `Authorization: Bearer <token>` to the request
  headers. When unset it behaves exactly as before (anonymous).
- **The token is a secret.** `notify._redact(reason, topic, token)` scrubs both the
  topic and the token out of the returned `error` string **and** out of
  `logger.warning` output (the log line is the redacted text, never the raw
  exception — a raw urllib error can echo the request URL or headers). The
  `Bearer {token}` form is always scrubbed; the bare token is scrubbed under the same
  `len >= 6` guard the topic uses, which no real `tk_`-prefixed token trips.
- A 429 gets an **actionable** hint appended to the error: with no token, that
  ntfy.sh meters anonymous publishing per IP and `NTFY_TOKEN` is the fix; with a
  token set, that the account's own quota is exhausted.

### Retry policy
`send_ntfy_verbose` retries **exactly once**, after `notify.RETRY_DELAY_SECONDS`
(2s), and only for **transient** statuses: **HTTP 429 or any 5xx**. Everything else
(403 bad token, DNS failure, timeout, `UnicodeEncodeError`) fails on the first
attempt — a retry would never fix it and would only double the delay. The status is
read from `HTTPError.code`, falling back to parsing urllib's `HTTP Error NNN` message
form. Worst-case added latency is therefore one 2s sleep: this runs inside the Plaid
sync path and must never stall it. The pause is isolated in `notify._sleep` so the
test suite patches it out and never actually waits. `send_ntfy` still **never
raises**.

### `POST /api/plaid/webhook`
Receives Plaid webhooks. **Always returns HTTP 200 quickly** (even on unknown
types / items / errors) so Plaid never retry-storms.

- **Auth:** exempt from Clerk in `auth.py` — Plaid can't send a session token. The
  exemption is scoped to **exactly** the path ending in `/plaid/webhook` (so
  `/plaid/webhook/register` stays authed). The gate is instead the payload's
  `item_id` lookup + (in production) signature verification.
- **Handled:** `webhook_type == "TRANSACTIONS"` with codes
  `SYNC_UPDATES_AVAILABLE`, `DEFAULT_UPDATE`, `INITIAL_UPDATE`, `HISTORICAL_UPDATE`.
  The payload's `item_id` (Plaid's item-id **string**) is matched against
  `PlaidItem.item_id`; on a hit we call `sync_items(db, item_id=<local id>)` (which
  syncs then detects+notifies). Unknown `item_id` ⇒ `{"status":"unknown_item"}`; any
  other webhook type/code ⇒ `{"status":"ignored"}`. All 200.
- **Verification (sandbox vs prod):** signature verification via the
  `Plaid-Verification` JWT header (fetch the ES256 key with
  `/webhook_verification_key/get`, verify the token, check the body SHA-256) is
  **only enforced when `PLAID_ENV == "production"`**. In sandbox/development it is
  **skipped** (logged). Tradeoff: the endpoint only triggers an idempotent sync + a
  de-duplicated notification, so a spoofed call at worst causes a redundant sync —
  acceptable for a personal single-user app.

### Startup auto-registration
On boot, `main.py::_maybe_register_plaid_webhook()` pushes `PLAID_WEBHOOK_URL` to
every already-linked item automatically — **no manual step**. It is **gated** (only
when Plaid is configured **and** `PLAID_WEBHOOK_URL` is set) and runs in a **daemon
thread** (mirrors `_maybe_start_plaid_scheduler` / `_maybe_start_keepalive`), so a
slow or failed Plaid round-trip never blocks or breaks boot; the outcome is logged
(`[plaid] webhook registered on N items`, or the failure). It reuses
`register_webhook_all(db)` — the same idempotent `/item/webhook/update` loop the
endpoint below uses — so running once per boot is safe.

### `POST /api/plaid/webhook/register` (authed)
Pushes `PLAID_WEBHOOK_URL` to **all already-linked items** via Plaid
`/item/webhook/update`, so items linked before the webhook was configured start
sending webhooks. Returns `{"updated": N}`. **503** if Plaid is unconfigured or
`PLAID_WEBHOOK_URL` is unset. Startup does this automatically (see above); this
endpoint remains for **on-demand re-registration**. New links get the webhook
automatically because `PLAID_WEBHOOK_URL` is set on the Link token in
`POST /api/plaid/link-token`.

### `POST /api/notify/test` (authed)
Sends a test ntfy message so you can verify your setup. Returns:

```json
{
  "sent": false,
  "topic_configured": true,
  "token_configured": false,
  "server": "https://ntfy.sh",
  "error": "HTTP Error 429: Too Many Requests — ntfy.sh rate-limits ANONYMOUS publishing per source IP, …"
}
```

`sent` is `false` when `NTFY_TOPIC` is unset or the send failed, and `error` says
which. `topic_configured` / `token_configured` are **booleans only** — the topic and
token values are secrets and are never echoed back; `token_configured` exists so you
can confirm `NTFY_TOKEN` reached the running process.

### Config keys
`NTFY_TOPIC`, `NTFY_SERVER` (optional), `NTFY_TOKEN`, `PLAID_WEBHOOK_URL`, `APP_URL`
— all declared `sync: false` in `render.yaml` and present (blank, commented) in
`.env.example`.

---

## Monthly spend recap (v12)

A cron on the **1st of each month** pushes an ntfy recap of the month that just
ended, reusing `notify.send_ntfy`. Uses the CURRENT categories (no food split).

### `POST /api/reports/monthly`
- **`month`** (optional, query **or** JSON body, `"YYYY-MM"`). **Default = the
  PREVIOUS calendar month** relative to today (UTC). Invalid format → **422**.
- Computes over that month's `[first_day, last_day]` with **/stats/summary
  semantics** (transfers excluded; net-expense = `Σ expense − Σ refund`):
  - `income` = Σ income; `spent` = Σ net expense; `net` = income − spent.
  - `saved` = Σ amount where `type='expense' AND category='Savings'`.
  - `invested` = Σ amount where `type='expense' AND category='Investment'`.
  - `savings` = Σ where `category ∈ {Savings, Investment}`;
    `savings_rate` = `savings / income` (0 when income ≤ 0).
  - `categories` = per-category **net expense**, EXCLUDING
    `{Savings, Investment, Transfer, Payments & Credits, Income}`, positive totals
    only, sorted desc, **top 6**.
- Sends one ntfy notification (Title `📊 {Month Year} recap`, `Tags: bar_chart`,
  `Click: {APP_URL}/dashboard` when `APP_URL` is set), then returns:
  `{month, income, spent, saved, invested, savings_rate, net, categories:[{category, amount}], sent}`.

**Recap message body** (currency rounded to whole dollars, thousands-separated;
the `(NN%)` next to Saved is `savings_rate`):

```
Income $5,000 · Spent $3,240
Saved $600 (12%) · Invested $500
Top spending:
• Food & Drink $520
• Shopping $410
• Bills & Utilities $900
```

### Auth (cron token)
Mirrors `POST /plaid/sync-all` **exactly**. `PLAID_SYNC_TOKEN` is the **general
cron token**: the `/reports/monthly` path is exempt from Clerk auth in `auth.py`
when the `X-Plaid-Sync-Token` header matches it, and the endpoint **also** checks
the token (**401** when `PLAID_SYNC_TOKEN` is set and the header mismatches). When
`PLAID_SYNC_TOKEN` is **unset**, the request falls through to normal Clerk auth (so
local dev + tests with auth disabled can call it).

### Cron
`.github/workflows/monthly-report.yml` — schedule `0 13 1 * *` (1st of month, 13:00
UTC) + `workflow_dispatch`. Warms `${BASE_URL}/api/health`, then POSTs
`${BASE_URL}/api/reports/monthly` with `X-Plaid-Sync-Token: ${{ secrets.PLAID_SYNC_TOKEN }}`.
`BASE_URL` = `vars.SYNC_BASE_URL` (default `https://expense-tracker-2kfq.onrender.com`).
Fails the run on any non-2xx.
