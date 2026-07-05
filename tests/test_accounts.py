"""
test_accounts.py — v4 per-card / account metrics.

Covers:
  1. `account` field on transactions: round-trips on TransactionOut, PUT updates,
     GET ?account=X filters, null account allowed.
  2. CSV account tagging: importing a fixture with form field `account=X` tags
     every imported row with X.
  3. GET /api/stats/by-account: per-card income/expense/net/count, descending by
     expense, transfers excluded, null/empty -> "Unassigned".
  4. GET /api/accounts: distinct non-empty accounts.
  5. `account` filter on stats endpoints (summary, by-category).

Ground-truth values (verified against the real backend with the shipped
fixtures + isolated DB). v5.4: Chase `Return` rows are now `refund` (net
against spend, included in count) — not transfer — so Chase's expense TOTAL
drops and its stats count rises:
  discover_sample.csv (account=Discover): imported 15, transfers 2,
     13 expenses totaling 296.90, income 0.
  chase_sample.csv    (account=Chase):    imported 157, transfers 13,
     net expense 2014.72 (133 expense − 11 refund), stats count 144, income 0.
  Combined: net expense 2311.62, stats count 157.
"""

from __future__ import annotations

import pytest

from .conftest import fixture_csv_file

# Known per-card aggregates (transfers excluded; refunds net against spend and
# are included in the stats count — v5.4) for the shipped fixtures.
DISCOVER_EXPENSE = 296.90
DISCOVER_EXPENSE_COUNT = 13
CHASE_EXPENSE = 2014.72            # v5.4: 133 expense − 11 refund (net)
CHASE_STAT_COUNT = 144            # v5.4: 133 expense + 11 refund (transfers excluded)
COMBINED_EXPENSE = round(DISCOVER_EXPENSE + CHASE_EXPENSE, 2)   # 2311.62
COMBINED_COUNT = DISCOVER_EXPENSE_COUNT + CHASE_STAT_COUNT      # 157


def _import_fixture(client, name: str, account: str | None):
    """Import a fixture CSV, optionally tagging with an account form field."""
    data = {"account": account} if account is not None else None
    resp = client.post("/api/transactions/csv", files=fixture_csv_file(name), data=data)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _post(client, **kwargs):
    defaults = {"source": "manual"}
    defaults.update(kwargs)
    r = client.post("/api/transactions", json=defaults)
    assert r.status_code == 201, r.text
    return r.json()


# ---------------------------------------------------------------------------
# 1. account field on transactions
# ---------------------------------------------------------------------------

class TestAccountField:

    def test_create_with_account_round_trips(self, client):
        """POST with account -> it's returned on TransactionOut."""
        tx = _post(client, date="2024-01-01", amount=50.0, type="expense",
                   category="Groceries", account="Chase")
        assert tx["account"] == "Chase"
        # And on a subsequent GET by id
        fetched = client.get(f"/api/transactions/{tx['id']}").json()
        assert fetched["account"] == "Chase"

    def test_create_without_account_is_null(self, client):
        """account is optional; omitting it yields null."""
        tx = _post(client, date="2024-01-01", amount=50.0, type="expense",
                   category="Groceries")
        assert tx["account"] is None

    def test_create_explicit_null_account(self, client):
        """Explicit null account is accepted."""
        tx = _post(client, date="2024-01-01", amount=50.0, type="expense",
                   category="Groceries", account=None)
        assert tx["account"] is None

    def test_put_updates_account(self, client):
        """PUT can set/change the account."""
        tx = _post(client, date="2024-01-01", amount=50.0, type="expense",
                   category="Groceries")
        assert tx["account"] is None
        resp = client.put(f"/api/transactions/{tx['id']}", json={
            "date": "2024-01-01", "amount": 50.0, "type": "expense",
            "category": "Groceries", "account": "Discover",
        })
        assert resp.status_code == 200
        assert resp.json()["account"] == "Discover"
        # persisted
        assert client.get(f"/api/transactions/{tx['id']}").json()["account"] == "Discover"

    def test_filter_by_account(self, client):
        """GET /api/transactions?account=X returns only that account's rows."""
        _post(client, date="2024-01-01", amount=10.0, type="expense", category="A", account="Chase")
        _post(client, date="2024-01-02", amount=20.0, type="expense", category="B", account="Discover")
        _post(client, date="2024-01-03", amount=30.0, type="expense", category="C")  # null

        chase = client.get("/api/transactions?account=Chase").json()
        assert len(chase) == 1
        assert all(t["account"] == "Chase" for t in chase)

        discover = client.get("/api/transactions?account=Discover").json()
        assert len(discover) == 1
        assert discover[0]["account"] == "Discover"


# ---------------------------------------------------------------------------
# 2. CSV account tagging
# ---------------------------------------------------------------------------

class TestCSVAccountTagging:

    def test_discover_tagged(self, client):
        """Every row imported from the Discover fixture carries account=Discover."""
        result = _import_fixture(client, "discover_sample.csv", "Discover")
        assert result["imported"] == 15
        txs = client.get("/api/transactions?account=Discover").json()
        assert len(txs) == 15
        assert all(t["account"] == "Discover" for t in txs)

    def test_chase_tagged(self, client):
        """Every row imported from the Chase fixture carries account=Chase."""
        result = _import_fixture(client, "chase_sample.csv", "Chase")
        assert result["imported"] == 157
        # limit defaults to 100 — raise it to page in all rows
        txs = client.get("/api/transactions?account=Chase&limit=1000").json()
        assert len(txs) == 157
        assert all(t["account"] == "Chase" for t in txs)

    def test_import_without_account_is_null(self, client):
        """Importing with no account form field leaves account null."""
        _import_fixture(client, "discover_sample.csv", None)
        txs = client.get("/api/transactions").json()
        assert all(t["account"] is None for t in txs)

    def test_two_fixtures_keep_distinct_accounts(self, client):
        """Importing both fixtures keeps each file's rows on the right card."""
        _import_fixture(client, "discover_sample.csv", "Discover")
        _import_fixture(client, "chase_sample.csv", "Chase")
        disc = client.get("/api/transactions?account=Discover&limit=1000").json()
        chase = client.get("/api/transactions?account=Chase&limit=1000").json()
        assert len(disc) == 15
        assert len(chase) == 157
        assert all(t["account"] == "Discover" for t in disc)
        assert all(t["account"] == "Chase" for t in chase)


# ---------------------------------------------------------------------------
# 3. GET /api/stats/by-account
# ---------------------------------------------------------------------------

class TestByAccount:

    def _import_both(self, client):
        _import_fixture(client, "discover_sample.csv", "Discover")
        _import_fixture(client, "chase_sample.csv", "Chase")

    def test_by_account_returns_both_cards(self, client):
        self._import_both(client)
        items = client.get("/api/stats/by-account").json()
        accounts = {i["account"] for i in items}
        assert accounts == {"Chase", "Discover"}

    def test_by_account_descending_by_expense(self, client):
        """Chase (net expense 2014.72) before Discover (296.90)."""
        self._import_both(client)
        items = client.get("/api/stats/by-account").json()
        expenses = [i["expense"] for i in items]
        assert expenses == sorted(expenses, reverse=True)
        assert items[0]["account"] == "Chase"
        assert items[1]["account"] == "Discover"

    def test_by_account_per_card_values(self, client):
        """Per-card expense/income/net/count are correct (transfers excluded)."""
        self._import_both(client)
        items = client.get("/api/stats/by-account").json()
        by = {i["account"]: i for i in items}

        assert by["Discover"]["expense"] == pytest.approx(DISCOVER_EXPENSE, abs=0.01)
        assert by["Discover"]["income"] == pytest.approx(0.0)
        assert by["Discover"]["net"] == pytest.approx(-DISCOVER_EXPENSE, abs=0.01)
        assert by["Discover"]["count"] == DISCOVER_EXPENSE_COUNT

        assert by["Chase"]["expense"] == pytest.approx(CHASE_EXPENSE, abs=0.01)
        assert by["Chase"]["income"] == pytest.approx(0.0)
        assert by["Chase"]["net"] == pytest.approx(-CHASE_EXPENSE, abs=0.01)
        assert by["Chase"]["count"] == CHASE_STAT_COUNT

    def test_by_account_excludes_transfers(self, client):
        """Payments (transfers) are not counted in any card's totals; refunds ARE.

        v5.4: Discover has 2 transfers + 13 expenses = 15 imported, by-account
        count 13. Chase has 13 transfers (payments) + 144 non-transfers
        (133 expense + 11 refund), so by-account count is 144.
        """
        self._import_both(client)
        items = client.get("/api/stats/by-account").json()
        by = {i["account"]: i for i in items}
        assert by["Discover"]["count"] == 13    # not 15
        assert by["Chase"]["count"] == 144       # 133 expense + 11 refund (v5.4)
        # Cross-check: per-card expense sums to the global (transfer-excluded) total
        total = sum(i["expense"] for i in items)
        assert total == pytest.approx(COMBINED_EXPENSE, abs=0.01)

    def test_by_account_unassigned_for_null(self, client):
        """Null/empty account is reported as 'Unassigned'."""
        _post(client, date="2024-01-01", amount=100.0, type="expense", category="Misc")          # null
        _post(client, date="2024-01-02", amount=50.0,  type="expense", category="Misc", account="")  # empty
        _post(client, date="2024-01-03", amount=25.0,  type="expense", category="Misc", account="Chase")
        items = client.get("/api/stats/by-account").json()
        by = {i["account"]: i for i in items}
        assert "Unassigned" in by
        # both null + empty land in Unassigned: 100 + 50 = 150
        assert by["Unassigned"]["expense"] == pytest.approx(150.0)
        assert by["Unassigned"]["count"] == 2
        assert by["Chase"]["expense"] == pytest.approx(25.0)

    def test_by_account_empty_db(self, client):
        """Empty DB -> empty list."""
        assert client.get("/api/stats/by-account").json() == []


# ---------------------------------------------------------------------------
# 4. GET /api/accounts
# ---------------------------------------------------------------------------

class TestAccountsList:

    def test_accounts_distinct_non_empty(self, client):
        """Returns distinct non-empty accounts, ascending."""
        _import_fixture(client, "discover_sample.csv", "Discover")
        _import_fixture(client, "chase_sample.csv", "Chase")
        accounts = client.get("/api/accounts").json()
        assert accounts == ["Chase", "Discover"]

    def test_accounts_excludes_null_and_empty(self, client):
        """Null/empty accounts do not appear in /api/accounts."""
        _post(client, date="2024-01-01", amount=10.0, type="expense", category="A")           # null
        _post(client, date="2024-01-02", amount=20.0, type="expense", category="B", account="")  # empty
        _post(client, date="2024-01-03", amount=30.0, type="expense", category="C", account="Chase")
        accounts = client.get("/api/accounts").json()
        assert accounts == ["Chase"]

    def test_accounts_empty_db(self, client):
        """No accounts when nothing is tagged."""
        _post(client, date="2024-01-01", amount=10.0, type="expense", category="A")
        assert client.get("/api/accounts").json() == []

    def test_accounts_is_list_of_strings(self, client):
        _post(client, date="2024-01-01", amount=10.0, type="expense", category="A", account="Visa")
        accounts = client.get("/api/accounts").json()
        assert isinstance(accounts, list)
        assert all(isinstance(a, str) for a in accounts)


# ---------------------------------------------------------------------------
# 5. account filter on stats endpoints
# ---------------------------------------------------------------------------

class TestStatsAccountFilter:

    def _import_both(self, client):
        _import_fixture(client, "discover_sample.csv", "Discover")
        _import_fixture(client, "chase_sample.csv", "Chase")

    def test_summary_account_filter_chase(self, client):
        """summary?account=Chase restricts to Chase rows."""
        self._import_both(client)
        s = client.get("/api/stats/summary?account=Chase").json()
        assert s["total_expense"] == pytest.approx(CHASE_EXPENSE, abs=0.01)
        assert s["count"] == CHASE_STAT_COUNT

    def test_summary_account_filter_discover(self, client):
        """summary?account=Discover restricts to Discover rows."""
        self._import_both(client)
        s = client.get("/api/stats/summary?account=Discover").json()
        assert s["total_expense"] == pytest.approx(DISCOVER_EXPENSE, abs=0.01)
        assert s["count"] == DISCOVER_EXPENSE_COUNT

    def test_summary_no_account_aggregates_both(self, client):
        """No account filter aggregates across both cards."""
        self._import_both(client)
        s = client.get("/api/stats/summary").json()
        assert s["total_expense"] == pytest.approx(COMBINED_EXPENSE, abs=0.01)
        assert s["count"] == COMBINED_COUNT

    def test_summary_account_split_sums_to_total(self, client):
        """Chase + Discover summary expense == unfiltered summary expense."""
        self._import_both(client)
        chase = client.get("/api/stats/summary?account=Chase").json()["total_expense"]
        disc = client.get("/api/stats/summary?account=Discover").json()["total_expense"]
        allx = client.get("/api/stats/summary").json()["total_expense"]
        assert chase + disc == pytest.approx(allx, abs=0.01)

    def test_by_category_account_filter_discover(self, client):
        """by-category?account=Discover only reflects Discover spending."""
        self._import_both(client)
        items = client.get("/api/stats/by-category?account=Discover").json()
        # Discover fixture categories are Restaurants + Merchandise only
        cats = {i["category"] for i in items}
        assert cats <= {"Restaurants", "Merchandise"}
        assert "Restaurants" in cats
        # Per-card category totals sum to that card's expense
        total = sum(i["total"] for i in items)
        assert total == pytest.approx(DISCOVER_EXPENSE, abs=0.01)

    def test_by_category_account_filter_chase_differs(self, client):
        """by-category?account=Chase reflects Chase spending (different total)."""
        self._import_both(client)
        items = client.get("/api/stats/by-category?account=Chase").json()
        total = sum(i["total"] for i in items)
        assert total == pytest.approx(CHASE_EXPENSE, abs=0.01)
        # Chase has categories Discover doesn't (e.g. Travel/Groceries/Gas)
        cats = {i["category"] for i in items}
        assert "Restaurants" not in cats   # that's a Discover-only label here

    def test_by_category_no_account_aggregates_both(self, client):
        """by-category with no account spans both cards."""
        self._import_both(client)
        items = client.get("/api/stats/by-category").json()
        total = sum(i["total"] for i in items)
        assert total == pytest.approx(COMBINED_EXPENSE, abs=0.01)
