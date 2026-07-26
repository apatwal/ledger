"""
test_investments.py — Regression tests for the v11 investments/savings backend.

Covers the surface introduced in v11 / v11b, which previously had ZERO coverage:

* Account nicknames        GET/PUT/DELETE /api/accounts/nicknames
* Savings designation      PUT /api/accounts/savings
* Savings mapping (unit)   src.api.plaid_mapping.map_transaction
* Savings rate (API)       GET /api/stats/summary
* Net worth                GET /api/stats/net-worth
* Investment flows         GET /api/stats/investment-flows
* Snapshots                GET/POST /api/investments/*

Plaid is unconfigured in the test environment, so Plaid-network endpoints must
return 503. We assert that gating rather than mocking Plaid.
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _post_tx(client, **kwargs) -> dict:
    """Create a transaction and assert success."""
    defaults = {"source": "manual"}
    defaults.update(kwargs)
    resp = client.post("/api/transactions", json=defaults)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _nicknames(client) -> list[dict]:
    """GET the current nicknames list."""
    resp = client.get("/api/accounts/nicknames")
    assert resp.status_code == 200, resp.text
    return resp.json()


def _by_label(rows: list[dict]) -> dict[str, dict]:
    return {r["label"]: r for r in rows}


# ===========================================================================
# Account nicknames — GET/PUT/DELETE /api/accounts/nicknames
# ===========================================================================

class TestAccountNicknames:

    def test_get_empty(self, client):
        """No nicknames set → empty list."""
        assert _nicknames(client) == []

    def test_put_sets_nickname(self, client):
        """PUT sets a nickname and returns the full list containing it."""
        resp = client.put(
            "/api/accounts/nicknames",
            json={"label": "Chase ...1234", "nickname": "Everyday Checking"},
        )
        assert resp.status_code == 200, resp.text
        by = _by_label(resp.json())
        assert "Chase ...1234" in by
        assert by["Chase ...1234"]["nickname"] == "Everyday Checking"

    def test_get_returns_nickname_is_savings_false_default(self, client):
        """GET reflects the PUT with is_savings=False by default."""
        client.put(
            "/api/accounts/nicknames",
            json={"label": "Chase ...1234", "nickname": "Everyday Checking"},
        )
        by = _by_label(_nicknames(client))
        assert by["Chase ...1234"]["nickname"] == "Everyday Checking"
        assert by["Chase ...1234"]["is_savings"] is False

    def test_put_updates_existing_nickname(self, client):
        """A second PUT on the same label overwrites the nickname."""
        client.put("/api/accounts/nicknames", json={"label": "L", "nickname": "First"})
        client.put("/api/accounts/nicknames", json={"label": "L", "nickname": "Second"})
        by = _by_label(_nicknames(client))
        assert by["L"]["nickname"] == "Second"

    def test_put_blank_nickname_deletes_non_savings_row(self, client):
        """Blank/whitespace nickname on a NON-savings row deletes it."""
        client.put("/api/accounts/nicknames", json={"label": "L", "nickname": "Name"})
        resp = client.put("/api/accounts/nicknames", json={"label": "L", "nickname": "   "})
        assert resp.status_code == 200
        assert "L" not in _by_label(resp.json())
        # GET no longer lists it.
        assert "L" not in _by_label(_nicknames(client))

    def test_put_blank_nickname_keeps_savings_row(self, client):
        """Blank nickname on an is_savings=True row keeps the row (flag survives)."""
        # First designate the label as savings (also give it a nickname).
        client.put("/api/accounts/nicknames", json={"label": "Ally", "nickname": "Ally Save"})
        client.put("/api/accounts/savings", json={"label": "Ally", "is_savings": True})
        # Now clear the nickname with a blank PUT.
        resp = client.put("/api/accounts/nicknames", json={"label": "Ally", "nickname": ""})
        by = _by_label(resp.json())
        assert "Ally" in by, "savings row must not be deleted by a blank nickname"
        assert by["Ally"]["nickname"] == ""
        assert by["Ally"]["is_savings"] is True

    def test_delete_removes_nickname(self, client):
        """DELETE /api/accounts/nicknames/{label} removes it."""
        client.put("/api/accounts/nicknames", json={"label": "Chase", "nickname": "N"})
        resp = client.delete("/api/accounts/nicknames/Chase")
        assert resp.status_code == 204
        assert "Chase" not in _by_label(_nicknames(client))

    def test_delete_is_idempotent(self, client):
        """Second DELETE on the same label still returns 204."""
        client.put("/api/accounts/nicknames", json={"label": "Chase", "nickname": "N"})
        assert client.delete("/api/accounts/nicknames/Chase").status_code == 204
        assert client.delete("/api/accounts/nicknames/Chase").status_code == 204

    def test_delete_nonexistent_is_204(self, client):
        """DELETE on a label that never existed is 204 (idempotent)."""
        assert client.delete("/api/accounts/nicknames/Never").status_code == 204


# ===========================================================================
# Savings designation — PUT /api/accounts/savings
# ===========================================================================

class TestSavingsDesignation:

    def test_set_savings_true_returns_updated_list(self, client):
        """PUT is_savings:true returns list with that label is_savings=True."""
        resp = client.put("/api/accounts/savings", json={"label": "Ally", "is_savings": True})
        assert resp.status_code == 200, resp.text
        by = _by_label(resp.json())
        assert by["Ally"]["is_savings"] is True

    def test_set_savings_true_reflected_in_get(self, client):
        """GET nicknames reflects the savings designation."""
        client.put("/api/accounts/savings", json={"label": "Ally", "is_savings": True})
        by = _by_label(_nicknames(client))
        assert "Ally" in by
        assert by["Ally"]["is_savings"] is True

    def test_set_savings_false_no_nickname_deletes_row(self, client):
        """is_savings:false on a row with no nickname deletes the row."""
        # Create a savings-only row (no nickname).
        client.put("/api/accounts/savings", json={"label": "Ally", "is_savings": True})
        assert "Ally" in _by_label(_nicknames(client))
        # Un-designate: nothing left to persist → row deleted.
        resp = client.put("/api/accounts/savings", json={"label": "Ally", "is_savings": False})
        assert "Ally" not in _by_label(resp.json())
        assert "Ally" not in _by_label(_nicknames(client))

    def test_set_savings_preserves_existing_nickname(self, client):
        """Setting is_savings on a label with a nickname preserves the nickname."""
        client.put("/api/accounts/nicknames", json={"label": "Ally", "nickname": "My Savings"})
        resp = client.put("/api/accounts/savings", json={"label": "Ally", "is_savings": True})
        by = _by_label(resp.json())
        assert by["Ally"]["nickname"] == "My Savings"
        assert by["Ally"]["is_savings"] is True

    def test_set_savings_false_keeps_row_with_nickname(self, client):
        """is_savings:false on a row that still has a nickname keeps the row."""
        client.put("/api/accounts/nicknames", json={"label": "Ally", "nickname": "My Savings"})
        client.put("/api/accounts/savings", json={"label": "Ally", "is_savings": True})
        resp = client.put("/api/accounts/savings", json={"label": "Ally", "is_savings": False})
        by = _by_label(resp.json())
        assert "Ally" in by
        assert by["Ally"]["nickname"] == "My Savings"
        assert by["Ally"]["is_savings"] is False


# ===========================================================================
# Savings mapping — unit test of src.api.plaid_mapping.map_transaction
# ===========================================================================

class TestSavingsMapping:

    def _txn(self, primary, amount):
        return {
            "personal_finance_category": {"primary": primary},
            "amount": amount,
            "name": "x",
            "date": "2026-07-01",
            "transaction_id": "t",
            "account_id": "a",
        }

    def test_transfer_in_to_savings_account_becomes_savings_expense(self):
        from src.api.plaid_mapping import map_transaction

        out = map_transaction(
            self._txn("TRANSFER_IN", -500), "Ally", is_savings_account=True
        )
        assert out["type"] == "expense"
        assert out["category"] == "Savings"

    def test_transfer_in_to_non_savings_account_stays_transfer(self):
        from src.api.plaid_mapping import map_transaction

        out = map_transaction(
            self._txn("TRANSFER_IN", -500), "Ally", is_savings_account=False
        )
        assert out["type"] == "transfer"
        assert out["category"] == "Transfer"

    def test_transfer_out_from_savings_account_stays_transfer(self):
        """Outflow (amount > 0) from a savings account is untouched."""
        from src.api.plaid_mapping import map_transaction

        out = map_transaction(
            self._txn("TRANSFER_OUT", 500), "Ally", is_savings_account=True
        )
        assert out["type"] == "transfer"

    def test_income_into_savings_account_stays_income(self):
        """Interest INCOME into a savings account is not recategorized."""
        from src.api.plaid_mapping import map_transaction

        out = map_transaction(
            self._txn("INCOME", -50), "Ally", is_savings_account=True
        )
        assert out["type"] == "income"

    def test_savings_is_canonical_and_module_imports_without_raising(self):
        """'Savings' is a canonical category and the drift-guard assertion holds."""
        import importlib
        import src.api.plaid_mapping as pm

        # Re-importing must not raise (module-level drift-guard assert stays true).
        importlib.reload(pm)
        assert "Savings" in pm.CANONICAL_CATEGORIES
        # The drift guard: every mapped label + Investment + Savings + fallback
        # is canonical.
        assert set(pm.CANONICAL_CATEGORIES) >= set(pm.PFC_CATEGORY_LABELS.values()) | {
            "Investment",
            "Savings",
            pm.FALLBACK_CATEGORY,
        }


# ===========================================================================
# Savings rate integration — GET /api/stats/summary
# ===========================================================================

class TestSavingsRateIntegration:

    def test_savings_row_and_rate(self, client):
        """A Savings expense counts toward savings; rate = savings / income."""
        _post_tx(client, date="2026-07-01", amount=5000.0, type="income", category="Salary")
        _post_tx(client, date="2026-07-05", amount=1000.0, type="expense", category="Savings")

        data = client.get("/api/stats/summary").json()
        assert data["total_income"] == pytest.approx(5000.0)
        assert data["savings"] == pytest.approx(1000.0)
        assert data["savings_rate"] == pytest.approx(1000.0 / 5000.0)  # 0.2


# ===========================================================================
# Net worth — GET /api/stats/net-worth
# ===========================================================================

class TestNetWorth:

    def test_net_worth_no_plaid_returns_200_zeros(self, client):
        """No Plaid items → 200 (NOT 503) with zeroed assets/liabilities."""
        resp = client.get("/api/stats/net-worth")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        for key in ("net_worth", "assets", "liabilities", "currency", "as_of"):
            assert key in data, f"Missing key: {key}"
        assert data["net_worth"] == pytest.approx(0.0)
        assert data["assets"] == pytest.approx(0.0)
        assert data["liabilities"] == pytest.approx(0.0)
        assert data["currency"] == "USD"
        assert data["as_of"]  # non-empty timestamp


# ===========================================================================
# Investment flows — GET /api/stats/investment-flows
# ===========================================================================

class TestInvestmentFlows:

    def test_empty_returns_empty_list(self, client):
        """No Investment rows → 200 and empty list."""
        resp = client.get("/api/stats/investment-flows")
        assert resp.status_code == 200, resp.text
        assert resp.json() == []

    def test_contributions_and_dividends_grouped(self, client):
        """Investment expense → contributions; Investment income → dividends."""
        _post_tx(client, date="2026-07-01", amount=300.0, type="expense", category="Investment")
        _post_tx(client, date="2026-07-10", amount=25.0, type="income", category="Investment")
        # A transfer-typed Investment row must be counted in neither series.
        _post_tx(client, date="2026-07-15", amount=999.0, type="transfer", category="Investment")

        items = client.get("/api/stats/investment-flows").json()
        assert isinstance(items, list)
        by_period = {i["period"]: i for i in items}
        assert "2026-07" in by_period
        row = by_period["2026-07"]
        assert row["contributions"] == pytest.approx(300.0)
        assert row["dividends"] == pytest.approx(25.0)

    def test_non_investment_rows_excluded(self, client):
        """Rows in other categories don't appear in investment flows."""
        _post_tx(client, date="2026-07-01", amount=100.0, type="expense", category="Groceries")
        assert client.get("/api/stats/investment-flows").json() == []


# ===========================================================================
# Snapshots — GET/POST /api/investments/*
# ===========================================================================

class TestSnapshots:

    def test_get_snapshots_empty(self, client):
        """GET snapshots → 200 and [] when empty."""
        resp = client.get("/api/investments/snapshots")
        assert resp.status_code == 200, resp.text
        assert resp.json() == []

    def test_post_snapshot_503_when_plaid_unconfigured(self, client, monkeypatch):
        """POST snapshot → 503 when Plaid is unconfigured.

        `is_configured()` reads PLAID_CLIENT_ID/PLAID_SECRET from the env at call
        time, and main.py's load_dotenv() may populate them from a local .env, so
        we delenv them here (matching test_plaid.py) to assert the gate is OFF —
        deterministically in both a clean CI env and a local dev env.
        """
        monkeypatch.delenv("PLAID_CLIENT_ID", raising=False)
        monkeypatch.delenv("PLAID_SECRET", raising=False)
        resp = client.post("/api/investments/snapshot")
        assert resp.status_code == 503, resp.text
