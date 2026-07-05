"""
test_transfers.py — v2 'transfer' type: ledger-visible but EXCLUDED from all stats.

Per docs/api-contract.md (v2):
  transfer = money moving between the user's own accounts. Transfer rows are
  recorded and returned by the transactions endpoints, but are EXCLUDED from
  ALL statistics: total_income, total_expense, net, savings, savings_rate,
  count (summary), by-category, and over-time.
"""

from __future__ import annotations

import pytest


def _post(client, **kwargs):
    defaults = {"source": "manual"}
    defaults.update(kwargs)
    r = client.post("/api/transactions", json=defaults)
    assert r.status_code == 201, r.text
    return r.json()


def _seed_with_transfers(client):
    """
    Seed a deterministic mix of income / expense / transfer.

    income:   Salary 5000
    expense:  Rent 1200, Groceries 300, Savings 500 (savings cat)
    transfer: 'Payments and Credits' 2000, 'Transfer' 750  <- excluded everywhere

    Expected stats (transfers excluded):
      total_income  = 5000
      total_expense = 2000  (1200 + 300 + 500)
      net           = 3000
      savings       = 500
      savings_rate  = 0.10
      count         = 4     (NOT 6 — the 2 transfers are excluded)
    """
    _post(client, date="2024-01-15", amount=5000.0, type="income",   category="Salary")
    _post(client, date="2024-01-20", amount=1200.0, type="expense",  category="Rent")
    _post(client, date="2024-01-22", amount=300.0,  type="expense",  category="Groceries")
    _post(client, date="2024-01-25", amount=500.0,  type="expense",  category="Savings")
    _post(client, date="2024-01-26", amount=2000.0, type="transfer", category="Payments and Credits")
    _post(client, date="2024-01-28", amount=750.0,  type="transfer", category="Transfer")


# ---------------------------------------------------------------------------
# Transfers are accepted and visible in the ledger
# ---------------------------------------------------------------------------

class TestTransferLedgerVisibility:

    def test_post_transfer_accepted(self, client):
        """POST a transfer → 201 and returned with type=transfer."""
        tx = _post(client, date="2024-02-01", amount=1500.0, type="transfer",
                   category="Payments and Credits", description="Card payment")
        assert tx["type"] == "transfer"
        assert tx["id"] is not None

    def test_transfer_in_list_all(self, client):
        """A transfer appears in GET /api/transactions (unfiltered)."""
        created = _post(client, date="2024-02-01", amount=1500.0, type="transfer",
                        category="Transfer")
        ids = [t["id"] for t in client.get("/api/transactions").json()]
        assert created["id"] in ids

    def test_transfer_filtered_by_type(self, client):
        """GET /api/transactions?type=transfer returns transfer rows."""
        _seed_with_transfers(client)
        items = client.get("/api/transactions?type=transfer").json()
        assert len(items) == 2
        assert all(t["type"] == "transfer" for t in items)

    def test_get_transfer_by_id(self, client):
        """A transfer is retrievable by id."""
        tx = _post(client, date="2024-02-01", amount=1500.0, type="transfer",
                   category="Transfer")
        fetched = client.get(f"/api/transactions/{tx['id']}").json()
        assert fetched["type"] == "transfer"


# ---------------------------------------------------------------------------
# Summary excludes transfers (incl. count)
# ---------------------------------------------------------------------------

class TestSummaryExcludesTransfers:

    def test_total_income_excludes_transfers(self, client):
        _seed_with_transfers(client)
        s = client.get("/api/stats/summary").json()
        assert s["total_income"] == pytest.approx(5000.0)

    def test_total_expense_excludes_transfers(self, client):
        _seed_with_transfers(client)
        s = client.get("/api/stats/summary").json()
        # 1200 + 300 + 500 = 2000 (the 2000 + 750 transfers are NOT counted)
        assert s["total_expense"] == pytest.approx(2000.0)

    def test_net_excludes_transfers(self, client):
        _seed_with_transfers(client)
        s = client.get("/api/stats/summary").json()
        assert s["net"] == pytest.approx(3000.0)

    def test_savings_excludes_transfers(self, client):
        _seed_with_transfers(client)
        s = client.get("/api/stats/summary").json()
        assert s["savings"] == pytest.approx(500.0)

    def test_savings_rate_excludes_transfers(self, client):
        _seed_with_transfers(client)
        s = client.get("/api/stats/summary").json()
        assert s["savings_rate"] == pytest.approx(500.0 / 5000.0, abs=1e-4)

    def test_count_excludes_transfers(self, client):
        """count reflects only non-transfer rows (4, not 6)."""
        _seed_with_transfers(client)
        s = client.get("/api/stats/summary").json()
        assert s["count"] == 4

    def test_transfer_only_db_summary_is_zero(self, client):
        """A DB containing ONLY transfers yields an all-zero summary."""
        _post(client, date="2024-01-01", amount=1000.0, type="transfer", category="Transfer")
        _post(client, date="2024-01-02", amount=500.0,  type="transfer", category="Payments and Credits")
        s = client.get("/api/stats/summary").json()
        assert s["total_income"] == pytest.approx(0.0)
        assert s["total_expense"] == pytest.approx(0.0)
        assert s["net"] == pytest.approx(0.0)
        assert s["savings"] == pytest.approx(0.0)
        assert s["savings_rate"] == pytest.approx(0.0)
        assert s["count"] == 0


# ---------------------------------------------------------------------------
# by-category excludes transfers
# ---------------------------------------------------------------------------

class TestByCategoryExcludesTransfers:

    def test_by_category_omits_transfer_categories(self, client):
        """Transfer categories never appear in the expense breakdown."""
        _seed_with_transfers(client)
        items = client.get("/api/stats/by-category").json()
        cats = {i["category"] for i in items}
        assert "Payments and Credits" not in cats
        assert "Transfer" not in cats
        # Real expense categories are present
        assert "Rent" in cats and "Groceries" in cats and "Savings" in cats

    def test_by_category_totals_unaffected_by_transfers(self, client):
        """Expense totals match the non-transfer expenses only."""
        _seed_with_transfers(client)
        items = client.get("/api/stats/by-category").json()
        total = sum(i["total"] for i in items)
        assert total == pytest.approx(2000.0)   # 1200 + 300 + 500

    def test_by_category_pct_still_sums_100(self, client):
        """pct sums ~100 over the transfer-excluded expense set."""
        _seed_with_transfers(client)
        items = client.get("/api/stats/by-category").json()
        assert sum(i["pct"] for i in items) == pytest.approx(100.0, abs=0.5)

    def test_by_category_type_transfer_returns_empty(self, client):
        """Explicitly asking for type=transfer yields [] (transfers excluded)."""
        _seed_with_transfers(client)
        items = client.get("/api/stats/by-category?type=transfer").json()
        assert items == []


# ---------------------------------------------------------------------------
# over-time excludes transfers
# ---------------------------------------------------------------------------

class TestOverTimeExcludesTransfers:

    def test_over_time_excludes_transfer_amounts(self, client):
        """Per-period income/expense exclude transfer rows."""
        _seed_with_transfers(client)   # all in 2024-01
        items = client.get("/api/stats/over-time").json()
        by_period = {i["period"]: i for i in items}
        jan = by_period["2024-01"]
        assert jan["income"] == pytest.approx(5000.0)
        assert jan["expense"] == pytest.approx(2000.0)   # transfers excluded
        assert jan["net"] == pytest.approx(3000.0)
        assert jan["savings"] == pytest.approx(500.0)

    def test_over_time_transfer_only_period_absent(self, client):
        """A period containing only transfers does not appear in over-time."""
        # Jan: a real expense; Feb: only a transfer
        _post(client, date="2024-01-10", amount=100.0, type="expense", category="Groceries")
        _post(client, date="2024-02-10", amount=900.0, type="transfer", category="Transfer")
        items = client.get("/api/stats/over-time").json()
        periods = [i["period"] for i in items]
        assert "2024-01" in periods
        assert "2024-02" not in periods   # transfer-only month is excluded entirely

    def test_over_time_consistency_with_summary(self, client):
        """Sum of per-period income/expense equals the (transfer-excluded) summary."""
        _seed_with_transfers(client)
        summary = client.get("/api/stats/summary").json()
        ot = client.get("/api/stats/over-time").json()
        assert sum(p["income"] for p in ot) == pytest.approx(summary["total_income"])
        assert sum(p["expense"] for p in ot) == pytest.approx(summary["total_expense"])
