"""
test_account_filter.py — multi-account filtering (v9).

The shared helper `src.api.account_filter` backs the `accounts` (comma-separated)
query param on the list / stats / duplicates endpoints, alongside the legacy
single `account` param. Semantics locked down here:

  * `accounts` (>=1 non-empty token) WINS over the single `account` param.
  * absent/blank `accounts` AND absent `account` -> ALL accounts (no filter).
  * `GET /api/transactions`, `/api/stats/summary`, `/api/stats/by-category`,
    `/api/stats/over-time`, and `/api/duplicates` all filter identically.

Everything runs against the per-test in-memory DB via the `client` fixture — no
network, no real DB.
"""
from __future__ import annotations

import pytest

from src.api.account_filter import parse_accounts, account_filter_condition


# ── Helpers ──────────────────────────────────────────────────────────────────

def _post(client, **kwargs) -> dict:
    defaults = {"source": "manual"}
    defaults.update(kwargs)
    resp = client.post("/api/transactions", json=defaults)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _seed_three_accounts(client):
    """Amex: income 1000 + expense 100; Chase: expense 200; Discover: expense 400."""
    _post(client, date="2026-01-05", amount=1000.0, type="income",  category="Salary",    account="Amex")
    _post(client, date="2026-01-10", amount=100.0,  type="expense", category="Dining",    account="Amex")
    _post(client, date="2026-01-12", amount=200.0,  type="expense", category="Groceries", account="Chase")
    _post(client, date="2026-01-15", amount=400.0,  type="expense", category="Travel",    account="Discover")


# ═══════════════════════════════════════════════════════════════════════════
# 1. Pure helper — parse_accounts / account_filter_condition
# ═══════════════════════════════════════════════════════════════════════════

class TestParseAccounts:

    def test_none_is_none(self):
        assert parse_accounts(None) is None

    def test_blank_is_none(self):
        assert parse_accounts("") is None
        assert parse_accounts("   ") is None
        assert parse_accounts(",, ,") is None

    def test_trims_and_drops_empty_tokens(self):
        assert parse_accounts(" A , B ,,C") == ["A", "B", "C"]

    def test_single_token(self):
        assert parse_accounts("Amex") == ["Amex"]


class TestAccountFilterCondition:

    def test_all_when_nothing_given(self):
        assert account_filter_condition(None, None) is None

    def test_accounts_wins_over_account(self):
        """When both are given, the returned condition is an IN over `accounts`
        (a multi-value clause), NOT the single-account equality."""
        cond = account_filter_condition("Discover", "Amex,Chase")
        # An IN clause compiles to '... IN (...)'; equality does not.
        assert "IN" in str(cond).upper()

    def test_single_account_used_when_no_accounts(self):
        cond = account_filter_condition("Amex", None)
        assert cond is not None
        assert "IN" not in str(cond).upper()  # equality, not an IN clause


# ═══════════════════════════════════════════════════════════════════════════
# 2. GET /api/transactions
# ═══════════════════════════════════════════════════════════════════════════

class TestTransactionsAccountFilter:

    def test_accounts_returns_only_listed(self, client):
        _seed_three_accounts(client)
        rows = client.get("/api/transactions?accounts=Amex,Chase").json()
        assert {r["account"] for r in rows} == {"Amex", "Chase"}
        assert len(rows) == 3  # Amex income + Amex expense + Chase expense

    def test_single_account_subset(self, client):
        _seed_three_accounts(client)
        rows = client.get("/api/transactions?accounts=Amex").json()
        assert {r["account"] for r in rows} == {"Amex"}
        assert len(rows) == 2

    def test_legacy_single_account_still_works(self, client):
        _seed_three_accounts(client)
        rows = client.get("/api/transactions?account=Discover").json()
        assert {r["account"] for r in rows} == {"Discover"}
        assert len(rows) == 1

    def test_accounts_wins_when_both_given(self, client):
        _seed_three_accounts(client)
        rows = client.get("/api/transactions?account=Discover&accounts=Amex,Chase").json()
        # accounts wins -> Discover must be absent
        assert {r["account"] for r in rows} == {"Amex", "Chase"}
        assert all(r["account"] != "Discover" for r in rows)

    def test_absent_returns_all(self, client):
        _seed_three_accounts(client)
        rows = client.get("/api/transactions").json()
        assert {r["account"] for r in rows} == {"Amex", "Chase", "Discover"}
        assert len(rows) == 4


# ═══════════════════════════════════════════════════════════════════════════
# 3. GET /api/stats/summary
# ═══════════════════════════════════════════════════════════════════════════

class TestSummaryAccountFilter:

    def test_accounts_scopes_totals(self, client):
        _seed_three_accounts(client)
        s = client.get("/api/stats/summary?accounts=Amex,Chase").json()
        assert s["total_income"] == pytest.approx(1000.0)   # Amex salary
        assert s["total_expense"] == pytest.approx(300.0)    # 100 + 200 (no Discover 400)
        assert s["count"] == 3

    def test_single_account(self, client):
        _seed_three_accounts(client)
        s = client.get("/api/stats/summary?accounts=Discover").json()
        assert s["total_expense"] == pytest.approx(400.0)
        assert s["total_income"] == pytest.approx(0.0)

    def test_legacy_account(self, client):
        _seed_three_accounts(client)
        s = client.get("/api/stats/summary?account=Chase").json()
        assert s["total_expense"] == pytest.approx(200.0)

    def test_accounts_wins_when_both(self, client):
        _seed_three_accounts(client)
        s = client.get("/api/stats/summary?account=Discover&accounts=Amex,Chase").json()
        assert s["total_expense"] == pytest.approx(300.0)   # Discover 400 excluded

    def test_absent_all(self, client):
        _seed_three_accounts(client)
        s = client.get("/api/stats/summary").json()
        assert s["total_expense"] == pytest.approx(700.0)   # 100 + 200 + 400


# ═══════════════════════════════════════════════════════════════════════════
# 4. GET /api/stats/by-category
# ═══════════════════════════════════════════════════════════════════════════

class TestByCategoryAccountFilter:

    def test_accounts_scopes_categories(self, client):
        _seed_three_accounts(client)
        items = client.get("/api/stats/by-category?accounts=Amex,Chase").json()
        cats = {i["category"] for i in items}
        assert cats == {"Dining", "Groceries"}   # Travel (Discover) excluded
        assert "Travel" not in cats

    def test_legacy_account(self, client):
        _seed_three_accounts(client)
        items = client.get("/api/stats/by-category?account=Discover").json()
        assert {i["category"] for i in items} == {"Travel"}

    def test_accounts_wins_when_both(self, client):
        _seed_three_accounts(client)
        items = client.get("/api/stats/by-category?account=Discover&accounts=Amex").json()
        assert {i["category"] for i in items} == {"Dining"}


# ═══════════════════════════════════════════════════════════════════════════
# 5. GET /api/stats/over-time
# ═══════════════════════════════════════════════════════════════════════════

class TestOverTimeAccountFilter:

    def test_accounts_scopes_expense(self, client):
        _seed_three_accounts(client)
        items = client.get("/api/stats/over-time?accounts=Amex,Chase").json()
        by_period = {i["period"]: i for i in items}
        assert by_period["2026-01"]["income"] == pytest.approx(1000.0)
        assert by_period["2026-01"]["expense"] == pytest.approx(300.0)

    def test_single_account(self, client):
        _seed_three_accounts(client)
        items = client.get("/api/stats/over-time?accounts=Discover").json()
        by_period = {i["period"]: i for i in items}
        assert by_period["2026-01"]["expense"] == pytest.approx(400.0)

    def test_accounts_wins_when_both(self, client):
        _seed_three_accounts(client)
        items = client.get("/api/stats/over-time?account=Discover&accounts=Amex,Chase").json()
        by_period = {i["period"]: i for i in items}
        assert by_period["2026-01"]["expense"] == pytest.approx(300.0)

    def test_absent_all(self, client):
        _seed_three_accounts(client)
        items = client.get("/api/stats/over-time").json()
        by_period = {i["period"]: i for i in items}
        assert by_period["2026-01"]["expense"] == pytest.approx(700.0)


# ═══════════════════════════════════════════════════════════════════════════
# 6. GET /api/duplicates
# ═══════════════════════════════════════════════════════════════════════════

class TestDuplicatesAccountFilter:

    def _seed_dupes(self, client):
        """Two identical expense pairs — one on Amex, one on Chase."""
        for _ in range(2):
            _post(client, date="2026-02-01", amount=9.99, type="expense",
                  category="Dining", description="COFFEE", account="Amex")
        for _ in range(2):
            _post(client, date="2026-02-02", amount=15.00, type="expense",
                  category="Dining", description="LUNCH", account="Chase")

    def test_accounts_scopes_groups(self, client):
        self._seed_dupes(client)
        groups = client.get("/api/duplicates?accounts=Amex").json()
        assert len(groups) == 1
        assert groups[0]["account"] == "Amex"

    def test_accounts_multi(self, client):
        self._seed_dupes(client)
        groups = client.get("/api/duplicates?accounts=Amex,Chase").json()
        assert {g["account"] for g in groups} == {"Amex", "Chase"}

    def test_legacy_account(self, client):
        self._seed_dupes(client)
        groups = client.get("/api/duplicates?account=Chase").json()
        assert len(groups) == 1
        assert groups[0]["account"] == "Chase"

    def test_accounts_wins_when_both(self, client):
        self._seed_dupes(client)
        groups = client.get("/api/duplicates?account=Chase&accounts=Amex").json()
        assert len(groups) == 1
        assert groups[0]["account"] == "Amex"

    def test_absent_all(self, client):
        self._seed_dupes(client)
        groups = client.get("/api/duplicates").json()
        assert {g["account"] for g in groups} == {"Amex", "Chase"}
