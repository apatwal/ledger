"""
test_plaid_v9.py — v9 Plaid enrichment mapping + the holdings endpoint.

Same discipline as test_plaid.py: the real Plaid network is NEVER touched.
  * The enrichment fields of `map_transaction` are PURE — tested offline with
    plain dicts, no mocks.
  * `GET /api/plaid/holdings` is tested with the Plaid client MOCKED at the
    `plaid_client.get_client` seam (the route did `from .. import plaid_client`,
    so patching `plaid_client.get_client` intercepts the call). Gating reads the
    env directly, so we setenv to configure and delenv to unconfigure.
"""
from __future__ import annotations

import json

import pytest

from src.api import plaid_client
from src.api.plaid_mapping import map_transaction
from src.api.models import PlaidItem


# ═══════════════════════════════════════════════════════════════════════════
# Fakes / helpers
# ═══════════════════════════════════════════════════════════════════════════

class _Resp:
    def __init__(self, data: dict):
        self._data = data

    def to_dict(self) -> dict:
        return dict(self._data)


class FakeHoldingsClient:
    """Fake PlaidApi returning canned investments/holdings/get + securities."""

    def __init__(self, payload: dict):
        self._payload = payload

    def investments_holdings_get(self, req):
        return _Resp(self._payload)


class NoInvestmentsClient:
    """An item without the investments product raises (route skips it)."""

    def investments_holdings_get(self, req):
        raise RuntimeError("PRODUCT_NOT_READY / not supported for this item")


def _seed_item(session, item_id="item-1"):
    item = PlaidItem(
        item_id=item_id,
        access_token="access-sandbox-1",
        institution_name="Test Brokerage",
        accounts_json=json.dumps({"inv1": {"app_account": "My Brokerage"}}),
        status="active",
    )
    session.add(item)
    session.commit()
    session.refresh(item)
    return item


def _configure(monkeypatch):
    monkeypatch.setenv("PLAID_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("PLAID_SECRET", "test-secret")


def _unconfigure(monkeypatch):
    monkeypatch.delenv("PLAID_CLIENT_ID", raising=False)
    monkeypatch.delenv("PLAID_SECRET", raising=False)


def _base_txn(**overrides):
    d = {
        "transaction_id": "tid-1",
        "account_id": "aid-1",
        "amount": 12.5,
        "date": "2026-06-01",
        "name": "SOME MERCHANT",
        "personal_finance_category": {"primary": "FOOD_AND_DRINK"},
    }
    d.update(overrides)
    return d


# ═══════════════════════════════════════════════════════════════════════════
# 1. Enrichment mapping — pure, no mocks
# ═══════════════════════════════════════════════════════════════════════════

class TestEnrichmentMapping:

    def test_emits_all_v9_fields(self):
        m = map_transaction(_base_txn(
            merchant_name="Starbucks",
            logo_url="https://cdn/logo.png",
            pending=True,
            pending_transaction_id="pend-1",
            personal_finance_category_icon_url="https://cdn/icon.png",
        ), "Card")
        assert m["merchant_name"] == "Starbucks"
        assert m["logo_url"] == "https://cdn/logo.png"
        assert m["pending"] is True
        assert m["pending_transaction_id"] == "pend-1"
        assert m["category_icon_url"] == "https://cdn/icon.png"

    def test_logo_url_prefers_own_over_counterparty(self):
        """Own logo_url takes precedence over a counterparty logo."""
        m = map_transaction(_base_txn(
            logo_url="https://cdn/own.png",
            counterparties=[{"logo_url": "https://cdn/cp.png"}],
        ), "Card")
        assert m["logo_url"] == "https://cdn/own.png"

    def test_logo_url_falls_back_to_counterparty(self):
        """No own logo -> first counterparty's logo_url is used (the fallback)."""
        m = map_transaction(_base_txn(
            logo_url=None,
            counterparties=[{"logo_url": "https://cdn/cp.png"}],
        ), "Card")
        assert m["logo_url"] == "https://cdn/cp.png"

    def test_logo_url_none_when_neither_present(self):
        m = map_transaction(_base_txn(logo_url=None), "Card")
        assert m["logo_url"] is None

    def test_logo_url_none_when_counterparties_empty(self):
        m = map_transaction(_base_txn(logo_url=None, counterparties=[]), "Card")
        assert m["logo_url"] is None

    def test_pending_defaults_false(self):
        m = map_transaction(_base_txn(), "Card")
        assert m["pending"] is False
        assert m["pending_transaction_id"] is None
        assert m["category_icon_url"] is None

    def test_merchant_name_none_when_absent(self):
        m = map_transaction(_base_txn(merchant_name=None), "Card")
        assert m["merchant_name"] is None


# ═══════════════════════════════════════════════════════════════════════════
# 2. GET /api/plaid/holdings
# ═══════════════════════════════════════════════════════════════════════════

class TestHoldings:

    def _payload(self):
        return {
            "accounts": [{"account_id": "inv1"}],
            "securities": [
                {
                    "security_id": "sec-AAPL",
                    "name": "Apple Inc",
                    "ticker_symbol": "AAPL",
                    "iso_currency_code": "USD",
                },
            ],
            "holdings": [
                {
                    "account_id": "inv1",
                    "security_id": "sec-AAPL",
                    "quantity": 10,
                    "institution_price": 150.0,
                    "institution_value": 1500.0,
                    "iso_currency_code": "USD",
                },
            ],
        }

    def test_holdings_shape(self, client, test_session, monkeypatch):
        _configure(monkeypatch)
        _seed_item(test_session)
        monkeypatch.setattr(
            plaid_client, "get_client", lambda: FakeHoldingsClient(self._payload())
        )

        resp = client.get("/api/plaid/holdings")
        assert resp.status_code == 200
        holdings = resp.json()
        assert len(holdings) == 1
        h = holdings[0]
        assert h["security_name"] == "Apple Inc"
        assert h["ticker_symbol"] == "AAPL"
        assert h["quantity"] == pytest.approx(10.0)
        assert h["price"] == pytest.approx(150.0)
        assert h["value"] == pytest.approx(1500.0)
        assert h["currency"] == "USD"
        assert h["account"] == "My Brokerage"        # resolved from accounts_json
        assert h["institution"] == "Test Brokerage"

    def test_holdings_503_when_unconfigured(self, client, monkeypatch):
        _unconfigure(monkeypatch)
        assert client.get("/api/plaid/holdings").status_code == 503

    def test_holdings_empty_when_no_investment_accounts(self, client, test_session, monkeypatch):
        """An item whose holdings call raises (no investments product) -> []."""
        _configure(monkeypatch)
        _seed_item(test_session)
        monkeypatch.setattr(plaid_client, "get_client", lambda: NoInvestmentsClient())

        resp = client.get("/api/plaid/holdings")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_holdings_empty_when_no_items(self, client, monkeypatch):
        """Configured but nothing linked -> [] (get_client never even needed)."""
        _configure(monkeypatch)
        monkeypatch.setattr(
            plaid_client, "get_client", lambda: FakeHoldingsClient(self._payload())
        )
        resp = client.get("/api/plaid/holdings")
        assert resp.status_code == 200
        assert resp.json() == []
