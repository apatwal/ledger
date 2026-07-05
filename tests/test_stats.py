"""
test_stats.py — Tests for the statistics and categories endpoints.

Coverage
--------
* GET /api/stats/summary
  - Correct total_income, total_expense, net, savings, savings_rate, count
  - Empty DB returns zeros
  - Date range filtering
  - savings = Savings + Investment categories only
  - savings_rate = savings / total_income (or 0 if no income)

* GET /api/stats/by-category
  - Correct per-category totals and counts
  - pct values sum to ~100 for a given type
  - Descending order by total
  - type filter (default expense, can be income)
  - Empty result for unknown category filter

* GET /api/stats/over-time
  - Default granularity=month produces YYYY-MM period strings
  - granularity=week produces YYYY-Www strings
  - granularity=day produces YYYY-MM-DD strings
  - Results are in ascending order by period
  - Income, expense, net, savings per period are correct
  - Date range filtering

* GET /api/categories
  - Returns a list of strings
  - Contains at least sensible defaults
  - Contains categories that have been added via transactions
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


def _seed_summary_data(client):
    """
    Seed a deterministic set of transactions for summary math verification.

    Jan 2024:
      income  Salary      5000
      expense Rent        1200
      expense Groceries    300
      expense Savings      500   ← counts toward savings
      expense Investment   200   ← counts toward savings

    Expected:
      total_income  = 5000
      total_expense = 2200  (1200+300+500+200)
      net           = 2800  (5000-2200)
      savings       = 700   (500+200)
      savings_rate  = 0.14  (700/5000)
      count         = 5
    """
    _post_tx(client, date="2024-01-15", amount=5000.0, type="income",   category="Salary")
    _post_tx(client, date="2024-01-20", amount=1200.0, type="expense",  category="Rent")
    _post_tx(client, date="2024-01-22", amount=300.0,  type="expense",  category="Groceries")
    _post_tx(client, date="2024-01-25", amount=500.0,  type="expense",  category="Savings")
    _post_tx(client, date="2024-01-28", amount=200.0,  type="expense",  category="Investment")


# ---------------------------------------------------------------------------
# GET /api/stats/summary
# ---------------------------------------------------------------------------

class TestStatsSummary:

    def test_summary_empty_db(self, client):
        """Empty DB → all zeros, count=0."""
        resp = client.get("/api/stats/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_income"] == pytest.approx(0.0)
        assert data["total_expense"] == pytest.approx(0.0)
        assert data["net"] == pytest.approx(0.0)
        assert data["savings"] == pytest.approx(0.0)
        assert data["savings_rate"] == pytest.approx(0.0)
        assert data["count"] == 0

    def test_summary_response_schema(self, client):
        """Response has all required keys."""
        resp = client.get("/api/stats/summary")
        data = resp.json()
        for key in ("total_income", "total_expense", "net", "savings", "savings_rate", "count"):
            assert key in data, f"Missing key: {key}"

    def test_summary_total_income(self, client):
        """total_income is the sum of all income transactions."""
        _seed_summary_data(client)
        data = client.get("/api/stats/summary").json()
        assert data["total_income"] == pytest.approx(5000.0)

    def test_summary_total_expense(self, client):
        """total_expense is the sum of all expense transactions."""
        _seed_summary_data(client)
        data = client.get("/api/stats/summary").json()
        assert data["total_expense"] == pytest.approx(2200.0)

    def test_summary_net(self, client):
        """net = total_income - total_expense."""
        _seed_summary_data(client)
        data = client.get("/api/stats/summary").json()
        assert data["net"] == pytest.approx(5000.0 - 2200.0)

    def test_summary_savings_includes_savings_and_investment(self, client):
        """savings = sum of expense rows in Savings + Investment categories."""
        _seed_summary_data(client)
        data = client.get("/api/stats/summary").json()
        assert data["savings"] == pytest.approx(700.0)  # 500 + 200

    def test_summary_savings_excludes_other_expenses(self, client):
        """Regular expense categories (Rent, Groceries) don't count toward savings."""
        _seed_summary_data(client)
        data = client.get("/api/stats/summary").json()
        # If all expenses counted, savings would be 2200; should be 700
        assert data["savings"] < data["total_expense"]

    def test_summary_savings_rate_math(self, client):
        """savings_rate = savings / total_income."""
        _seed_summary_data(client)
        data = client.get("/api/stats/summary").json()
        expected_rate = 700.0 / 5000.0
        assert data["savings_rate"] == pytest.approx(expected_rate, rel=1e-4)

    def test_summary_savings_rate_no_income(self, client):
        """savings_rate = 0 when total_income = 0 (no division by zero)."""
        _post_tx(client, date="2024-01-01", amount=100.0, type="expense", category="Rent")
        data = client.get("/api/stats/summary").json()
        assert data["savings_rate"] == pytest.approx(0.0)

    def test_summary_count(self, client):
        """count equals total number of transactions in range."""
        _seed_summary_data(client)
        data = client.get("/api/stats/summary").json()
        assert data["count"] == 5

    def test_summary_date_range_start(self, client):
        """start_date filters out older transactions."""
        _seed_summary_data(client)
        # Add a Feb transaction
        _post_tx(client, date="2024-02-01", amount=1000.0, type="income", category="Salary")
        data = client.get("/api/stats/summary?start_date=2024-02-01").json()
        assert data["total_income"] == pytest.approx(1000.0)
        assert data["count"] == 1

    def test_summary_date_range_end(self, client):
        """end_date filters out newer transactions."""
        _seed_summary_data(client)
        _post_tx(client, date="2024-02-01", amount=1000.0, type="income", category="Salary")
        data = client.get("/api/stats/summary?end_date=2024-01-31").json()
        assert data["total_income"] == pytest.approx(5000.0)
        assert data["count"] == 5

    def test_summary_date_range_combined(self, client):
        """Both start_date and end_date together narrow results correctly."""
        _seed_summary_data(client)
        _post_tx(client, date="2024-02-01", amount=1000.0, type="income", category="Salary")
        data = client.get(
            "/api/stats/summary?start_date=2024-01-01&end_date=2024-01-31"
        ).json()
        assert data["count"] == 5

    def test_summary_only_income_no_expenses(self, client):
        """Pure income: net = total_income, savings = 0."""
        _post_tx(client, date="2024-01-01", amount=3000.0, type="income", category="Salary")
        data = client.get("/api/stats/summary").json()
        assert data["total_expense"] == pytest.approx(0.0)
        assert data["net"] == pytest.approx(3000.0)
        assert data["savings"] == pytest.approx(0.0)

    def test_summary_savings_rate_is_fraction_not_percent(self, client):
        """savings_rate is a fraction (0-1), not a percentage (0-100)."""
        _seed_summary_data(client)
        data = client.get("/api/stats/summary").json()
        # 700/5000 = 0.14 — if it were percentage it would be 14
        assert 0.0 <= data["savings_rate"] <= 1.0


# ---------------------------------------------------------------------------
# GET /api/stats/by-category
# ---------------------------------------------------------------------------

class TestStatsByCategory:

    def _seed(self, client):
        """Seed expense data for by-category tests."""
        _post_tx(client, date="2024-01-20", amount=1200.0, type="expense", category="Rent")
        _post_tx(client, date="2024-01-22", amount=300.0,  type="expense", category="Groceries")
        _post_tx(client, date="2024-01-25", amount=300.0,  type="expense", category="Groceries")
        _post_tx(client, date="2024-01-28", amount=200.0,  type="expense", category="Transport")
        _post_tx(client, date="2024-01-15", amount=5000.0, type="income",  category="Salary")

    def test_by_category_status_200(self, client):
        """Endpoint returns 200."""
        resp = client.get("/api/stats/by-category")
        assert resp.status_code == 200

    def test_by_category_response_is_list(self, client):
        """Response is a list."""
        resp = client.get("/api/stats/by-category")
        assert isinstance(resp.json(), list)

    def test_by_category_empty_db(self, client):
        """Empty DB returns empty list."""
        resp = client.get("/api/stats/by-category")
        assert resp.json() == []

    def test_by_category_schema(self, client):
        """Each item has category, total, count, pct."""
        self._seed(client)
        items = client.get("/api/stats/by-category").json()
        for item in items:
            assert "category" in item
            assert "total" in item
            assert "count" in item
            assert "pct" in item

    def test_by_category_default_type_expense(self, client):
        """Default type filter is expense — income categories excluded."""
        self._seed(client)
        items = client.get("/api/stats/by-category").json()
        categories = [i["category"] for i in items]
        assert "Salary" not in categories  # income category

    def test_by_category_totals_correct(self, client):
        """Per-category totals are summed correctly."""
        self._seed(client)
        items = client.get("/api/stats/by-category").json()
        by_cat = {i["category"]: i for i in items}
        assert by_cat["Rent"]["total"] == pytest.approx(1200.0)
        assert by_cat["Groceries"]["total"] == pytest.approx(600.0)  # 300+300
        assert by_cat["Transport"]["total"] == pytest.approx(200.0)

    def test_by_category_counts_correct(self, client):
        """Per-category transaction counts are correct."""
        self._seed(client)
        items = client.get("/api/stats/by-category").json()
        by_cat = {i["category"]: i for i in items}
        assert by_cat["Rent"]["count"] == 1
        assert by_cat["Groceries"]["count"] == 2
        assert by_cat["Transport"]["count"] == 1

    def test_by_category_pct_sums_to_100(self, client):
        """pct values for all expense categories sum to approximately 100."""
        self._seed(client)
        items = client.get("/api/stats/by-category").json()
        total_pct = sum(i["pct"] for i in items)
        assert total_pct == pytest.approx(100.0, abs=0.5)

    def test_by_category_pct_values_range(self, client):
        """Each pct value is between 0 and 100."""
        self._seed(client)
        items = client.get("/api/stats/by-category").json()
        for item in items:
            assert 0.0 <= item["pct"] <= 100.0, f"pct out of range: {item}"

    def test_by_category_descending_order(self, client):
        """Results are ordered descending by total."""
        self._seed(client)
        items = client.get("/api/stats/by-category").json()
        totals = [i["total"] for i in items]
        assert totals == sorted(totals, reverse=True)

    def test_by_category_type_income(self, client):
        """type=income returns income categories only."""
        self._seed(client)
        items = client.get("/api/stats/by-category?type=income").json()
        categories = [i["category"] for i in items]
        assert "Salary" in categories
        # Expense categories should not appear
        assert "Rent" not in categories

    def test_by_category_date_range(self, client):
        """Date range filtering works for by-category."""
        self._seed(client)
        # Add a Feb transaction
        _post_tx(client, date="2024-02-01", amount=800.0, type="expense", category="Rent")
        items = client.get("/api/stats/by-category?start_date=2024-02-01").json()
        by_cat = {i["category"]: i for i in items}
        # Only the Feb Rent row should be in range
        assert "Rent" in by_cat
        assert by_cat["Rent"]["total"] == pytest.approx(800.0)
        assert by_cat["Rent"]["count"] == 1

    def test_by_category_single_category(self, client):
        """With a single category of expenses, pct should be 100."""
        _post_tx(client, date="2024-01-01", amount=500.0, type="expense", category="Rent")
        items = client.get("/api/stats/by-category").json()
        assert len(items) == 1
        assert items[0]["pct"] == pytest.approx(100.0, abs=0.1)


# ---------------------------------------------------------------------------
# GET /api/stats/over-time
# ---------------------------------------------------------------------------

class TestStatsOverTime:

    def _seed_multi_month(self, client):
        """Seed income + expense data across Jan and Feb 2024."""
        # January
        _post_tx(client, date="2024-01-15", amount=5000.0, type="income",  category="Salary")
        _post_tx(client, date="2024-01-20", amount=1200.0, type="expense", category="Rent")
        _post_tx(client, date="2024-01-25", amount=500.0,  type="expense", category="Savings")
        # February
        _post_tx(client, date="2024-02-15", amount=5000.0, type="income",  category="Salary")
        _post_tx(client, date="2024-02-20", amount=1200.0, type="expense", category="Rent")
        _post_tx(client, date="2024-02-22", amount=200.0,  type="expense", category="Investment")

    def test_over_time_status_200(self, client):
        """Endpoint returns 200."""
        resp = client.get("/api/stats/over-time")
        assert resp.status_code == 200

    def test_over_time_empty_db(self, client):
        """Empty DB returns empty list."""
        assert client.get("/api/stats/over-time").json() == []

    def test_over_time_response_is_list(self, client):
        """Response is a list."""
        resp = client.get("/api/stats/over-time")
        assert isinstance(resp.json(), list)

    def test_over_time_schema(self, client):
        """Each item has period, income, expense, net, savings."""
        self._seed_multi_month(client)
        items = client.get("/api/stats/over-time").json()
        for item in items:
            for key in ("period", "income", "expense", "net", "savings"):
                assert key in item, f"Missing key '{key}' in {item}"

    def test_over_time_month_granularity_default(self, client):
        """Default granularity=month → period strings are YYYY-MM."""
        self._seed_multi_month(client)
        items = client.get("/api/stats/over-time").json()
        assert len(items) >= 1
        for item in items:
            # YYYY-MM format: 7 chars, dash at index 4
            assert len(item["period"]) == 7, f"Bad period: {item['period']}"
            assert item["period"][4] == "-"

    def test_over_time_month_granularity_explicit(self, client):
        """granularity=month explicitly → same as default."""
        self._seed_multi_month(client)
        items = client.get("/api/stats/over-time?granularity=month").json()
        for item in items:
            assert len(item["period"]) == 7

    def test_over_time_week_granularity(self, client):
        """granularity=week → period strings contain a week indicator."""
        self._seed_multi_month(client)
        items = client.get("/api/stats/over-time?granularity=week").json()
        assert len(items) >= 1
        # ISO week format: YYYY-Www (e.g. "2024-W03") — 8 chars with 'W'
        for item in items:
            assert "W" in item["period"] or len(item["period"]) == 8, \
                f"Expected week period format, got: {item['period']}"

    def test_over_time_day_granularity(self, client):
        """granularity=day → period strings are YYYY-MM-DD."""
        self._seed_multi_month(client)
        items = client.get("/api/stats/over-time?granularity=day").json()
        assert len(items) >= 1
        for item in items:
            # YYYY-MM-DD: 10 chars
            assert len(item["period"]) == 10, f"Bad day period: {item['period']}"
            assert item["period"].count("-") == 2

    def test_over_time_ascending_order(self, client):
        """Results are in ascending order by period."""
        self._seed_multi_month(client)
        items = client.get("/api/stats/over-time").json()
        periods = [i["period"] for i in items]
        assert periods == sorted(periods), "Expected ascending period order"

    def test_over_time_month_two_periods(self, client):
        """Jan and Feb data produces exactly two monthly periods."""
        self._seed_multi_month(client)
        items = client.get("/api/stats/over-time").json()
        periods = [i["period"] for i in items]
        assert "2024-01" in periods
        assert "2024-02" in periods

    def test_over_time_income_per_period(self, client):
        """Income per period is aggregated correctly."""
        self._seed_multi_month(client)
        items = client.get("/api/stats/over-time").json()
        by_period = {i["period"]: i for i in items}
        assert by_period["2024-01"]["income"] == pytest.approx(5000.0)
        assert by_period["2024-02"]["income"] == pytest.approx(5000.0)

    def test_over_time_expense_per_period(self, client):
        """Expense per period is aggregated correctly."""
        self._seed_multi_month(client)
        items = client.get("/api/stats/over-time").json()
        by_period = {i["period"]: i for i in items}
        # Jan: Rent 1200 + Savings 500 = 1700
        assert by_period["2024-01"]["expense"] == pytest.approx(1700.0)
        # Feb: Rent 1200 + Investment 200 = 1400
        assert by_period["2024-02"]["expense"] == pytest.approx(1400.0)

    def test_over_time_net_per_period(self, client):
        """net = income - expense per period."""
        self._seed_multi_month(client)
        items = client.get("/api/stats/over-time").json()
        by_period = {i["period"]: i for i in items}
        assert by_period["2024-01"]["net"] == pytest.approx(5000.0 - 1700.0)
        assert by_period["2024-02"]["net"] == pytest.approx(5000.0 - 1400.0)

    def test_over_time_savings_per_period(self, client):
        """savings per period = Savings + Investment expenses in that period."""
        self._seed_multi_month(client)
        items = client.get("/api/stats/over-time").json()
        by_period = {i["period"]: i for i in items}
        assert by_period["2024-01"]["savings"] == pytest.approx(500.0)   # Savings only
        assert by_period["2024-02"]["savings"] == pytest.approx(200.0)   # Investment only

    def test_over_time_invalid_granularity(self, client):
        """Invalid granularity → 422."""
        resp = client.get("/api/stats/over-time?granularity=yearly")
        assert resp.status_code == 422

    def test_over_time_date_range_filter(self, client):
        """start_date/end_date restrict which periods appear."""
        self._seed_multi_month(client)
        items = client.get(
            "/api/stats/over-time?start_date=2024-02-01&end_date=2024-02-28"
        ).json()
        periods = [i["period"] for i in items]
        assert "2024-01" not in periods
        assert "2024-02" in periods

    def test_over_time_single_transaction(self, client):
        """Single transaction → one period entry."""
        _post_tx(client, date="2024-03-10", amount=100.0, type="expense", category="Groceries")
        items = client.get("/api/stats/over-time").json()
        assert len(items) == 1
        assert items[0]["period"] == "2024-03"


# ---------------------------------------------------------------------------
# GET /api/stats/over-time?granularity=year  (added when backend gained "year")
# ---------------------------------------------------------------------------

import re  # noqa: E402  (local to the year-granularity tests)


class TestStatsOverTimeYear:
    """granularity=year → period label 'YYYY'. Transfers still excluded."""

    def _seed_multi_year(self, client):
        """Seed income + expense + transfer across 2024 and 2025.

        2024:
          income  Salary      6000
          expense Rent        1200
          expense Savings      400   (savings category)
          transfer Payments   5000   <- excluded from stats
        2025:
          income  Salary      7000
          expense Rent        1500
          expense Investment   600   (savings category)
          transfer Transfer    900   <- excluded from stats

        Expected (transfers excluded):
          2024: income 6000, expense 1600, net 4400, savings 400
          2025: income 7000, expense 2100, net 4900, savings 600
        """
        _post_tx(client, date="2024-01-15", amount=6000.0, type="income",   category="Salary")
        _post_tx(client, date="2024-03-20", amount=1200.0, type="expense",  category="Rent")
        _post_tx(client, date="2024-06-25", amount=400.0,  type="expense",  category="Savings")
        _post_tx(client, date="2024-08-01", amount=5000.0, type="transfer", category="Payments and Credits")
        _post_tx(client, date="2025-02-15", amount=7000.0, type="income",   category="Salary")
        _post_tx(client, date="2025-04-20", amount=1500.0, type="expense",  category="Rent")
        _post_tx(client, date="2025-07-25", amount=600.0,  type="expense",  category="Investment")
        _post_tx(client, date="2025-09-01", amount=900.0,  type="transfer", category="Transfer")

    def test_year_granularity_status_200(self, client):
        """granularity=year is accepted (no longer 422)."""
        self._seed_multi_year(client)
        resp = client.get("/api/stats/over-time?granularity=year")
        assert resp.status_code == 200

    def test_year_period_label_format(self, client):
        """Each period label is a 4-digit year matching ^\\d{4}$."""
        self._seed_multi_year(client)
        items = client.get("/api/stats/over-time?granularity=year").json()
        assert len(items) == 2
        for item in items:
            assert re.match(r"^\d{4}$", item["period"]), f"Bad year period: {item['period']}"

    def test_year_periods_present_and_ascending(self, client):
        """Both years appear, in ascending order."""
        self._seed_multi_year(client)
        items = client.get("/api/stats/over-time?granularity=year").json()
        periods = [i["period"] for i in items]
        assert periods == ["2024", "2025"]
        assert periods == sorted(periods)

    def test_year_income_expense_net_savings_correct(self, client):
        """Per-year income/expense/net/savings are aggregated correctly."""
        self._seed_multi_year(client)
        items = client.get("/api/stats/over-time?granularity=year").json()
        by_year = {i["period"]: i for i in items}
        # 2024
        assert by_year["2024"]["income"] == pytest.approx(6000.0)
        assert by_year["2024"]["expense"] == pytest.approx(1600.0)   # 1200 + 400
        assert by_year["2024"]["net"] == pytest.approx(4400.0)
        assert by_year["2024"]["savings"] == pytest.approx(400.0)
        # 2025
        assert by_year["2025"]["income"] == pytest.approx(7000.0)
        assert by_year["2025"]["expense"] == pytest.approx(2100.0)   # 1500 + 600
        assert by_year["2025"]["net"] == pytest.approx(4900.0)
        assert by_year["2025"]["savings"] == pytest.approx(600.0)

    def test_year_excludes_transfers(self, client):
        """Transfers (5000 in 2024, 900 in 2025) are excluded from yearly stats."""
        self._seed_multi_year(client)
        items = client.get("/api/stats/over-time?granularity=year").json()
        by_year = {i["period"]: i for i in items}
        # If transfers leaked in, expense or income would be inflated.
        assert by_year["2024"]["expense"] == pytest.approx(1600.0)   # not 6600
        assert by_year["2025"]["expense"] == pytest.approx(2100.0)   # not 3000
        # Cross-check with the transfer-excluded summary totals.
        summary = client.get("/api/stats/summary").json()
        assert sum(i["income"] for i in items) == pytest.approx(summary["total_income"])
        assert sum(i["expense"] for i in items) == pytest.approx(summary["total_expense"])

    def test_year_transfer_only_year_absent(self, client):
        """A year containing only transfers does not appear in yearly over-time."""
        _post_tx(client, date="2024-05-01", amount=100.0, type="expense",  category="Groceries")
        _post_tx(client, date="2025-05-01", amount=800.0, type="transfer", category="Transfer")
        items = client.get("/api/stats/over-time?granularity=year").json()
        periods = [i["period"] for i in items]
        assert "2024" in periods
        assert "2025" not in periods   # transfer-only year excluded entirely

    def test_year_schema(self, client):
        """Each yearly item has period, income, expense, net, savings."""
        self._seed_multi_year(client)
        items = client.get("/api/stats/over-time?granularity=year").json()
        for item in items:
            for key in ("period", "income", "expense", "net", "savings"):
                assert key in item


# ---------------------------------------------------------------------------
# GET /api/categories
# ---------------------------------------------------------------------------

class TestCategories:

    def test_categories_status_200(self, client):
        """Endpoint returns 200."""
        resp = client.get("/api/categories")
        assert resp.status_code == 200

    def test_categories_returns_list(self, client):
        """Response is a JSON array."""
        resp = client.get("/api/categories")
        assert isinstance(resp.json(), list)

    def test_categories_items_are_strings(self, client):
        """Every category in the list is a string."""
        resp = client.get("/api/categories")
        for item in resp.json():
            assert isinstance(item, str)

    def test_categories_has_sensible_defaults(self, client):
        """Sensible default categories are present even with empty DB."""
        resp = client.get("/api/categories")
        categories = resp.json()
        # At least some defaults from the contract (Salary, Rent, Groceries, etc.)
        expected_defaults = {"Salary", "Rent", "Groceries", "Investment", "Savings"}
        found = expected_defaults & set(categories)
        assert len(found) > 0, (
            f"Expected at least one default category from {expected_defaults}, "
            f"got: {categories}"
        )

    def test_categories_includes_user_created(self, client):
        """After adding a transaction, its category appears in the list."""
        _post_tx(client, date="2024-01-01", amount=100.0, type="expense", category="PetCare")
        categories = client.get("/api/categories").json()
        assert "PetCare" in categories

    def test_categories_no_duplicates(self, client):
        """Category list contains no duplicate entries."""
        # Add two transactions with same category
        _post_tx(client, date="2024-01-01", amount=100.0, type="expense", category="Groceries")
        _post_tx(client, date="2024-01-02", amount=200.0, type="expense", category="Groceries")
        categories = client.get("/api/categories").json()
        assert len(categories) == len(set(categories)), "Duplicate categories found"

    def test_categories_not_empty(self, client):
        """Category list always has at least the default entries."""
        categories = client.get("/api/categories").json()
        assert len(categories) > 0
