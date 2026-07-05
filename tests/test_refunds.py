"""
test_refunds.py — v5.4 refund netting + P2P pass-through.

Refund (4th type: income|expense|transfer|refund):
  - NETS against its category's spend (treated as a negative expense).
  - summary.total_expense = Σ(expense) − Σ(refund); net = income − (expense − refund).
  - savings / total_income unaffected; refunds INCLUDED in count.
  - by-category: category total = Σ(expense) − Σ(refund in cat); over-time subtracts too.
  - CSV Type-column `return`/`refund`/`reversal` → refund (not transfer).

P2P pass-through (Venmo/Zelle/Cash App):
  - DEFAULT to type=transfer (excluded) + needs_review, review_reason starting
    "Assumed pass-through transfer", UNLESS a Type column or user rule dictates.
"""

from __future__ import annotations

import io
import pytest

from .conftest import fixture_csv_file


def _post(client, **kwargs):
    defaults = {"source": "manual"}
    defaults.update(kwargs)
    r = client.post("/api/transactions", json=defaults)
    assert r.status_code == 201, r.text
    return r.json()


def _csv(content, statement_type=None, filename="r.csv"):
    data = {"statement_type": statement_type} if statement_type else None
    return dict(files={"file": (filename, io.BytesIO(content.encode()), "text/csv")}, data=data)


# ---------------------------------------------------------------------------
# refund type accepted + netting math
# ---------------------------------------------------------------------------

class TestRefundNetting:

    def test_create_refund_type(self, client):
        """`refund` is a valid transaction type (v5.4 enum)."""
        tx = _post(client, date="2026-01-01", amount=30.0, type="refund", category="Shopping")
        assert tx["type"] == "refund"

    def test_refund_nets_category_spend(self, client):
        """expense $100 + refund $30 (same cat) → by-category total 70, count 2."""
        _post(client, date="2026-01-01", amount=100.0, type="expense", category="Shopping")
        _post(client, date="2026-01-05", amount=30.0, type="refund", category="Shopping")
        cats = {c["category"]: c for c in client.get("/api/stats/by-category").json()}
        assert cats["Shopping"]["total"] == pytest.approx(70.0)   # 100 − 30
        assert cats["Shopping"]["count"] == 2                     # both rows counted

    def test_summary_total_expense_reflects_refund(self, client):
        """summary.total_expense = Σexpense − Σrefund; refund included in count."""
        _post(client, date="2026-01-01", amount=100.0, type="expense", category="Shopping")
        _post(client, date="2026-01-05", amount=30.0, type="refund", category="Shopping")
        s = client.get("/api/stats/summary").json()
        assert s["total_expense"] == pytest.approx(70.0)   # 100 − 30
        assert s["count"] == 2                              # refund included

    def test_refund_does_not_touch_income_or_savings(self, client):
        """A refund is NOT income and does not change savings/savings_rate."""
        _post(client, date="2026-01-01", amount=2000.0, type="income", category="Salary")
        _post(client, date="2026-01-02", amount=500.0, type="expense", category="Savings")
        before = client.get("/api/stats/summary").json()
        _post(client, date="2026-01-05", amount=40.0, type="refund", category="Shopping")
        after = client.get("/api/stats/summary").json()
        assert after["total_income"] == pytest.approx(before["total_income"])  # unchanged
        assert after["savings"] == pytest.approx(before["savings"])            # unchanged
        assert after["savings_rate"] == pytest.approx(before["savings_rate"])
        # expense dropped by the refund
        assert after["total_expense"] == pytest.approx(before["total_expense"] - 40.0)

    def test_refund_net_of_computed_correctly(self, client):
        """net = income − (expense − refund)."""
        _post(client, date="2026-01-01", amount=1000.0, type="income", category="Salary")
        _post(client, date="2026-01-02", amount=300.0, type="expense", category="Dining")
        _post(client, date="2026-01-03", amount=50.0, type="refund", category="Dining")
        s = client.get("/api/stats/summary").json()
        assert s["net"] == pytest.approx(1000.0 - (300.0 - 50.0))   # 750

    def test_refund_reduces_over_time_expense(self, client):
        """over-time: a period's expense is reduced by refunds in that period."""
        _post(client, date="2026-03-10", amount=200.0, type="expense", category="Shopping")
        _post(client, date="2026-03-15", amount=80.0, type="refund", category="Shopping")
        ot = {p["period"]: p for p in client.get("/api/stats/over-time").json()}
        assert ot["2026-03"]["expense"] == pytest.approx(120.0)   # 200 − 80

    def test_refund_reduces_by_account_expense(self, client):
        """by-account: a card's expense nets refunds on that card."""
        _post(client, date="2026-01-01", amount=150.0, type="expense", category="Shopping", account="Amex")
        _post(client, date="2026-01-05", amount=25.0, type="refund", category="Shopping", account="Amex")
        by = {b["account"]: b for b in client.get("/api/stats/by-account").json()}
        assert by["Amex"]["expense"] == pytest.approx(125.0)   # 150 − 25
        assert by["Amex"]["count"] == 2

    def test_refund_can_exceed_and_go_negative(self, client):
        """A category with more refund than spend nets negative (allowed)."""
        _post(client, date="2026-01-01", amount=20.0, type="expense", category="Returns")
        _post(client, date="2026-01-02", amount=50.0, type="refund", category="Returns")
        cats = {c["category"]: c for c in client.get("/api/stats/by-category").json()}
        assert cats["Returns"]["total"] == pytest.approx(-30.0)


# ---------------------------------------------------------------------------
# CSV Type-column → refund
# ---------------------------------------------------------------------------

class TestCSVRefundMapping:

    def test_return_refund_reversal_map_to_refund(self, client):
        """Type-column values return/refund/reversal import as `refund` (not transfer)."""
        content = (
            "Date,Description,Amount,Type\n"
            "2026-02-01,STORE A,50.00,Return\n"
            "2026-02-02,STORE B,20.00,Refund\n"
            "2026-02-03,STORE C,10.00,Reversal\n"
        )
        resp = client.post("/api/transactions/csv", **_csv(content))
        assert resp.status_code == 200
        types = [t["type"] for t in client.get("/api/transactions?limit=10").json()]
        assert types.count("refund") == 3
        assert "transfer" not in types

    def test_chase_import_yields_11_refunds(self, client):
        """v5.4: the Chase fixture's 11 `Return` rows import as refunds."""
        resp = client.post("/api/transactions/csv", files=fixture_csv_file("chase_sample.csv"),
                           data={"account": "Chase"})
        assert resp.status_code == 200
        refunds = [t for t in client.get("/api/transactions?account=Chase&limit=1000").json()
                   if t["type"] == "refund"]
        assert len(refunds) == 11

    def test_labeled_refund_row_nets(self, client):
        """A clearly-labeled refund row nets against its category via CSV."""
        content = (
            "Date,Description,Amount,Type,Category\n"
            "2026-02-01,APPLE.COM/BILL SUBSCRIPTION,50.00,Sale,Software\n"
            "2026-02-05,APPLE.COM/BILL REFUND,15.00,Refund,Software\n"
        )
        client.post("/api/transactions/csv", **_csv(content))
        cats = {c["category"]: c for c in client.get("/api/stats/by-category").json()}
        assert cats["Software"]["total"] == pytest.approx(35.0)   # 50 − 15


# ---------------------------------------------------------------------------
# P2P pass-through defaults to transfer + review
# ---------------------------------------------------------------------------

class TestP2PPassThrough:

    def test_venmo_zelle_cashapp_default_transfer(self, client):
        """Venmo/Zelle/Cash App default to transfer + needs_review (bank mode)."""
        content = (
            "Date,Description,Amount\n"
            "2026-01-01,VENMO PAYMENT TO SAM,-40.00\n"
            "2026-01-02,ZELLE TO LANDLORD,-900.00\n"
            "2026-01-03,CASH APP CASHOUT,25.00\n"
        )
        resp = client.post("/api/transactions/csv", **_csv(content, statement_type="bank"))
        assert resp.status_code == 200
        data = resp.json()
        # all three default to transfer (excluded), and all flagged for review
        assert data["transfers"] == 3
        txs = client.get("/api/transactions?limit=10").json()
        assert all(t["type"] == "transfer" for t in txs)
        assert all(t["needs_review"] is True for t in txs)

    def test_pass_through_review_reason(self, client):
        """review_reason starts with 'Assumed pass-through transfer'."""
        content = "Date,Description,Amount\n2026-01-01,VENMO TO SAM,-40.00\n"
        client.post("/api/transactions/csv", **_csv(content, statement_type="bank"))
        tx = client.get("/api/transactions").json()[0]
        assert tx["review_reason"].startswith("Assumed pass-through transfer")

    def test_pass_through_excluded_from_income_and_expense(self, client):
        """P2P transfers don't appear as income or expense in the summary."""
        content = (
            "Date,Description,Amount\n"
            "2026-01-01,VENMO CASHOUT,300.00\n"     # would be +income by bank sign
            "2026-01-02,ZELLE TO BOB,-120.00\n"      # would be −expense by bank sign
        )
        client.post("/api/transactions/csv", **_csv(content, statement_type="bank"))
        s = client.get("/api/stats/summary").json()
        assert s["total_income"] == pytest.approx(0.0)
        assert s["total_expense"] == pytest.approx(0.0)
        assert s["count"] == 0        # both excluded (transfers)

    def test_explicit_type_column_overrides_p2p(self, client):
        """An explicit recognized Type value beats the P2P transfer default."""
        content = "Date,Description,Amount,Type\n2026-01-01,VENMO PAYMENT,40.00,SALE\n"
        client.post("/api/transactions/csv", **_csv(content, statement_type="bank"))
        tx = client.get("/api/transactions").json()[0]
        assert tx["type"] == "expense"     # Type=SALE → expense, not transfer

    def test_user_rule_overrides_p2p(self, client):
        """A matching user rule beats the P2P default and clears review."""
        client.post("/api/rules", json={
            "match_field": "description", "match_op": "contains", "match_value": "VENMO",
            "set_type": "expense", "set_category": "Reimbursed", "priority": 5})
        content = "Date,Description,Amount\n2026-01-01,VENMO TO FRIEND,55.00\n"
        client.post("/api/transactions/csv", **_csv(content, statement_type="bank"))
        tx = client.get("/api/transactions").json()[0]
        assert tx["type"] == "expense"
        assert tx["category"] == "Reimbursed"
        assert tx["needs_review"] is False
