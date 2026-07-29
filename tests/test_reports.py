"""
test_reports.py — Monthly spend recap (v12).

Seeds a month of transactions, POSTs /api/reports/monthly (auth disabled in
tests), monkeypatches the ntfy send to a recorder, and asserts the computed
figures + the recap message body. Also covers the default-previous-month path.
"""

from __future__ import annotations

from datetime import date, datetime
from email.header import make_header, decode_header

import pytest

from src.api import notify


def _title(headers: dict) -> str:
    """The recap Title contains an emoji, so it goes out as an RFC 2047
    encoded-word (raw non-ASCII headers are unsendable — see notify._encode_header).
    Decode it back to compare against the human-readable title."""
    return str(make_header(decode_header(headers["Title"])))


class _Recorder:
    """Captures notify._http_post calls so no real network is touched."""

    def __init__(self):
        self.calls = []

    def __call__(self, url, body, headers):
        self.calls.append({"url": url, "body": body, "headers": headers})


def _seed_month(client, ym: str):
    """Seed one month (YYYY-MM) of transactions across a few categories, plus a
    Savings-category expense and an Investment-category expense."""
    rows = [
        # income
        {"date": f"{ym}-01", "amount": 5000.00, "type": "income", "category": "Salary", "description": "Payroll"},
        # spending categories
        {"date": f"{ym}-03", "amount": 520.00, "type": "expense", "category": "Food & Drink", "description": "Groceries"},
        {"date": f"{ym}-05", "amount": 410.00, "type": "expense", "category": "Shopping", "description": "Clothes"},
        {"date": f"{ym}-07", "amount": 900.00, "type": "expense", "category": "Bills & Utilities", "description": "Rent"},
        # savings + investment (must be excluded from "top spending")
        {"date": f"{ym}-10", "amount": 600.00, "type": "expense", "category": "Savings", "description": "To savings"},
        {"date": f"{ym}-12", "amount": 500.00, "type": "expense", "category": "Investment", "description": "ETF"},
        # a transfer (excluded everywhere) and a Payments & Credits row (excluded from top)
        {"date": f"{ym}-14", "amount": 1000.00, "type": "transfer", "category": "Transfer", "description": "Move money"},
        {"date": f"{ym}-15", "amount": 50.00, "type": "expense", "category": "Payments & Credits", "description": "CC payment"},
    ]
    for r in rows:
        resp = client.post("/api/transactions", json=r)
        assert resp.status_code == 201, resp.text


def _post_report(client, monkeypatch, params=None):
    monkeypatch.setenv("NTFY_TOPIC", "recap")
    rec = _Recorder()
    monkeypatch.setattr(notify, "_http_post", rec)
    resp = client.post("/api/reports/monthly", params=params or {})
    assert resp.status_code == 200, resp.text
    return resp.json(), rec


class TestMonthlyReport:

    def test_computed_figures_and_message(self, client, monkeypatch):
        ym = "2026-06"
        _seed_month(client, ym)
        data, rec = _post_report(client, monkeypatch, {"month": ym})

        assert data["month"] == ym
        assert data["sent"] is True
        assert data["income"] == pytest.approx(5000.00)
        # spent = net expense = 520+410+900+600+500+50 = 2980 (transfer excluded)
        assert data["spent"] == pytest.approx(2980.00)
        assert data["saved"] == pytest.approx(600.00)
        assert data["invested"] == pytest.approx(500.00)
        # savings = saved + invested = 1100; rate = 1100/5000 = 0.22
        assert data["savings_rate"] == pytest.approx(0.22)
        # net = income - spent = 5000 - 2980 = 2020
        assert data["net"] == pytest.approx(2020.00)

        # Top categories EXCLUDE Savings / Investment / Transfer / Payments & Credits.
        cats = [c["category"] for c in data["categories"]]
        assert "Savings" not in cats
        assert "Investment" not in cats
        assert "Transfer" not in cats
        assert "Payments & Credits" not in cats
        # Ordered by amount desc.
        assert cats == ["Bills & Utilities", "Food & Drink", "Shopping"]
        assert data["categories"][0]["amount"] == pytest.approx(900.00)

        # ntfy message body: month name + headline figures + top spending lines.
        assert len(rec.calls) == 1
        headers = rec.calls[0]["headers"]
        assert _title(headers) == "📊 June 2026 recap"
        headers["Title"].encode("latin-1")  # must be sendable as an HTTP header
        body = rec.calls[0]["body"].decode("utf-8")
        assert "Income $5,000 · Spent $2,980" in body
        assert "Saved $600 (22%) · Invested $500" in body
        assert "Top spending:" in body
        assert "• Bills & Utilities $900" in body
        assert "• Food & Drink $520" in body
        assert "• Shopping $410" in body

    def test_top_categories_capped_at_six(self, client, monkeypatch):
        ym = "2026-05"
        for i in range(8):
            resp = client.post("/api/transactions", json={
                "date": f"{ym}-0{(i % 9) + 1}",
                "amount": 100.00 + i,
                "type": "expense",
                "category": f"Cat{i}",
                "description": "x",
            })
            assert resp.status_code == 201, resp.text
        data, _ = _post_report(client, monkeypatch, {"month": ym})
        assert len(data["categories"]) == 6

    def test_default_is_previous_month(self, client, monkeypatch):
        """With no month param, the recap covers the PREVIOUS calendar month."""
        today = datetime.utcnow().date()
        if today.month == 1:
            py, pm = today.year - 1, 12
        else:
            py, pm = today.year, today.month - 1
        ym = f"{py:04d}-{pm:02d}"

        _seed_month(client, ym)
        data, rec = _post_report(client, monkeypatch, None)  # no month param

        assert data["month"] == ym
        assert data["income"] == pytest.approx(5000.00)
        assert data["spent"] == pytest.approx(2980.00)
        month_name = date(py, pm, 1).strftime("%B %Y")
        assert _title(rec.calls[0]["headers"]) == f"📊 {month_name} recap"

    def test_empty_month_zeros(self, client, monkeypatch):
        """A month with no data → zeros, empty categories, sent still True."""
        data, rec = _post_report(client, monkeypatch, {"month": "2025-01"})
        assert data["income"] == 0.0
        assert data["spent"] == 0.0
        assert data["savings_rate"] == 0.0
        assert data["categories"] == []
        assert data["sent"] is True

    def test_invalid_month_422(self, client, monkeypatch):
        monkeypatch.setenv("NTFY_TOPIC", "recap")
        monkeypatch.setattr(notify, "_http_post", _Recorder())
        resp = client.post("/api/reports/monthly", params={"month": "not-a-month"})
        assert resp.status_code == 422, resp.text


class TestReportAuth:

    def test_sync_token_mismatch_401(self, client, monkeypatch):
        """When PLAID_SYNC_TOKEN is set, a missing/mismatched header → 401 even
        though Clerk auth is disabled in tests (endpoint-level check)."""
        monkeypatch.setenv("PLAID_SYNC_TOKEN", "s3cret")
        resp = client.post("/api/reports/monthly", params={"month": "2026-06"})
        assert resp.status_code == 401, resp.text

    def test_sync_token_match_ok(self, client, monkeypatch):
        monkeypatch.setenv("PLAID_SYNC_TOKEN", "s3cret")
        monkeypatch.setenv("NTFY_TOPIC", "recap")
        monkeypatch.setattr(notify, "_http_post", _Recorder())
        resp = client.post(
            "/api/reports/monthly",
            params={"month": "2026-06"},
            headers={"X-Plaid-Sync-Token": "s3cret"},
        )
        assert resp.status_code == 200, resp.text
