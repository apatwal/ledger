"""
test_plaid.py — Plaid integration (v8).

Same philosophy as test_assistant.py: the real Plaid network is NEVER touched.
What we lock down is:
  * the PURE mapping layer (plaid_mapping) — offline, no mocks
  * graceful gating (503 with no PLAID_CLIENT_ID/PLAID_SECRET, like the AI layer)
  * that Plaid `transfer` rows stay OUT of /api/stats/summary (the credit-card
    double-count fix) once mapped into the DB
  * the sync / exchange / delete flows with the Plaid client MOCKED at the
    `plaid_client.get_client` seam — the code still builds the real request
    models (they construct fine offline), but the returned "client" is a fake
    whose methods yield canned `.to_dict()` responses. No HTTP, no real client.

Mocking seam
------------
Every network-touching route + the shared worker call `plaid_client.get_client()`
(referenced as a module attribute, since plaid_routes did `from .. import
plaid_client`). Patching `plaid_client.get_client` therefore intercepts every
Plaid call in one place — we never patch `requests`/`urllib` or the SDK internals.
`is_configured()` reads the env directly, so we `monkeypatch.setenv` the keys to
flip gating on and `monkeypatch.delenv` to flip it off.
"""
from __future__ import annotations

import json
from datetime import date

import pytest

from src.api import plaid_client
from src.api.plaid_mapping import map_transaction, map_investment_transaction
from src.api.models import PlaidItem, Transaction
from src.api.routes.plaid_routes import sync_items


# ═══════════════════════════════════════════════════════════════════════════
# Fakes — stand in for a configured plaid_api.PlaidApi. Only the methods the
# code actually calls are implemented; anything else raises (mirrors a real
# item lacking a product, which sync_items swallows best-effort).
# ═══════════════════════════════════════════════════════════════════════════

class _Resp:
    """Wraps a dict so `.to_dict()` returns it — the shape the routes consume."""

    def __init__(self, data: dict):
        self._data = data

    def to_dict(self) -> dict:
        return dict(self._data)


class FakeSyncClient:
    """Fake PlaidApi for /transactions/sync. `pages` are returned in order (the
    last page is reused if called again, e.g. a re-sync with the saved cursor).
    Investments raise so sync_items' best-effort try/except skips them."""

    def __init__(self, pages: list[dict]):
        self._pages = list(pages)
        self._i = 0

    def transactions_sync(self, req):
        page = self._pages[min(self._i, len(self._pages) - 1)]
        self._i += 1
        return _Resp(page)

    def investments_transactions_get(self, req):
        raise RuntimeError("investments product not available for this item")


class _ExchangeResp:
    def __init__(self, access_token: str, item_id: str):
        self.access_token = access_token
        self.item_id = item_id


class FakeExchangeClient:
    """Fake PlaidApi for the /exchange flow: token exchange + accounts/get +
    institutions/get_by_id, all canned."""

    def item_public_token_exchange(self, req):
        return _ExchangeResp("access-sandbox-SECRET-do-not-leak", "item-abc-123")

    def accounts_get(self, req):
        return _Resp({
            "item": {"institution_id": "ins_test"},
            "accounts": [
                {
                    "account_id": "acc_1",
                    "name": "Everyday Checking",
                    "mask": "0000",
                    "type": "depository",
                    "subtype": "checking",
                },
            ],
        })

    def institutions_get_by_id(self, req):
        return _Resp({"institution": {"name": "Test Bank"}})


# ── Helpers ──────────────────────────────────────────────────────────────────

def _plaid_txn(
    pfc,
    amount,
    txn_id="txn-1",
    account_id="a1",
    name="Merchant",
    merchant_name=None,
    date_="2026-06-01",
):
    """Build a dict shaped like a Plaid /transactions/sync `added` entry.

    pfc=None omits personal_finance_category entirely; pfc="" via {"primary": ""}
    is handled by the caller.
    """
    d = {
        "transaction_id": txn_id,
        "account_id": account_id,
        "amount": amount,
        "date": date_,
        "name": name,
        "merchant_name": merchant_name,
    }
    if pfc is not None:
        d["personal_finance_category"] = {"primary": pfc}
    return d


def _sync_page(added=None, modified=None, removed=None, cursor="c1", has_more=False):
    return {
        "added": added or [],
        "modified": modified or [],
        "removed": removed or [],
        "next_cursor": cursor,
        "has_more": has_more,
    }


def _seed_item(session, item_id="item-1", access_token="access-sandbox-1"):
    """Insert a PlaidItem with one labelled account, visible to the app via the
    shared in-memory engine."""
    item = PlaidItem(
        item_id=item_id,
        access_token=access_token,
        institution_name="Test Bank",
        accounts_json=json.dumps({"a1": {"app_account": "My Checking"}}),
        status="active",
    )
    session.add(item)
    session.commit()
    session.refresh(item)
    return item


def _configure(monkeypatch):
    """Flip is_configured() -> True."""
    monkeypatch.setenv("PLAID_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("PLAID_SECRET", "test-secret")


# ═══════════════════════════════════════════════════════════════════════════
# 1. Pure mapping — no mocks, no DB, no network
# ═══════════════════════════════════════════════════════════════════════════

class TestMapTransaction:

    def test_income_row(self):
        m = map_transaction(_plaid_txn("INCOME", -2500.0, name="ACME PAYROLL"), "Checking")
        assert m["type"] == "income"
        assert m["category"] == "Income"
        assert m["amount"] == pytest.approx(2500.0)  # abs of plaid amount

    def test_food_and_drink_positive_is_expense(self):
        m = map_transaction(
            _plaid_txn("FOOD_AND_DRINK", 5.75, name="SBUX", merchant_name="Starbucks"),
            "Card",
        )
        assert m["type"] == "expense"
        assert m["category"] == "Food & Drink"
        assert m["description"] == "Starbucks"   # merchant_name preferred over name
        assert m["amount"] == pytest.approx(5.75)
        assert m["account"] == "Card"

    def test_loan_payment_is_transfer(self):
        """LOAN_PAYMENTS -> transfer keeps credit-card payments out of stats
        (the double-count fix)."""
        m = map_transaction(_plaid_txn("LOAN_PAYMENTS", 400.0, name="AUTOPAY CC"), "Card")
        assert m["type"] == "transfer"
        assert m["category"] == "Payments & Credits"

    def test_negative_general_merchandise_is_refund(self):
        m = map_transaction(_plaid_txn("GENERAL_MERCHANDISE", -30.0, name="AMZN REFUND"), "Card")
        assert m["type"] == "refund"
        assert m["category"] == "Shopping"
        assert m["amount"] == pytest.approx(30.0)  # stored positive

    def test_amount_is_absolute_value(self):
        assert map_transaction(_plaid_txn("FOOD_AND_DRINK", -12.5), "x")["amount"] == pytest.approx(12.5)
        assert map_transaction(_plaid_txn("FOOD_AND_DRINK", 12.5), "x")["amount"] == pytest.approx(12.5)

    def test_unknown_pfc_is_uncategorized(self):
        m = map_transaction(_plaid_txn("SOME_NEW_CATEGORY", 10.0), "x")
        assert m["category"] == "Uncategorized"
        assert m["type"] == "expense"  # positive, non-income, non-transfer

    def test_missing_pfc_handled(self):
        """personal_finance_category absent entirely -> Uncategorized, no crash."""
        m = map_transaction(_plaid_txn(None, 10.0), "x")
        assert m["category"] == "Uncategorized"
        assert m["type"] == "expense"

    def test_none_primary_handled(self):
        """personal_finance_category present but primary None -> Uncategorized."""
        txn = _plaid_txn("FOOD_AND_DRINK", 10.0)
        txn["personal_finance_category"] = {"primary": None}
        m = map_transaction(txn, "x")
        assert m["category"] == "Uncategorized"

    def test_ids_and_date_carried_through(self):
        m = map_transaction(
            _plaid_txn("FOOD_AND_DRINK", 3.0, txn_id="tid-9", account_id="aid-9", date_="2026-02-03"),
            "x",
        )
        assert m["plaid_transaction_id"] == "tid-9"
        assert m["plaid_account_id"] == "aid-9"
        assert str(m["date"]) == "2026-02-03"


class TestMapInvestmentTransaction:

    def _inv(self, type_, subtype="", amount=100.0, name="Trade", inv_id="inv-1", account_id="a1"):
        """Real Plaid investment transactions always carry BOTH type and subtype.
        The new mapping decides primarily from `type`, refining only `cash` rows
        by `subtype` (and defaulting to `transfer` when `type` is absent)."""
        return {
            "investment_transaction_id": inv_id,
            "account_id": account_id,
            "amount": amount,
            "date": "2026-05-01",
            "name": name,
            "type": type_,
            "subtype": subtype,
        }

    def test_buy_is_neutralized_transfer(self):
        """Buys are portfolio churn -> transfer (excluded from stats)."""
        m = map_investment_transaction(self._inv("buy", "buy"), "Brokerage")
        assert m["type"] == "transfer"
        assert m["category"] == "Investment"

    def test_sell_is_neutralized_transfer(self):
        """Sells are portfolio churn -> transfer (excluded from stats)."""
        assert map_investment_transaction(self._inv("sell", "sell"), "B")["type"] == "transfer"

    def test_cancel_is_transfer(self):
        assert map_investment_transaction(self._inv("cancel", "cancel"), "B")["type"] == "transfer"

    def test_cash_dividend_is_income(self):
        assert map_investment_transaction(self._inv("cash", "dividend"), "B")["type"] == "income"

    def test_cash_interest_is_income(self):
        assert map_investment_transaction(self._inv("cash", "interest"), "B")["type"] == "income"

    def test_cash_contribution_is_expense(self):
        """Contributions/deposits -> expense, counting toward savings."""
        assert map_investment_transaction(self._inv("cash", "contribution"), "B")["type"] == "expense"

    def test_cash_withdrawal_is_transfer(self):
        assert map_investment_transaction(self._inv("cash", "withdrawal"), "B")["type"] == "transfer"

    def test_cash_unknown_subtype_is_transfer(self):
        assert map_investment_transaction(self._inv("cash", "mystery"), "B")["type"] == "transfer"

    def test_fee_is_expense(self):
        assert map_investment_transaction(self._inv("fee", "management fee"), "B")["type"] == "expense"

    def test_transfer_type_is_transfer(self):
        assert map_investment_transaction(self._inv("transfer", "transfer"), "B")["type"] == "transfer"

    def test_unknown_type_defaults_to_transfer(self):
        assert map_investment_transaction(self._inv("weird_type", "weird_subtype"), "B")["type"] == "transfer"

    def test_absent_type_defaults_to_transfer(self):
        txn = {"investment_transaction_id": "i1", "account_id": "a1",
               "amount": 50.0, "date": "2026-05-01", "name": "x"}
        assert map_investment_transaction(txn, "B")["type"] == "transfer"

    def test_uses_investment_transaction_id(self):
        m = map_investment_transaction(self._inv("buy", "buy", inv_id="INV-42"), "B")
        assert m["plaid_transaction_id"] == "INV-42"
        assert m["category"] == "Investment"


# ═══════════════════════════════════════════════════════════════════════════
# 2. Gating — no PLAID keys -> status still works, mutating routes 503
# ═══════════════════════════════════════════════════════════════════════════

class TestPlaidGating:

    def _unconfigure(self, monkeypatch):
        monkeypatch.delenv("PLAID_CLIENT_ID", raising=False)
        monkeypatch.delenv("PLAID_SECRET", raising=False)

    def test_status_reports_unconfigured(self, client, monkeypatch):
        self._unconfigure(monkeypatch)
        resp = client.get("/api/plaid/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["configured"] is False
        assert body["items"] == []

    def test_link_token_503(self, client, monkeypatch):
        self._unconfigure(monkeypatch)
        assert client.post("/api/plaid/link-token").status_code == 503

    def test_sync_503(self, client, monkeypatch):
        self._unconfigure(monkeypatch)
        assert client.post("/api/plaid/sync", json={}).status_code == 503

    def test_sync_all_503(self, client, monkeypatch):
        self._unconfigure(monkeypatch)
        assert client.post("/api/plaid/sync-all").status_code == 503

    def test_exchange_503(self, client, monkeypatch):
        self._unconfigure(monkeypatch)
        assert client.post("/api/plaid/exchange", json={"public_token": "x"}).status_code == 503


# ═══════════════════════════════════════════════════════════════════════════
# 3. Mapping -> stats: a Plaid `transfer` (credit-card payment) is EXCLUDED
#    from /api/stats/summary, while income/expense still count.
# ═══════════════════════════════════════════════════════════════════════════

class TestMappedTypesInStats:

    def test_transfer_excluded_income_expense_counted(self, client):
        # Types exactly as produced by map_transaction.
        rows = [
            {"date": "2026-01-05", "amount": 5000.0, "type": "income",   "category": "Income"},
            {"date": "2026-01-06", "amount": 1200.0, "type": "expense",  "category": "Shopping"},
            {"date": "2026-01-07", "amount": 3000.0, "type": "transfer", "category": "Payments & Credits"},
        ]
        for r in rows:
            assert client.post("/api/transactions", json=r).status_code == 201

        summary = client.get("/api/stats/summary").json()
        assert summary["total_income"] == pytest.approx(5000.0)
        assert summary["total_expense"] == pytest.approx(1200.0)  # transfer NOT added
        assert summary["net"] == pytest.approx(3800.0)
        assert summary["count"] == 2  # transfer excluded from the count too

    def test_neutralized_investment_churn_excluded(self, client):
        """A buy and a sell — mapped to `transfer` by the new investment rules —
        do NOT inflate income/expense; only a real cash dividend (income) and a
        contribution (expense, counts toward savings) show up."""
        rows = [
            # portfolio churn -> transfer (neutralized)
            {"date": "2026-01-05", "amount": 800.0, "type": "transfer", "category": "Investment"},  # buy
            {"date": "2026-01-06", "amount": 900.0, "type": "transfer", "category": "Investment"},  # sell
            # real cash flow
            {"date": "2026-01-07", "amount": 15.0,  "type": "income",   "category": "Investment"},  # dividend
            {"date": "2026-01-08", "amount": 500.0, "type": "expense",  "category": "Investment"},  # contribution
        ]
        for r in rows:
            assert client.post("/api/transactions", json=r).status_code == 201

        summary = client.get("/api/stats/summary").json()
        assert summary["total_income"] == pytest.approx(15.0)    # dividend only, not the sell
        assert summary["total_expense"] == pytest.approx(500.0)  # contribution only, not the buy
        assert summary["savings"] == pytest.approx(500.0)        # Investment expense counts as savings
        assert summary["count"] == 2                             # both transfers excluded


# ═══════════════════════════════════════════════════════════════════════════
# 4. sync_items with a MOCKED client — create, map, cursor, idempotency,
#    removed, modified.
# ═══════════════════════════════════════════════════════════════════════════

class TestSyncItems:

    def test_added_creates_and_maps_transactions(self, test_session, monkeypatch):
        item = _seed_item(test_session)
        page = _sync_page(
            added=[
                _plaid_txn("INCOME", -4000.0, txn_id="p-inc", name="PAYROLL"),
                _plaid_txn("FOOD_AND_DRINK", 6.25, txn_id="p-food",
                           name="SBUX", merchant_name="Starbucks"),
            ],
            cursor="cursor-1",
        )
        monkeypatch.setattr(plaid_client, "get_client", lambda: FakeSyncClient([page]))

        result = sync_items(test_session, item.id)

        assert result == {"items_synced": 1, "added": 2, "modified": 0, "removed": 0}

        txns = test_session.query(Transaction).order_by(Transaction.plaid_transaction_id).all()
        assert len(txns) == 2
        by_pid = {t.plaid_transaction_id: t for t in txns}
        inc = by_pid["p-inc"]
        assert inc.type == "income" and inc.category == "Income" and inc.source == "plaid"
        assert inc.amount == pytest.approx(4000.0)
        assert inc.plaid_item_id == item.id
        assert inc.account == "My Checking"  # resolved from accounts_json label
        food = by_pid["p-food"]
        assert food.type == "expense" and food.category == "Food & Drink"
        assert food.description == "Starbucks"

        # cursor persisted on the item
        test_session.refresh(item)
        assert item.cursor == "cursor-1"

    def test_idempotent_resync_no_duplicates(self, test_session, monkeypatch):
        item = _seed_item(test_session)
        page = _sync_page(
            added=[_plaid_txn("FOOD_AND_DRINK", 6.25, txn_id="p-food")],
            cursor="cursor-1",
        )
        fake = FakeSyncClient([page])
        monkeypatch.setattr(plaid_client, "get_client", lambda: fake)

        first = sync_items(test_session, item.id)
        assert first["added"] == 1
        assert test_session.query(Transaction).count() == 1

        # Second sync returns the SAME added id -> upsert, no new row.
        second = sync_items(test_session, item.id)
        assert second["added"] == 0
        assert test_session.query(Transaction).count() == 1

    def test_removed_deletes_matching_row(self, test_session, monkeypatch):
        item = _seed_item(test_session)
        add_page = _sync_page(added=[_plaid_txn("FOOD_AND_DRINK", 6.25, txn_id="p-food")])
        monkeypatch.setattr(plaid_client, "get_client", lambda: FakeSyncClient([add_page]))
        sync_items(test_session, item.id)
        assert test_session.query(Transaction).count() == 1

        remove_page = _sync_page(removed=[{"transaction_id": "p-food"}], cursor="cursor-2")
        monkeypatch.setattr(plaid_client, "get_client", lambda: FakeSyncClient([remove_page]))
        result = sync_items(test_session, item.id)
        assert result["removed"] == 1
        assert test_session.query(Transaction).count() == 0

    def test_modified_updates_row(self, test_session, monkeypatch):
        item = _seed_item(test_session)
        add_page = _sync_page(added=[
            _plaid_txn("FOOD_AND_DRINK", 6.25, txn_id="p-food", merchant_name="Starbucks"),
        ])
        monkeypatch.setattr(plaid_client, "get_client", lambda: FakeSyncClient([add_page]))
        sync_items(test_session, item.id)

        mod_page = _sync_page(modified=[
            _plaid_txn("GENERAL_MERCHANDISE", 9.99, txn_id="p-food", merchant_name="Target"),
        ], cursor="cursor-2")
        monkeypatch.setattr(plaid_client, "get_client", lambda: FakeSyncClient([mod_page]))
        result = sync_items(test_session, item.id)

        assert result["modified"] == 1
        assert test_session.query(Transaction).count() == 1  # still one row
        row = test_session.query(Transaction).filter_by(plaid_transaction_id="p-food").one()
        assert row.amount == pytest.approx(9.99)
        assert row.category == "Shopping"
        assert row.description == "Target"

    def test_sync_route_returns_result(self, client, test_session, monkeypatch):
        """POST /api/plaid/sync {item_id} returns a PlaidSyncResult body."""
        _configure(monkeypatch)
        item = _seed_item(test_session)
        page = _sync_page(added=[_plaid_txn("FOOD_AND_DRINK", 6.25, txn_id="p-food")])
        monkeypatch.setattr(plaid_client, "get_client", lambda: FakeSyncClient([page]))

        resp = client.post("/api/plaid/sync", json={"item_id": item.id})
        assert resp.status_code == 200
        assert resp.json() == {"items_synced": 1, "added": 1, "modified": 0, "removed": 0}

    def test_sync_route_bad_item_404(self, client, monkeypatch):
        _configure(monkeypatch)
        monkeypatch.setattr(plaid_client, "get_client", lambda: FakeSyncClient([_sync_page()]))
        assert client.post("/api/plaid/sync", json={"item_id": 99999}).status_code == 404


# ═══════════════════════════════════════════════════════════════════════════
# 5. exchange (mocked) — item stored, NO access_token leaked, accounts present.
# ═══════════════════════════════════════════════════════════════════════════

class TestExchange:

    def test_exchange_stores_item_without_leaking_token(self, client, monkeypatch):
        _configure(monkeypatch)
        monkeypatch.setattr(plaid_client, "get_client", lambda: FakeExchangeClient())

        resp = client.post("/api/plaid/exchange", json={"public_token": "public-sandbox-x"})
        assert resp.status_code == 200
        body = resp.json()

        assert "access_token" not in body                 # token NEVER serialized
        assert body["item_id"] == "item-abc-123"
        assert body["institution_name"] == "Test Bank"
        assert len(body["accounts"]) == 1
        acct = body["accounts"][0]
        assert acct["account_id"] == "acc_1"
        assert acct["app_account"] == "Everyday Checking ••0000"

        # Item is persisted and listed; still no token in the /items output.
        items = client.get("/api/plaid/items").json()
        assert len(items) == 1
        assert "access_token" not in items[0]


# ═══════════════════════════════════════════════════════════════════════════
# 6. items CRUD — list + delete keeps transactions (nulls plaid_item_id).
# ═══════════════════════════════════════════════════════════════════════════

class TestItemsCrud:

    def test_list_items(self, client, test_session):
        _seed_item(test_session, item_id="item-A")
        _seed_item(test_session, item_id="item-B")
        items = client.get("/api/plaid/items").json()
        assert {i["item_id"] for i in items} == {"item-A", "item-B"}

    def test_delete_keeps_transactions_nulls_item_id(self, client, test_session, monkeypatch):
        # Unconfigured -> delete skips the best-effort Plaid item/remove call.
        monkeypatch.delenv("PLAID_CLIENT_ID", raising=False)
        monkeypatch.delenv("PLAID_SECRET", raising=False)

        item = _seed_item(test_session)
        txn = Transaction(
            date=date(2026, 3, 1),
            amount=12.0, type="expense", category="Food & Drink",
            description="Coffee", account="My Checking", source="plaid",
            plaid_transaction_id="p-keep", plaid_account_id="a1", plaid_item_id=item.id,
        )
        test_session.add(txn)
        test_session.commit()
        txn_id = txn.id

        resp = client.delete(f"/api/plaid/items/{item.id}")
        assert resp.status_code == 204

        # Item is gone.
        assert client.get("/api/plaid/items").json() == []

        # Transaction survives, detached from the deleted item.
        all_txns = client.get("/api/transactions").json()
        kept = next(t for t in all_txns if t["id"] == txn_id)
        assert kept["plaid_item_id"] is None
        assert kept["plaid_transaction_id"] == "p-keep"

    def test_delete_missing_item_404(self, client):
        assert client.delete("/api/plaid/items/424242").status_code == 404


# ═══════════════════════════════════════════════════════════════════════════
# 7. sync-all auth — PLAID_SYNC_TOKEN gate.
# ═══════════════════════════════════════════════════════════════════════════

class TestSyncAllAuth:

    def _arm(self, client, monkeypatch):
        _configure(monkeypatch)
        monkeypatch.setenv("PLAID_SYNC_TOKEN", "s3cr3t")
        # No items seeded -> a proceeding sync just returns zeros; get_client is
        # still invoked, so provide a harmless fake.
        monkeypatch.setattr(plaid_client, "get_client", lambda: FakeSyncClient([_sync_page()]))

    def test_missing_header_401(self, client, monkeypatch):
        self._arm(client, monkeypatch)
        assert client.post("/api/plaid/sync-all").status_code == 401

    def test_wrong_header_401(self, client, monkeypatch):
        self._arm(client, monkeypatch)
        resp = client.post("/api/plaid/sync-all", headers={"X-Plaid-Sync-Token": "nope"})
        assert resp.status_code == 401

    def test_correct_header_proceeds(self, client, monkeypatch):
        self._arm(client, monkeypatch)
        resp = client.post("/api/plaid/sync-all", headers={"X-Plaid-Sync-Token": "s3cr3t"})
        assert resp.status_code == 200
        assert resp.json()["items_synced"] == 0

    def test_no_token_configured_no_header_needed(self, client, monkeypatch):
        """Without PLAID_SYNC_TOKEN set, sync-all proceeds with no header."""
        _configure(monkeypatch)
        monkeypatch.delenv("PLAID_SYNC_TOKEN", raising=False)
        monkeypatch.setattr(plaid_client, "get_client", lambda: FakeSyncClient([_sync_page()]))
        assert client.post("/api/plaid/sync-all").status_code == 200
