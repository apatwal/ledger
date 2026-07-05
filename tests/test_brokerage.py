"""
test_brokerage.py — v5.1 brokerage savings-vs-transfer prompt.

A brokerage/investment deposit from checking is ambiguous: it could count toward
the savings rate (modeled as expense in category Investment/Savings) OR be a
neutral transfer (excluded from everything). The importer detects it, defaults
SAFE (transfer), and flags needs_review with a distinct "Brokerage:" reason so
the user decides once per institution — the choice becomes a rule.

Reference row: `ROBINHOOD BROKERAGE DEPOSIT` (500.00) in debit_sample.csv.
"""

from __future__ import annotations

import io
import pytest

from .conftest import fixture_csv_file

ROBINHOOD = "ROBINHOOD BROKERAGE DEPOSIT"
ROBINHOOD_AMOUNT = 500.0


def _import_debit(client):
    resp = client.post("/api/transactions/csv", files=fixture_csv_file("debit_sample.csv"))
    assert resp.status_code == 200
    return resp.json()


def _get_robinhood(client):
    txs = client.get("/api/transactions?limit=100").json()
    return next(t for t in txs if t["description"] == ROBINHOOD)


def _rule(client, **actions):
    body = {
        "match_field": "description",
        "match_op": "contains",
        "match_value": "ROBINHOOD",
        "priority": 10,
    }
    body.update(actions)
    r = client.post("/api/rules", json=body)
    assert r.status_code == 201, r.text
    return r.json()


# ---------------------------------------------------------------------------
# Default (no rules): detected, defaulted to transfer, flagged for review
# ---------------------------------------------------------------------------

class TestBrokerageDefault:

    def test_default_transfer_and_flagged(self, client):
        """No rules → ROBINHOOD row is transfer, needs_review, 'Brokerage:' reason."""
        _import_debit(client)
        rob = _get_robinhood(client)
        assert rob["type"] == "transfer"
        assert rob["needs_review"] is True
        assert rob["review_reason"].startswith("Brokerage:")
        assert "robinhood" in rob["review_reason"].lower()

    def test_default_excluded_from_savings(self, client):
        """As a transfer, the brokerage deposit does not count toward savings."""
        _import_debit(client)
        s = client.get("/api/stats/summary").json()
        assert s["savings"] == pytest.approx(0.0)
        assert s["savings_rate"] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# "Savings" choice: rule -> expense/Investment; savings + savings_rate rise
# ---------------------------------------------------------------------------

class TestBrokerageSavingsChoice:

    def test_savings_choice_reclassifies_and_clears_review(self, client):
        """A ROBINHOOD → expense/Investment rule + apply makes the row count as savings."""
        _import_debit(client)
        before = client.get("/api/stats/summary").json()
        assert before["savings"] == pytest.approx(0.0)

        _rule(client, set_type="expense", set_category="Investment")
        applied = client.post("/api/rules/apply", json={}).json()
        assert applied["updated"] == 1

        rob = _get_robinhood(client)
        assert rob["type"] == "expense"
        assert rob["category"] == "Investment"
        assert rob["needs_review"] is False
        assert rob["review_reason"] is None

    def test_savings_choice_increases_savings_and_rate(self, client):
        """savings and savings_rate increase by the brokerage amount after the rule."""
        _import_debit(client)
        before = client.get("/api/stats/summary").json()

        _rule(client, set_type="expense", set_category="Investment")
        client.post("/api/rules/apply", json={})
        after = client.get("/api/stats/summary").json()

        # savings rises by exactly the ROBINHOOD amount
        assert after["savings"] == pytest.approx(before["savings"] + ROBINHOOD_AMOUNT)
        # savings_rate = savings / total_income (income unchanged)
        assert after["total_income"] == pytest.approx(before["total_income"])
        expected_rate = round(after["savings"] / after["total_income"], 4)
        assert after["savings_rate"] == pytest.approx(expected_rate, abs=1e-4)
        assert after["savings_rate"] > before["savings_rate"]

    def test_savings_choice_shows_in_by_category(self, client):
        """After the savings choice, Investment appears in the expense breakdown."""
        _import_debit(client)
        _rule(client, set_type="expense", set_category="Investment")
        client.post("/api/rules/apply", json={})
        cats = {c["category"]: c for c in client.get("/api/stats/by-category").json()}
        assert "Investment" in cats
        assert cats["Investment"]["total"] == pytest.approx(ROBINHOOD_AMOUNT)


# ---------------------------------------------------------------------------
# "Transfer" choice: rule -> transfer; stays excluded from savings
# ---------------------------------------------------------------------------

class TestBrokerageTransferChoice:

    def test_transfer_choice_stays_transfer_excluded(self, client):
        """A ROBINHOOD → transfer rule keeps it a transfer, excluded from savings."""
        _import_debit(client)
        _rule(client, set_type="transfer")
        applied = client.post("/api/rules/apply", json={}).json()
        # the rule matched and cleared needs_review (type already transfer)
        assert applied["updated"] == 1

        rob = _get_robinhood(client)
        assert rob["type"] == "transfer"
        assert rob["needs_review"] is False   # decision recorded

        s = client.get("/api/stats/summary").json()
        assert s["savings"] == pytest.approx(0.0)         # still excluded
        assert s["savings_rate"] == pytest.approx(0.0)
        # and it doesn't appear in the expense breakdown
        cats = {c["category"] for c in client.get("/api/stats/by-category").json()}
        assert "Investment" not in cats


# ---------------------------------------------------------------------------
# Rule present at import time → not re-flagged (rule overrides detection)
# ---------------------------------------------------------------------------

class TestBrokerageRuleOverridesImport:

    def test_reimport_with_rule_not_flagged(self, client):
        """With a matching rule present, re-import does NOT re-flag the ROBINHOOD row.

        needs_review total drops by 1 (7 → 6) because the rule resolves the
        brokerage row before the needs-review heuristic runs.
        """
        first = _import_debit(client)
        assert first["needs_review"] == 7

        # User makes a choice → rule created (transfer, but either choice overrides).
        _rule(client, set_type="transfer")

        # Fresh isolated import of the same file WITH the rule in place.
        second = _import_debit(client)
        assert second["needs_review"] == 6   # ROBINHOOD no longer flagged

        # None of the newly-imported ROBINHOOD rows are flagged.
        robs = [t for t in client.get("/api/transactions?limit=200").json()
                if t["description"] == ROBINHOOD]
        # two imports → two ROBINHOOD rows; the one from the 2nd import isn't flagged
        assert any(r["needs_review"] is False for r in robs)

    def test_rule_at_import_sets_type_directly(self, client):
        """A savings rule present at import time classifies ROBINHOOD without review."""
        _rule(client, set_type="expense", set_category="Investment")
        result = _import_debit(client)
        # brokerage row resolved by the rule → only the 6 ambiguous rows flagged
        assert result["needs_review"] == 6
        rob = _get_robinhood(client)
        assert rob["type"] == "expense"
        assert rob["category"] == "Investment"
        assert rob["needs_review"] is False
