"""
test_exclude_filter.py — EXCLUDE filters on GET /api/transactions (v9).

`GET /api/transactions` accepts two comma-separated exclusion params:
  * `exclude_types`      — types to HIDE (income | expense | transfer | refund)
  * `exclude_categories` — category names to HIDE

Semantics locked down here:
  * A row is HIDDEN when its type is in `exclude_types` OR its category is in
    `exclude_categories`.
  * Both params AND with each other and with the pre-existing filters
    (date/type/category/account/accounts/needs_review).
  * Absent/blank param -> no exclusion. Parsing trims whitespace and drops
    empty tokens (mirrors the shared `parse_accounts` helper).

Everything runs against the per-test in-memory DB via the `client` fixture —
no network, no real DB.
"""
from __future__ import annotations


# ── Helpers ──────────────────────────────────────────────────────────────────

def _post(client, **kwargs) -> dict:
    defaults = {"source": "manual"}
    defaults.update(kwargs)
    resp = client.post("/api/transactions", json=defaults)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _seed_mixed(client):
    """Six rows spanning every type and a spread of categories/accounts.

    types:      income(1), expense(3), transfer(1), refund(1)  -> 6 rows
    categories: Salary, Dining, Investment, Transfers, Shopping, Groceries
    accounts:   Amex(2), Chase(2), Discover(2)
    """
    _post(client, date="2026-03-01", amount=5000.0, type="income",   category="Salary",     account="Amex")
    _post(client, date="2026-03-02", amount=80.0,   type="expense",  category="Dining",     account="Amex")
    _post(client, date="2026-03-03", amount=200.0,  type="expense",  category="Investment", account="Chase")
    _post(client, date="2026-03-04", amount=500.0,  type="transfer", category="Transfers",  account="Chase")
    _post(client, date="2026-03-05", amount=45.0,   type="refund",   category="Shopping",   account="Discover")
    _post(client, date="2026-03-06", amount=120.0,  type="expense",  category="Groceries",  account="Discover")


def _get(client, query: str = "") -> list[dict]:
    resp = client.get(f"/api/transactions{query}")
    assert resp.status_code == 200, resp.text
    return resp.json()


# ═══════════════════════════════════════════════════════════════════════════
# EXCLUDE filters on GET /api/transactions
# ═══════════════════════════════════════════════════════════════════════════

class TestExcludeFilters:

    def test_baseline_absent_returns_all(self, client):
        """No exclude params -> every seeded row is returned."""
        _seed_mixed(client)
        rows = _get(client)
        assert len(rows) == 6
        assert {r["type"] for r in rows} == {"income", "expense", "transfer", "refund"}

    def test_exclude_single_type(self, client):
        """exclude_types=transfer -> no transfer rows, all others present."""
        _seed_mixed(client)
        rows = _get(client, "?exclude_types=transfer")
        assert len(rows) == 5
        assert all(r["type"] != "transfer" for r in rows)
        # every non-transfer type still represented
        assert {r["type"] for r in rows} == {"income", "expense", "refund"}

    def test_exclude_multiple_types(self, client):
        """exclude_types=transfer,income -> neither type returned."""
        _seed_mixed(client)
        rows = _get(client, "?exclude_types=transfer,income")
        assert len(rows) == 4
        assert all(r["type"] not in {"transfer", "income"} for r in rows)
        assert {r["type"] for r in rows} == {"expense", "refund"}

    def test_exclude_single_category(self, client):
        """exclude_categories=Investment -> no Investment rows, others present."""
        _seed_mixed(client)
        rows = _get(client, "?exclude_categories=Investment")
        assert len(rows) == 5
        assert all(r["category"] != "Investment" for r in rows)
        assert "Investment" not in {r["category"] for r in rows}
        # the other expense categories survive
        assert {"Dining", "Groceries"} <= {r["category"] for r in rows}

    def test_exclude_multiple_categories(self, client):
        """exclude_categories accepts a comma list; both categories hidden."""
        _seed_mixed(client)
        rows = _get(client, "?exclude_categories=Investment,Shopping")
        assert len(rows) == 4
        assert {"Investment", "Shopping"}.isdisjoint({r["category"] for r in rows})

    def test_exclude_types_and_categories_compose(self, client):
        """Both params together: a row is hidden if its type is excluded OR its
        category is excluded (union of hidden rows)."""
        _seed_mixed(client)
        rows = _get(client, "?exclude_types=transfer&exclude_categories=Investment")
        # transfer row (Transfers/Chase) AND Investment row (Chase) both hidden -> 4 left
        assert len(rows) == 4
        assert all(r["type"] != "transfer" for r in rows)
        assert all(r["category"] != "Investment" for r in rows)
        assert {r["category"] for r in rows} == {"Salary", "Dining", "Shopping", "Groceries"}

    def test_exclude_type_and_category_disjoint_rows(self, client):
        """When the excluded type and excluded category sit on different rows,
        both distinct rows are removed."""
        _seed_mixed(client)
        rows = _get(client, "?exclude_types=income&exclude_categories=Dining")
        # income(Salary) row + Dining row -> 2 distinct rows removed -> 4 left
        assert len(rows) == 4
        assert all(r["type"] != "income" for r in rows)
        assert all(r["category"] != "Dining" for r in rows)

    def test_whitespace_and_empty_tokens_ignored(self, client):
        """`exclude_types=transfer, ,` behaves exactly like `exclude_types=transfer`."""
        _seed_mixed(client)
        messy = _get(client, "?exclude_types=%20transfer%20,%20,")  # " transfer , ,"
        clean = _get(client, "?exclude_types=transfer")
        assert {r["id"] for r in messy} == {r["id"] for r in clean}
        assert len(messy) == 5
        assert all(r["type"] != "transfer" for r in messy)

    def test_blank_param_is_no_exclusion(self, client):
        """An all-empty token param (e.g. `exclude_types=,,`) excludes nothing."""
        _seed_mixed(client)
        rows = _get(client, "?exclude_types=,,%20,")
        assert len(rows) == 6

    def test_exclude_composes_with_accounts_filter(self, client):
        """accounts + exclude_categories AND together.

        Chase holds two rows: expense/Investment and transfer/Transfers.
        Excluding Investment leaves only the Chase transfer row.
        """
        _seed_mixed(client)
        rows = _get(client, "?accounts=Chase&exclude_categories=Investment")
        assert len(rows) == 1
        assert rows[0]["account"] == "Chase"
        assert rows[0]["type"] == "transfer"
        assert rows[0]["category"] == "Transfers"

    def test_exclude_composes_with_legacy_account_filter(self, client):
        """Legacy single `account` param + exclude_types compose (AND)."""
        _seed_mixed(client)
        rows = _get(client, "?account=Chase&exclude_types=transfer")
        # Chase has Investment(expense) + Transfers(transfer); drop transfer -> 1 left
        assert len(rows) == 1
        assert rows[0]["account"] == "Chase"
        assert rows[0]["category"] == "Investment"

    def test_exclude_type_not_present_is_noop(self, client):
        """Excluding a type that no row has changes nothing."""
        _seed_mixed(client)
        rows = _get(client, "?exclude_types=refund")
        assert len(rows) == 5
        rows2 = _get(client, "?exclude_categories=DoesNotExist")
        assert len(rows2) == 6
