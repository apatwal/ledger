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
- `POST /link-token` → `{ link_token, expiration }`. Creates a Link token (products/country codes from env, `client_user_id="local-user"`, optional redirect_uri). **503** if unconfigured.
- `POST /exchange` body `{ public_token }` → `PlaidItemOut`. Exchanges for an access_token, fetches accounts + best-effort institution name, stores/updates a `PlaidItem` (account label = `"{name} ••{mask}"`). **503** if unconfigured.
- `POST /sync` body `{ item_id?: int|null }` → `PlaidSyncResult { items_synced, added, modified, removed }`. Syncs one item, or ALL items when `item_id` is null. **503** if unconfigured; **404** if a given `item_id` doesn't exist.
- `POST /sync-all` (header `X-Plaid-Sync-Token` required only when `PLAID_SYNC_TOKEN` set) → `PlaidSyncResult`. Cron/scheduler friendly; **401** on token mismatch, **503** if unconfigured.
- `GET /items` → `PlaidItemOut[]`. **Works without keys.**
- `DELETE /items/{id}` → `204`. Best-effort Plaid `item/remove`, then deletes the `PlaidItem` row. **Keeps the imported transactions** — just nulls their `plaid_item_id`. **404** if not found.

`PlaidItemOut { id, item_id, institution_id?, institution_name?, accounts: PlaidAccount[], status, last_synced_at?, created_at }`; `PlaidAccount { account_id, name?, mask?, type?, subtype?, app_account? }`. **No `access_token` field anywhere.**

### Idempotent sync
`POST /sync` loops `/transactions/sync` with the item's stored `cursor` until
`has_more` is false, then **UPSERTs by `plaid_transaction_id`**: `added` → insert
(if new), `modified` → update, `removed` → delete. It also pulls
`/investments/transactions/get` over a rolling ~730-day window and upserts those
by `investment_transaction_id`. The new cursor + `last_synced_at` are persisted,
so re-running sync never duplicates rows (safe to call repeatedly / on a
schedule). Investments are best-effort — an item without the investments product
is skipped, not failed.

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
`{ messages: [{ role, content }, ...] }`. Gated on `GEMINI_API_KEY` (**503** when
unset, **502** on provider error, **400** on empty messages) like the other AI
endpoints.

Flow: the endpoint passes the KNOWN CATEGORIES (distinct `Transaction.category`)
and connected account labels (`app_account` from every `PlaidItem.accounts_json`)
to `ai.plan_budget(messages, known_categories, account_labels)`, which uses Gemini
**structured output** (`response_schema`) to return:
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
