# Expense Tracker — QA Test Report (v5.4)

**Date:** 2026-07-02
**QA:** patwal.a@northeastern.edu
**Scope:** Backend API (`src.api.main:app`) tested against `docs/api-contract.md` (v1 + v2 transfer + v3 bank-agnostic CSV + v4 per-card/account + v5 rules / needs-review / AI batch + v5.1 brokerage + v5.2 import history + v5.3 bank/checking + v5.4 refund netting / P2P pass-through).
**Result:** **299 / 299 passing. 0 failures.** One minor schema-omission defect (D-2, below); no behavioral defects.

> NOTE (real DB): the shared `expense_tracker.db` is intentionally EMPTY (0 rows)
> — seeds were removed by user request (confirmed by coordinator); this is NOT a
> defect, and the earlier D-1 "19 seed rows" invariant is retired. My tests never
> touch the real DB anyway — verified by identical MD5 of the file before/after a
> full suite run (temp-file + in-memory isolation).

---

## 1. How the tests were run

API-level tests using FastAPI `TestClient` (no live uvicorn required) against
the real app object imported from `src.api.main`.

### Environment
- Reused backend virtualenv `.venv` (Python 3.11, `pytest` 9.1.1 + `httpx`).
- From project root:

```bash
cd "/Users/adityapatwal/Documents/projects/expense tracker"
source ".venv/bin/activate"
python -m pytest tests/ -v
```

### Database isolation (real DB never touched)
`tests/conftest.py` provides two layers:
1. Before importing the app, `DATABASE_URL` is pointed at a throwaway temp-file
   SQLite DB so the app's startup table-create + 19-row seed land there.
2. Each test gets a fresh in-memory SQLite engine (`sqlite://` + `StaticPool`)
   and the `get_db` dependency is overridden to it — every test starts empty.

**Verified after this run:** real `expense_tracker.db` still has exactly
**19 rows, all `source=manual`** — no test data leaked in.

### Test files
| File | Area |
|------|------|
| `tests/conftest.py` | Fixtures + CSV/fixture helpers |
| `tests/test_transactions.py` | CRUD, filters, pagination, validation, health, transfer accepted |
| `tests/test_csv.py` | CSV import incl. v3 bank-agnostic (Discover fixture, multi-bank, fail-fast) |
| `tests/test_stats.py` | summary / by-category / over-time / categories |
| `tests/test_transfers.py` | **NEW** — v2 transfer type: ledger visible, excluded from all stats |
| `tests/test_integration.py` | End-to-end realistic flow |
| `tests/fixtures/discover_sample.csv` | Real 15-row Discover export (reference fixture) |

---

## 2. Totals

| File | Tests | Passed | Failed |
|------|------:|-------:|-------:|
| `test_transactions.py` | 43 | 43 | 0 |
| `test_csv.py` | 45 | 45 | 0 |
| `test_stats.py` | 59 | 59 | 0 |
| `test_transfers.py` | 18 | 18 | 0 |
| `test_accounts.py` (v4) | 26 | 26 | 0 |
| `test_rules.py` (v5) | 29 | 29 | 0 |
| `test_assistant.py` (v5) | 14 | 14 | 0 |
| `test_brokerage.py` (v5.1) | 8 | 8 | 0 |
| `test_imports.py` (v5.2) | 19 | 19 | 0 |
| `test_bank_import.py` (v5.3) | 20 | 20 | 0 |
| `test_refunds.py` (v5.4) | 16 | 16 | 0 |
| `test_integration.py` | 2 | 2 | 0 |
| **Total** | **299** | **299** | **0** |

`test_assistant.py` was originally added by another teammate; this round I
extended it with the v5 `categorize-batch` coverage (disabled + mocked-available
cases).

Runtime: ~1.2s for the full suite.

---

## 3. Pass/fail by group (all PASS)

### `test_transfers.py` (18) — NEW, v2 transfer exclusion
- `TestTransferLedgerVisibility` (4): POST transfer -> 201 type=transfer; appears
  in GET /transactions (unfiltered); GET ?type=transfer returns transfers;
  retrievable by id. — PASS
- `TestSummaryExcludesTransfers` (7): total_income / total_expense / net /
  savings / savings_rate / **count** all exclude transfers; transfer-only DB
  yields an all-zero summary with count=0. — PASS
- `TestByCategoryExcludesTransfers` (4): transfer categories absent from the
  breakdown; expense totals unaffected; pct still sums ~100; `?type=transfer`
  returns `[]`. — PASS
- `TestOverTimeExcludesTransfers` (3): per-period amounts exclude transfers;
  a transfer-only month is absent entirely; per-period sums equal summary. — PASS

### `test_csv.py` (37) — incl. v3 bank-agnostic
- `TestDiscoverFixtureImport` (5): **imported:15, skipped:0, transfers:2,
  errors:[]**; exactly 2 transfers stored (INTERNET PAYMENT) + 13 expenses;
  MM/DD/YYYY -> ISO; by-category has Restaurants/Merchandise but NOT
  "Payments and Credits"; payments excluded from expense total
  (expected expense 297.50, and they never appear as income). — PASS
- `TestBankFormatVariants` (4): `Trans. Date` alias + single signed amount
  (positive->expense, negative->transfer); split Debit/Credit
  (debit->expense, credit->income); bank Type column DEBIT/CREDIT/PAYMENT ->
  expense/income/transfer; recognized Type value wins over sign. — PASS
- `TestFailFastMissingColumns` (2): no date column -> 400 naming 'date' +
  headers seen; no amount/debit/credit -> 400 naming 'amount' + headers seen;
  nothing imported in either case. — PASS
- Plus existing template / valid-import / error-handling groups (26). — PASS

### `test_transactions.py` (43)
- CRUD happy paths, 404s, 422 validation, list filters, pagination, health.
- New: `test_create_transfer` (transfer accepted as a valid type). — PASS

### `test_stats.py` (59) and `test_integration.py` (2) — PASS.
- Includes `TestStatsOverTimeYear` (7, added when the backend gained
  `granularity=year`): asserts `granularity=year` returns 200 (no longer 422),
  period labels match `^\d{4}$`, both years present and ascending, per-year
  income/expense/net/savings correct for a multi-year (2024+2025) seed,
  transfers still excluded (per-year + cross-checked against the summary), a
  transfer-only year is absent, and the response schema. month/week/day
  coverage retained as-is.

### `test_accounts.py` (26) — NEW, v4 per-card / account metrics
- `TestAccountField` (5): `account` round-trips on TransactionOut; optional/null
  allowed; PUT updates it; `GET ?account=X` filters. — PASS
- `TestCSVAccountTagging` (4): importing `discover_sample.csv` with form field
  `account=Discover` (15 rows) and `chase_sample.csv` with `account=Chase`
  (157 rows) tags every imported row with the right card; no-account import
  leaves account null; both fixtures keep distinct accounts. — PASS
- `TestByAccount` (7): `GET /api/stats/by-account` returns Chase + Discover,
  **descending by expense** (Chase 2014.72 > Discover 296.90), correct per-card
  income/expense/net/count, transfers excluded (Discover count 13 not 15; Chase
  stats count 144 = 133 expense + 11 refund), null/empty → "Unassigned", empty
  DB → []. — PASS
- `TestAccountsList` (4): `GET /api/accounts` returns distinct non-empty
  accounts `["Chase","Discover"]`, excludes null/empty, list-of-strings. — PASS
- `TestStatsAccountFilter` (6): `summary?account=Chase|Discover` and
  `by-category?account=…` restrict correctly; no-account aggregates both; the
  per-card split sums to the unfiltered total (Chase 2014.72 + Discover 296.90
  = 2311.62; counts 144 + 13 = 157). — PASS

Per-card ground truth (verified against the live backend + shipped fixtures,
**v5.4**): Discover → 13 expenses = $296.90, 2 transfers; Chase → net expense
$2014.72 (133 expense − 11 refund), 13 transfers (payments), stats count 144;
combined net expense $2311.62, count 157. Both cards have $0 income.
(v5.4 change: Chase `Return` rows are now `refund` (net against spend, counted),
not transfer — so Chase expense dropped 2302.07 → 2014.72, transfers 24 → 13,
and stats count 133 → 144.)

### v5 — rules engine, needs-review, AI batch

`test_rules.py` (29) — user rules engine:
- **CRUD + validation** (16): create (with defaults priority=100/enabled=true),
  get/get-404, list ordered by priority asc, `?enabled=` filter, full + partial
  PUT (partial keeps untouched fields), PUT-404, delete/delete-404, and 422 for
  empty `match_value`, bad `match_field`/`match_op`/`set_type`. — PASS
- **apply ordering** (3): first match by priority (lower wins); disabled rule
  skipped; priority tie → lower id wins. — PASS
- **override during CSV import** (3): a `description contains ROBINHOOD →
  set_type=transfer` rule turns a positive-amount row (which built-in inference
  would call expense) into a transfer; a matching rule suppresses needs_review
  on import; a set_category-only rule overrides category but keeps inferred
  type. — PASS
- **`/rules/apply` + `/rules/preview`** (7): apply returns `{updated:N}`,
  reclassifies matched rows, no-rules → 0, `account` scope, `only_review` scope,
  clears `needs_review` when a rule matches; preview returns `{matches:N}`, is
  read-only (no mutation, no rule persisted), and honors amount range. — PASS

`test_csv.py::TestNeedsReviewImport` (8) — uses `debit_sample.csv`:
- import response includes a `needs_review` int (imported 14, **needs_review 7**
  in v5.1 = 6 ambiguous-token rows + 1 brokerage); the 6 ambiguous rows (Venmo×2,
  Zelle, ATM, cash deposit, check) are flagged with a token `review_reason`; the
  ROBINHOOD brokerage row is flagged with a distinct `Brokerage:` reason and
  defaulted to transfer; the unambiguous rows (paycheck, utilities, groceries,
  rent, comcast) are NOT flagged; `?needs_review=true` returns exactly the 7
  flagged rows and `?needs_review=false` the other 7; 0 skipped / 0 errors. — PASS

`test_assistant.py::TestCategorizeBatch*` (5) — `POST /assistant/categorize-batch`:
- 503 when `GEMINI_API_KEY` unset; with AI mocked available (no key/network),
  response shape is `{results:[{id,category,confidence}]}`; a confident
  suggestion (≥0.6) updates category + clears `needs_review`; a low-confidence
  suggestion is returned but does NOT mutate; `ids` targets specific rows. — PASS

### v5.1 — brokerage savings-vs-transfer prompt

`test_brokerage.py` (8) — the ROBINHOOD deposit ($500) in `debit_sample.csv`:
- **default** (no rules): row is `type=transfer`, `needs_review=true`,
  `review_reason` starts with `Brokerage:` and names the token; excluded from
  savings (savings 0). — PASS
- **"savings" choice** (rule `→ set_type=expense, set_category=Investment` +
  `/rules/apply`): row becomes expense/Investment, `needs_review` clears, and
  `summary.savings` rises by exactly $500 with `savings_rate = savings/income`
  increasing accordingly (0 → 0.1399); Investment appears in by-category. — PASS
- **"transfer" choice** (rule `→ set_type=transfer`): stays transfer,
  `needs_review` clears, remains excluded from savings and by-category. — PASS
- **rule overrides re-import**: with a matching rule present, re-importing the
  file does NOT re-flag ROBINHOOD (needs_review total 7 → 6); a rule present at
  import time classifies it directly with no review. — PASS

**Reference fixture `tests/fixtures/debit_sample.csv`** (14-row synthetic
CHECKING statement, Date/Description/Category/Type/Amount): paycheck (income),
credit-card payment, ROBINHOOD brokerage deposit, Venmo×2, Zelle, ATM
withdrawal, cash deposit, utilities×2, check, groceries, gas, rent. Serves as
the debit-handling + needs-review + brokerage regression guard (imports as 14
rows / 7 needs_review under v5.1).

### v5.2 — import history + reassign / undo

`test_imports.py` (19):
- **batch tagging** (4): the CSV import response carries an int `batch_id`;
  two imports get distinct ids; every imported row belongs to the batch
  (verified behaviorally — reassign touches exactly the N imported rows, since
  `batch_id` is not exposed on TransactionOut — see D-2); a manual POST creates
  no batch. — PASS
- **`GET /api/imports`** (5): empty when nothing imported; lists batches
  newest-first (Chase after Discover → Chase first); correct `filename`,
  `account` (Discover="Discover", Chase=null when imported without one), and
  counts (imported/skipped/transfers/needs_review) matching the import
  response. — PASS
- **`POST /imports/{id}/reassign`** (6): returns `{updated:N}` = batch size;
  sets the batch account AND every transaction (verified via
  `?account=Chase` and by-account moving Unassigned→Chase); null/empty/blank →
  Unassigned; only affects the target batch; 404 on unknown id. — PASS
- **`DELETE /imports/{id}`** (4): 204; removes the batch and all its
  transactions (total count drops by the batch size; batch disappears from
  `GET /imports`); leaves other batches and manual (null-batch) rows intact;
  404 on unknown id. — PASS

Verified batch counts against the fixtures: Discover batch → imported 15,
transfers 2, needs_review 0; Chase batch → imported 157, transfers 13,
needs_review 13 (v5.4: Chase transfers 24 → 13 as 11 `Return` rows are now
refunds). Batch metadata is read from the import response, so these tests
self-track the current backend numbers.

### v5.3 — bank/checking statement support

`test_bank_import.py` (20) — uses `bofa_checking_sample.csv`:
- **preamble skip** (3): the BofA summary block (`Description,,Summary Amt.` +
  Beginning/Total/Ending balance rows + blank line) is skipped and the real
  header (`Date,Description,Amount,Running Bal.`) is used — import succeeds
  (no 400, 12 imported); no summary rows become transactions; a generic
  inline preamble is likewise skipped. — PASS
- **`statement_type=bank` classification** (7): a `+` PAYROLL deposit imports
  as **income** (the v5.3 paycheck fix), not expense; `−` utilities/groceries/
  gas → expense; bank-side card payments (`DISCOVER DES:E-PAYMENT`,
  `CHASE CREDIT CRD DES:EPAY`) → transfer (not counted as spend); `SCHWAB` →
  transfer with a `Brokerage:` review; Venmo/Zelle → transfer + review (v5.4
  P2P pass-through); card payments + P2P excluded from spend (expense total
  $458.44, income $3300.00 under v5.4); transfers count = 5 (2 card payments +
  1 brokerage + Venmo + Zelle). — PASS
- **`statement_type=card` unchanged** (5): default + explicit `card` keep the
  Discover (15/2) and Chase (157/**13** transfers, v5.4) fixtures identical; a positive single
  amount is expense under `card` but income under `bank` (the sign flip). — PASS
- **error cases survive the scanner** (3): date-but-no-amount → specific
  `amount` 400; neither → `date` 400; an all-preamble file (no real header)
  fails as before; nothing imported in each. — PASS
- **batch records statement_type** (2): `GET /api/imports` shows
  `statement_type='bank'` for the BofA import and `'card'` for a default
  import. — PASS

**Reference fixture `tests/fixtures/bofa_checking_sample.csv`** — anonymized
synthetic BofA-style checking export: a summary preamble block + blank line +
the real `Date,Description,Amount,Running Bal.` header + 12 rows (PAYROLL×2,
EVERSOURCE/COMCAST/NATIONAL GRID utilities, DISCOVER E-PAYMENT, CHASE CREDIT
CRD, SCHWAB brokerage, VENMO cashout, Zelle, grocery, gas). Uses fake names
("JOHN DOE"). Preamble + bank-sign + card-payment-detection regression guard.

### v5.4 — refund netting + P2P pass-through

**Spec change (not a bug) — updated Chase numbers.** v5.4 makes Chase `Return`
rows `refund` (net against category spend, included in count) instead of
transfer. The affected assertions in `test_accounts.py`, `test_bank_import.py`,
and `test_imports.py` were updated to the new live-verified values:
Chase expense **2302.07 → 2014.72**, combined expense **2598.97 → 2311.62**,
Chase stats count **133 → 144** (133 expense + 11 refund), Chase transfers
**24 → 13**; BofA bank-mode expense **578.44 → 458.44** / income **3620 → 3300**
and transfers **3 → 5** (Venmo + Zelle now pass-through transfers).

`test_refunds.py` (16):
- **refund netting** (8): `refund` is a valid type; expense $100 + refund $30
  (same cat) → by-category total 70, count 2; `summary.total_expense` reflects
  −refund with the refund included in `count`; income/savings/savings_rate
  unaffected; `net = income − (expense − refund)`; over-time and by-account
  expense both net refunds; a category can net negative. — PASS
- **CSV refund mapping** (3): Type-column `Return`/`Refund`/`Reversal` →
  `refund` (not transfer); the Chase fixture yields **11 refunds**; a labeled
  APPLE.COM refund nets against its category. — PASS
- **P2P pass-through** (5): Venmo/Zelle/Cash App default to `transfer` +
  `needs_review` with a `review_reason` starting "Assumed pass-through
  transfer"; excluded from income AND expense (count 0); an explicit Type
  column value (`SALE→expense`) and a matching user rule both OVERRIDE the
  pass-through default (and a rule clears review). — PASS

---

## 4. Multi-bank CSV import results

| Format / fixture | Header style | Amount handling | Result |
|------------------|--------------|-----------------|--------|
| Discover (real fixture) | `Trans. Date, Post Date, Description, Amount, Category` | single signed; +=expense, −=payment/transfer | 15 imported, 0 skipped, **2 transfers**, 13 expenses; payments excluded from stats |
| Generic credit card | `Trans. Date, Description, Amount` | single signed | +4.50 -> expense; −12.00 -> transfer (refund) |
| Capital One style | `Transaction Date, Description, Debit, Credit` | split columns | debit -> expense; credit -> income |
| Bank Type column | `Date, Description, Amount, Type` | Type value DEBIT/CREDIT/PAYMENT | -> expense / income / transfer; value beats sign |
| Missing date | `Memo, Amount` | n/a | **400** fail-fast, names 'date' + lists headers |
| Missing amount | `Date, Description` | n/a | **400** fail-fast, names 'amount' + lists headers |

The reference Discover result matches the contract exactly (imported:15,
skipped:0, transfers:2; the two −payment rows do not appear in any spending
stat).

---

## 5. Defects found

**No API-behavior defects.** v2–v5.4 endpoints all behave to spec. v5.4 verified:
`refund` is a 4th type that nets against category spend (total_expense = Σexpense
− Σrefund; refunds in `count`; income/savings unaffected) across summary,
by-category, over-time, and by-account; CSV `Return`/`Refund`/`Reversal` map to
`refund` (Chase → 11 refunds); P2P (Venmo/Zelle/Cash App) default to `transfer`
+ needs_review ("Assumed pass-through transfer"), excluded from income/expense,
overridable by an explicit Type column value or a user rule. The v5.4 number
changes (Chase 2302.07→2014.72, transfers 24→13, count 133→144; combined
2598.97→2311.62; BofA 578.44→458.44) are a deliberate spec change and were
re-verified against the live backend. One pre-existing minor schema omission
remains open — D-2.

### D-2 (minor, open) — `batch_id` not exposed on `TransactionOut`
The v5.2 data model adds `batch_id` to `transactions`, and the contract says
`TransactionOut` returns "all columns", but `TransactionOut` (schemas.py) only
extends `TransactionCreate` with `id` + `created_at` — it does not declare
`batch_id`, so `GET /api/transactions` responses omit it entirely. Batch
membership is therefore not observable per-row via the transactions API (only
via the import/reassign/delete behavior). **Impact:** low — a UI that wants to
show "which import a row came from" or group rows by batch can't read it from
`TransactionOut` today. **Suggested fix (backend owner):** add
`batch_id: Optional[int] = None` to `TransactionOut`. My import tests verify
batch tagging behaviorally instead (reassign/delete cascades) so the suite is
green regardless. Reported to `main`.

### D-1 (RETIRED) — empty real `expense_tracker.db` is intentional
The previously-noted empty real DB is intentional: seeds were removed by user
request (confirmed by the coordinator). Not a defect; the "19 seed rows"
invariant no longer applies. My tests never touch the real DB regardless
(verified by identical pre/post-run MD5).

### Observation O-3 — `by-account.count` counts all non-transfer rows
`count` in `by-account` (and `summary.count`) is the number of non-transfer
rows for the account — i.e. income + expense rows, not expense-only. For the
fixtures both cards have 0 income, so count == expense-row count (Discover 13,
Chase 133). This matches the contract's `count` field (no "expense-only"
qualifier). Informational, not a defect.

### Test-side updates applied this round (not backend bugs)
Three previously-passing tests were stale against the new contract and were
corrected on the test side (the only files I own):

| # | Test | Old assumption | v2/v3 reality | Fix |
|---|------|----------------|---------------|-----|
| U-1 | `test_create_invalid_type` | `transfer` was an invalid type (422) | `transfer` is now valid | Split into `test_create_transfer` (201) + invalid-type test using a truly bad value (`withdrawal`) |
| U-2 | CSV `type=transfer` row | expected inferred `expense` | recognized direction value passes through -> `transfer` | Renamed to `test_unrecognized_type_value_falls_back_to_sign` (uses `frobnicate`) + added `test_explicit_transfer_type_value_passes_through` |
| U-3 | `test_missing_required_column_category` | category required -> 400 | v3 made category OPTIONAL (defaults `Uncategorized`) | Renamed to `test_missing_category_column_defaults_uncategorized` (200, category=Uncategorized) |

### Observations (low severity, unchanged from prior report; no action required)
- **O-1 — Query-param `type` not enum-validated** on GET /transactions and
  /stats/by-category (`Optional[str]`); unknown values match nothing and return
  `200 []` rather than 422. Acceptable for a filter; contract requires 422 only
  for request-body validation. No contract violation.
- **O-2 — Deprecation warning (cosmetic):** `main.py` uses
  `@app.on_event("startup")`, deprecated in favor of FastAPI lifespan handlers.
  Functions correctly.

### Note on the `transfers` count semantics (informational, not a defect)
The response `transfers` count increments only for rows the importer
**auto-classifies** as transfer (payment tokens / negative-amount refunds), not
for rows transferred via an explicit recognized `type` column value. This
matches the contract's intent ("how many imported rows were classified as
transfer ... excluded from spending") and the Discover fixture (transfers:2).
Tests assert behavior consistent with this: the bank `Type=PAYMENT` row is
stored as `transfer` (verified via GET ?type=transfer) and is excluded from
stats; the `transfers` counter specifically tracks auto-classified rows.

---

## 6. Reproduce

```bash
cd "/Users/adityapatwal/Documents/projects/expense tracker"
source ".venv/bin/activate"
python -m pytest tests/ -v                 # full suite
python -m pytest tests/test_transfers.py -v
python -m pytest tests/test_csv.py::TestDiscoverFixtureImport -v
```
