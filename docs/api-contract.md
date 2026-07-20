# Expense Tracker — API Contract & Data Model (v7)

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
- `POST /api/assistant/categorize-batch` → body `{ids?: int[], only_uncategorized?: bool, account?, start_date?, end_date?}` → for each target txn, reuse `ai.suggest_category(...)`; update `category` and clear `needs_review` when confident → `{ results: [{id, category, confidence}] }`. Gated by `GEMINI_API_KEY` (503 when unset), like other AI endpoints.

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

## CORS
- Allow origin `http://localhost:3000` (and `*` is fine for dev).

## Health
- `GET /api/health` → `{ "status": "ok" }`
