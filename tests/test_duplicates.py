"""
test_duplicates.py — Tests for duplicate charge detection (v7).

Coverage
--------
* GET /api/duplicates
  - Two identical expense rows → one group, count=2, total_extra=amount, group_key
  - Re-import scenario (same rows inserted twice) → detected
  - Normalization of description (case/whitespace) and account (case/whitespace)
  - Negative cases: differing date / amount / merchant / account, single row
  - Only expenses grouped (income/transfer/refund with identical fields ignored)
  - 3+ copies → count=3, total_extra = 2*amount
  - Date-range and account query filters
  - Groups sorted by total_extra descending
* POST /api/duplicates/dismiss
  - Dismiss a group's ids → dismissed count; group no longer returned
  - Dismissing one of two rows collapses the group
  - Non-existent id ignored; empty ids → {"dismissed": 0}
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _post_tx(client, **kwargs) -> dict:
    """Create a transaction and assert success, returning the created dict."""
    defaults = {"type": "expense", "category": "Shopping", "source": "manual"}
    defaults.update(kwargs)
    resp = client.post("/api/transactions", json=defaults)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _get_dupes(client, **params) -> list:
    """GET /api/duplicates → assert 200, return the list of groups."""
    resp = client.get("/api/duplicates", params=params or None)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _dismiss(client, ids) -> dict:
    """POST /api/duplicates/dismiss → assert 200, return the body."""
    resp = client.post("/api/duplicates/dismiss", json={"ids": ids})
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# GET /api/duplicates — core detection
# ---------------------------------------------------------------------------

class TestDetectDuplicates:

    def test_two_identical_rows_form_one_group(self, client):
        """Two identical expense rows → single group, count=2, total_extra=amount."""
        _post_tx(client, date="2026-03-02", amount=100.00,
                 description="Rent", account="BofA")
        _post_tx(client, date="2026-03-02", amount=100.00,
                 description="Rent", account="BofA")

        groups = _get_dupes(client)
        assert len(groups) == 1
        g = groups[0]
        assert g["count"] == 2
        assert g["amount"] == pytest.approx(100.00)
        assert g["total_extra"] == pytest.approx(100.00)
        assert g["group_key"] == "2026-03-02|100.00|rent|bofa"
        assert g["date"] == "2026-03-02"
        assert g["description"] == "Rent"      # raw echo from newest row
        assert g["account"] == "BofA"
        assert len(g["transactions"]) == 2

    def test_transactions_newest_first(self, client):
        """transactions are ordered newest-first (date desc, id desc)."""
        # Same date so ordering falls back to id desc.
        first = _post_tx(client, date="2026-04-01", amount=42.00,
                         description="Coffee", account="Amex")
        second = _post_tx(client, date="2026-04-01", amount=42.00,
                          description="Coffee", account="Amex")

        g = _get_dupes(client)[0]
        ids = [t["id"] for t in g["transactions"]]
        assert ids == [second["id"], first["id"]]

    def test_transaction_out_includes_dup_dismissed(self, client):
        """TransactionOut now exposes dup_dismissed (False for fresh rows)."""
        _post_tx(client, date="2026-05-01", amount=9.99,
                 description="App Store", account="Chase")
        _post_tx(client, date="2026-05-01", amount=9.99,
                 description="App Store", account="Chase")

        g = _get_dupes(client)[0]
        for t in g["transactions"]:
            assert "dup_dismissed" in t
            assert t["dup_dismissed"] is False

    def test_reimport_scenario_detected(self, client):
        """A re-import yields identical date+amount+merchant+account rows → grouped."""
        batch = [
            {"date": "2026-06-01", "amount": 12.50, "description": "Netflix", "account": "Visa"},
            {"date": "2026-06-02", "amount": 55.00, "description": "Gym", "account": "Visa"},
        ]
        # Import once, then again (simulating an overlapping statement re-import).
        for _ in range(2):
            for row in batch:
                _post_tx(client, **row)

        groups = _get_dupes(client)
        assert len(groups) == 2
        for g in groups:
            assert g["count"] == 2


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

class TestNormalization:

    def test_description_case_and_whitespace_normalized(self, client):
        """Descriptions differing only by case/whitespace still group together."""
        _post_tx(client, date="2026-03-10", amount=20.00,
                 description="Whole Foods", account="Amex")
        _post_tx(client, date="2026-03-10", amount=20.00,
                 description="  whole   foods  ", account="Amex")

        groups = _get_dupes(client)
        assert len(groups) == 1
        assert groups[0]["count"] == 2
        assert groups[0]["group_key"] == "2026-03-10|20.00|whole foods|amex"

    def test_account_case_and_whitespace_normalized(self, client):
        """Accounts differing only by case/whitespace still group together."""
        _post_tx(client, date="2026-03-11", amount=30.00,
                 description="Uber", account="Chase Sapphire")
        _post_tx(client, date="2026-03-11", amount=30.00,
                 description="Uber", account="  chase sapphire  ")

        groups = _get_dupes(client)
        assert len(groups) == 1
        assert groups[0]["count"] == 2
        assert groups[0]["group_key"] == "2026-03-11|30.00|uber|chase sapphire"


# ---------------------------------------------------------------------------
# Negative cases — NOT grouped
# ---------------------------------------------------------------------------

class TestNotGrouped:

    def test_single_row_not_flagged(self, client):
        """A single non-repeated row → no groups."""
        _post_tx(client, date="2026-03-02", amount=100.00,
                 description="Rent", account="BofA")
        assert _get_dupes(client) == []

    def test_different_date_not_grouped(self, client):
        _post_tx(client, date="2026-03-02", amount=100.00,
                 description="Rent", account="BofA")
        _post_tx(client, date="2026-03-03", amount=100.00,
                 description="Rent", account="BofA")
        assert _get_dupes(client) == []

    def test_different_amount_not_grouped(self, client):
        _post_tx(client, date="2026-03-02", amount=100.00,
                 description="Rent", account="BofA")
        _post_tx(client, date="2026-03-02", amount=100.01,
                 description="Rent", account="BofA")
        assert _get_dupes(client) == []

    def test_different_merchant_not_grouped(self, client):
        _post_tx(client, date="2026-03-02", amount=100.00,
                 description="Rent", account="BofA")
        _post_tx(client, date="2026-03-02", amount=100.00,
                 description="Mortgage", account="BofA")
        assert _get_dupes(client) == []

    def test_different_account_not_grouped(self, client):
        _post_tx(client, date="2026-03-02", amount=100.00,
                 description="Rent", account="BofA")
        _post_tx(client, date="2026-03-02", amount=100.00,
                 description="Rent", account="Chase")
        assert _get_dupes(client) == []


# ---------------------------------------------------------------------------
# Only expenses are considered
# ---------------------------------------------------------------------------

class TestOnlyExpenses:

    def test_income_not_flagged(self, client):
        _post_tx(client, type="income", category="Salary",
                 date="2026-03-02", amount=5000.00,
                 description="Payroll", account="BofA")
        _post_tx(client, type="income", category="Salary",
                 date="2026-03-02", amount=5000.00,
                 description="Payroll", account="BofA")
        assert _get_dupes(client) == []

    def test_transfer_not_flagged(self, client):
        _post_tx(client, type="transfer", category="Transfer",
                 date="2026-03-02", amount=250.00,
                 description="Move to savings", account="BofA")
        _post_tx(client, type="transfer", category="Transfer",
                 date="2026-03-02", amount=250.00,
                 description="Move to savings", account="BofA")
        assert _get_dupes(client) == []

    def test_refund_not_flagged(self, client):
        _post_tx(client, type="refund", category="Refund",
                 date="2026-03-02", amount=75.00,
                 description="Return", account="BofA")
        _post_tx(client, type="refund", category="Refund",
                 date="2026-03-02", amount=75.00,
                 description="Return", account="BofA")
        assert _get_dupes(client) == []

    def test_non_expense_types_do_not_join_expense_group(self, client):
        """Identical income row must not inflate an expense duplicate group."""
        _post_tx(client, type="expense", date="2026-03-02", amount=100.00,
                 description="Rent", account="BofA")
        _post_tx(client, type="expense", date="2026-03-02", amount=100.00,
                 description="Rent", account="BofA")
        _post_tx(client, type="income", category="Salary",
                 date="2026-03-02", amount=100.00,
                 description="Rent", account="BofA")

        groups = _get_dupes(client)
        assert len(groups) == 1
        assert groups[0]["count"] == 2


# ---------------------------------------------------------------------------
# 3+ copies
# ---------------------------------------------------------------------------

class TestMultipleCopies:

    def test_three_copies(self, client):
        """3 identical rows → count=3, total_extra = 2*amount."""
        for _ in range(3):
            _post_tx(client, date="2026-03-02", amount=100.00,
                     description="Rent", account="BofA")

        groups = _get_dupes(client)
        assert len(groups) == 1
        g = groups[0]
        assert g["count"] == 3
        assert g["total_extra"] == pytest.approx(200.00)
        assert len(g["transactions"]) == 3


# ---------------------------------------------------------------------------
# POST /api/duplicates/dismiss
# ---------------------------------------------------------------------------

class TestDismiss:

    def test_dismiss_group_removes_it(self, client):
        """Dismissing all ids in a group → group no longer returned."""
        a = _post_tx(client, date="2026-03-02", amount=100.00,
                     description="Rent", account="BofA")
        b = _post_tx(client, date="2026-03-02", amount=100.00,
                     description="Rent", account="BofA")

        assert len(_get_dupes(client)) == 1

        body = _dismiss(client, [a["id"], b["id"]])
        assert body == {"dismissed": 2}
        assert _get_dupes(client) == []

    def test_dismiss_one_of_two_collapses_group(self, client):
        """Dismissing only ONE of two rows leaves count=1 → group not returned."""
        a = _post_tx(client, date="2026-03-02", amount=100.00,
                     description="Rent", account="BofA")
        _post_tx(client, date="2026-03-02", amount=100.00,
                 description="Rent", account="BofA")

        body = _dismiss(client, [a["id"]])
        assert body == {"dismissed": 1}
        assert _get_dupes(client) == []

    def test_dismiss_one_of_three_keeps_group(self, client):
        """Dismissing one of three copies leaves a valid count=2 group."""
        ids = [
            _post_tx(client, date="2026-03-02", amount=100.00,
                     description="Rent", account="BofA")["id"]
            for _ in range(3)
        ]
        body = _dismiss(client, [ids[0]])
        assert body == {"dismissed": 1}

        groups = _get_dupes(client)
        assert len(groups) == 1
        assert groups[0]["count"] == 2

    def test_dismiss_nonexistent_id_ignored(self, client):
        """Non-existent ids are ignored in the dismissed count."""
        a = _post_tx(client, date="2026-03-02", amount=100.00,
                     description="Rent", account="BofA")
        _post_tx(client, date="2026-03-02", amount=100.00,
                 description="Rent", account="BofA")

        body = _dismiss(client, [a["id"], 999999])
        assert body == {"dismissed": 1}

    def test_dismiss_empty_ids(self, client):
        """Empty ids list → {"dismissed": 0}."""
        assert _dismiss(client, []) == {"dismissed": 0}


# ---------------------------------------------------------------------------
# Query filters: start_date / end_date / account
# ---------------------------------------------------------------------------

class TestFilters:

    def _seed_two_groups(self, client):
        """March group on BofA, June group on Chase."""
        for _ in range(2):
            _post_tx(client, date="2026-03-02", amount=100.00,
                     description="Rent", account="BofA")
        for _ in range(2):
            _post_tx(client, date="2026-06-15", amount=50.00,
                     description="Gym", account="Chase")

    def test_start_date_filter(self, client):
        self._seed_two_groups(client)
        groups = _get_dupes(client, start_date="2026-04-01")
        assert len(groups) == 1
        assert groups[0]["group_key"] == "2026-06-15|50.00|gym|chase"

    def test_end_date_filter(self, client):
        self._seed_two_groups(client)
        groups = _get_dupes(client, end_date="2026-04-01")
        assert len(groups) == 1
        assert groups[0]["group_key"] == "2026-03-02|100.00|rent|bofa"

    def test_date_range_filter(self, client):
        self._seed_two_groups(client)
        groups = _get_dupes(client, start_date="2026-03-01", end_date="2026-03-31")
        assert len(groups) == 1
        assert groups[0]["date"] == "2026-03-02"

    def test_account_filter(self, client):
        self._seed_two_groups(client)
        groups = _get_dupes(client, account="Chase")
        assert len(groups) == 1
        assert groups[0]["account"] == "Chase"

    def test_account_filter_no_match(self, client):
        self._seed_two_groups(client)
        assert _get_dupes(client, account="Discover") == []


# ---------------------------------------------------------------------------
# Sorting
# ---------------------------------------------------------------------------

class TestSorting:

    def test_groups_sorted_by_total_extra_desc(self, client):
        """Groups are ordered by total_extra descending (biggest waste first)."""
        # Small group: total_extra = 10
        for _ in range(2):
            _post_tx(client, date="2026-03-02", amount=10.00,
                     description="Snack", account="BofA")
        # Big group: total_extra = 500
        for _ in range(2):
            _post_tx(client, date="2026-03-03", amount=500.00,
                     description="Rent", account="Chase")
        # Medium group: total_extra = 2*50 = 100
        for _ in range(3):
            _post_tx(client, date="2026-03-04", amount=50.00,
                     description="Gym", account="Amex")

        groups = _get_dupes(client)
        extras = [g["total_extra"] for g in groups]
        assert extras == [500.00, 100.00, 10.00]
        assert extras == sorted(extras, reverse=True)
