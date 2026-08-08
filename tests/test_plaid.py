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

import sqlalchemy

from src.api import plaid_client
from src.api.plaid_mapping import map_transaction, map_investment_transaction
from src.api.models import PlaidItem, Transaction
from src.api.routes import plaid_routes
from src.api.routes.plaid_routes import sync_items


# The unique partial index makes duplicate Plaid rows un-insertable, which is the
# point — so tests that need to reproduce the PRE-migration corruption have to
# drop it first, seed the duplicates, and (where relevant) let the migration or
# the self-healing upsert put things right.
UX_INDEX = "ux_transactions_plaid_transaction_id"


def _drop_unique_index(engine) -> None:
    with engine.begin() as conn:
        conn.execute(sqlalchemy.text(f"DROP INDEX IF EXISTS {UX_INDEX}"))


def _index_names(engine) -> set[str]:
    with engine.begin() as conn:
        return {
            r[0] for r in conn.execute(sqlalchemy.text(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='transactions'"
            ))
        }


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


class _FakeApiException(Exception):
    """Shaped like plaid.ApiException: the JSON error envelope hangs off `.body`,
    and str() is the noisy status+headers+body dump the real SDK produces."""

    def __init__(self, body: str):
        super().__init__(f"(400)\nReason: Bad Request\nHTTP response body: {body}")
        self.body = body


NO_ACCOUNTS_BODY = json.dumps({
    "display_message": "No valid accounts were found at the financial institution.",
    "error_code": "NO_ACCOUNTS",
    "error_message": "no valid accounts were found for this item",
    "error_type": "ITEM_ERROR",
    "request_id": "req-1",
})


class FakeMixedClient:
    """Fake PlaidApi where SOME access_tokens raise an item-level Plaid error and
    the rest sync normally — the real-world "one bank is broken" case."""

    def __init__(self, page: dict, failing_tokens: set[str], body: str = NO_ACCOUNTS_BODY):
        self._page = page
        self._failing = failing_tokens
        self._body = body
        self.synced_tokens: list[str] = []

    def transactions_sync(self, req):
        if req.access_token in self._failing:
            raise _FakeApiException(self._body)
        self.synced_tokens.append(req.access_token)
        return _Resp(self._page)

    def accounts_get(self, req):
        # A NO_ACCOUNTS item fails here too; the balance refresh swallows it.
        if req.access_token in self._failing:
            raise _FakeApiException(self._body)
        return _Resp({"accounts": []})

    def investments_transactions_get(self, req):
        raise RuntimeError("investments product not available for this item")


class FakeReExchangeClient:
    """Fake PlaidApi for re-running Link on an ALREADY-linked institution: Plaid
    mints a brand-new Item (new item_id, new access_token) for the same
    institution_id, which is exactly how duplicate accounts used to appear."""

    def __init__(self, institution_id="ins_test", item_id="item-second", accounts=None):
        self._institution_id = institution_id
        self._item_id = item_id
        self._accounts = accounts if accounts is not None else [
            {"account_id": "acc_roth", "name": "Roth IRA", "mask": "1111",
             "type": "investment", "subtype": "ira"},
            {"account_id": "acc_brok", "name": "Brokerage", "mask": "2222",
             "type": "investment", "subtype": "brokerage"},
        ]
        self.removed_tokens: list[str] = []

    def item_public_token_exchange(self, req):
        return _ExchangeResp("access-sandbox-SECOND", self._item_id)

    def accounts_get(self, req):
        return _Resp({"item": {"institution_id": self._institution_id}, "accounts": self._accounts})

    def institutions_get_by_id(self, req):
        return _Resp({"institution": {"name": "Charles Schwab"}})

    def item_remove(self, req):
        self.removed_tokens.append(req.access_token)
        return _Resp({"request_id": "req-remove"})


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

    def test_payment_thank_you_is_transfer_even_without_loan_pfc(self):
        """"Payment Thank You" is a credit-card payment. Plaid often sends it with
        an empty/other PFC + money-in sign, which would mis-type it as a `refund`
        counted against spend; the hardcoded override forces transfer (excluded)."""
        m = map_transaction(
            _plaid_txn(None, -146.84, name="Payment Thank You-Mobile"), "Card"
        )
        assert m["type"] == "transfer"
        assert m["category"] == "Payments & Credits"

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

        assert result == {
            "items_synced": 1, "added": 2, "modified": 0, "removed": 0, "errors": [],
        }

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
        assert resp.json() == {
            "items_synced": 1, "added": 1, "modified": 0, "removed": 0, "errors": [],
        }

    def test_sync_route_bad_item_404(self, client, monkeypatch):
        _configure(monkeypatch)
        monkeypatch.setattr(plaid_client, "get_client", lambda: FakeSyncClient([_sync_page()]))
        assert client.post("/api/plaid/sync", json={"item_id": 99999}).status_code == 404


# ═══════════════════════════════════════════════════════════════════════════
# 4b. Per-item error isolation (v13) — one broken bank must not abort the run.
#     Regression guard for the NO_ACCOUNTS 502: sync_items used to raise out of
#     the loop, so every item ordered AFTER the bad one was silently skipped and
#     POST /plaid/sync-all returned 502 with no clue which bank was at fault.
# ═══════════════════════════════════════════════════════════════════════════

class TestSyncErrorIsolation:

    def test_failing_item_does_not_starve_later_items(self, test_session, monkeypatch):
        bad = _seed_item(test_session, item_id="item-bad", access_token="tok-bad")
        _seed_item(test_session, item_id="item-good-1", access_token="tok-good-1")
        _seed_item(test_session, item_id="item-good-2", access_token="tok-good-2")

        fake = FakeMixedClient(
            _sync_page(added=[_plaid_txn("FOOD_AND_DRINK", 6.25, txn_id="p-food")]),
            failing_tokens={"tok-bad"},
        )
        monkeypatch.setattr(plaid_client, "get_client", lambda: fake)

        result = sync_items(test_session)

        # The failure is FIRST in id order, yet both healthy banks still synced.
        assert result["items_synced"] == 2
        assert sorted(fake.synced_tokens) == ["tok-good-1", "tok-good-2"]

        assert len(result["errors"]) == 1
        err = result["errors"][0]
        assert err["item_id"] == bad.id
        assert err["error_code"] == "NO_ACCOUNTS"
        assert err["institution"] == "Test Bank"
        # The end-user display_message, not the raw header dump.
        assert err["message"] == "No valid accounts were found at the financial institution."

        test_session.refresh(bad)
        assert bad.status == "error"
        assert bad.error_code == "NO_ACCOUNTS"

    def test_failure_preserves_last_synced_at_and_cursor(self, test_session, monkeypatch):
        item = _seed_item(test_session)
        page = _sync_page(
            added=[_plaid_txn("FOOD_AND_DRINK", 6.25, txn_id="p-food")], cursor="cursor-1",
        )
        monkeypatch.setattr(plaid_client, "get_client", lambda: FakeSyncClient([page]))
        sync_items(test_session, item.id)
        test_session.refresh(item)
        synced_at, cursor = item.last_synced_at, item.cursor
        assert cursor == "cursor-1" and synced_at is not None

        # The same item now fails.
        monkeypatch.setattr(
            plaid_client, "get_client",
            lambda: FakeMixedClient(page, failing_tokens={item.access_token}),
        )
        result = sync_items(test_session, item.id)

        assert result["items_synced"] == 0 and len(result["errors"]) == 1
        test_session.refresh(item)
        assert item.status == "error"
        # The stamp still reads as the last SUCCESSFUL sync, not the last attempt,
        # and the cursor never advanced — so no transactions get skipped on retry.
        assert item.last_synced_at == synced_at
        assert item.cursor == cursor
        assert test_session.query(Transaction).count() == 1  # earlier data intact

    def test_successful_resync_clears_the_error(self, test_session, monkeypatch):
        item = _seed_item(test_session)
        page = _sync_page(added=[_plaid_txn("FOOD_AND_DRINK", 6.25, txn_id="p-food")])
        monkeypatch.setattr(
            plaid_client, "get_client",
            lambda: FakeMixedClient(page, failing_tokens={item.access_token}),
        )
        sync_items(test_session, item.id)
        test_session.refresh(item)
        assert item.status == "error" and item.error_code == "NO_ACCOUNTS"

        monkeypatch.setattr(plaid_client, "get_client", lambda: FakeSyncClient([page]))
        result = sync_items(test_session, item.id)

        assert result["errors"] == [] and result["items_synced"] == 1
        test_session.refresh(item)
        assert item.status == "active"
        assert item.error_code is None and item.error_message is None

    def test_non_plaid_exception_is_isolated_too(self, test_session, monkeypatch):
        """A network/unexpected error carries no Plaid envelope — it still gets
        isolated, with a null error_code instead of crashing the run."""
        item = _seed_item(test_session)

        class Boom:
            def accounts_get(self, req):
                raise RuntimeError("connection reset by peer")

            def transactions_sync(self, req):
                raise RuntimeError("connection reset by peer")

            def investments_transactions_get(self, req):
                raise RuntimeError("nope")

        monkeypatch.setattr(plaid_client, "get_client", lambda: Boom())
        result = sync_items(test_session, item.id)

        assert result["items_synced"] == 0
        assert result["errors"][0]["error_code"] is None
        assert "connection reset by peer" in result["errors"][0]["message"]
        test_session.refresh(item)
        assert item.status == "error"

    def test_sync_all_route_returns_200_not_502(self, client, test_session, monkeypatch):
        """THE regression: a NO_ACCOUNTS item used to 502 the whole endpoint."""
        _configure(monkeypatch)
        bad = _seed_item(test_session, item_id="item-bad", access_token="tok-bad")
        _seed_item(test_session, item_id="item-good", access_token="tok-good")
        page = _sync_page(added=[_plaid_txn("FOOD_AND_DRINK", 6.25, txn_id="p-food")])
        monkeypatch.setattr(
            plaid_client, "get_client",
            lambda: FakeMixedClient(page, failing_tokens={"tok-bad"}),
        )

        resp = client.post("/api/plaid/sync-all")

        assert resp.status_code == 200
        data = resp.json()
        assert data["items_synced"] == 1
        assert data["added"] == 1
        assert [e["error_code"] for e in data["errors"]] == ["NO_ACCOUNTS"]
        assert data["errors"][0]["item_id"] == bad.id

    def test_concurrent_sync_is_rejected_not_raced(self, client, test_session, monkeypatch):
        """Second caller gets 409 instead of racing the first into duplicate rows."""
        _configure(monkeypatch)
        _seed_item(test_session)
        monkeypatch.setattr(plaid_routes, "_SYNC_LOCK_TIMEOUT", 0.05)
        acquired = plaid_routes._sync_lock.acquire(timeout=1)
        assert acquired
        try:
            resp = client.post("/api/plaid/sync-all")
        finally:
            plaid_routes._sync_lock.release()

        assert resp.status_code == 409
        assert "already running" in resp.json()["detail"]

    def test_lock_is_released_after_a_failing_sync(self, test_session, monkeypatch):
        """A sync that blows up must not wedge the lock for every later sync."""
        item = _seed_item(test_session)

        class Boom:
            def accounts_get(self, req):
                raise RuntimeError("boom")

            def transactions_sync(self, req):
                raise RuntimeError("boom")

            def investments_transactions_get(self, req):
                raise RuntimeError("boom")

        monkeypatch.setattr(plaid_client, "get_client", lambda: Boom())
        sync_items(test_session, item.id)

        assert plaid_routes._sync_lock.acquire(blocking=False), "lock was left held"
        plaid_routes._sync_lock.release()

    def test_link_token_update_mode_targets_the_item(self, client, test_session, monkeypatch):
        """POST /plaid/link-token {item_id} builds an UPDATE-mode token: bound to
        that item's access_token, account selection re-opened, and NO `products`
        (Plaid rejects products alongside an access_token)."""
        _configure(monkeypatch)
        item = _seed_item(test_session, access_token="tok-repair-me")
        seen = {}

        class FakeLinkClient:
            def link_token_create(self, req):
                seen["req"] = req
                return _Resp({"link_token": "link-sandbox-update", "expiration": "2026-08-01T00:00:00Z"})

        monkeypatch.setattr(plaid_client, "get_client", lambda: FakeLinkClient())

        resp = client.post("/api/plaid/link-token", json={"item_id": item.id})

        assert resp.status_code == 200
        assert resp.json()["link_token"] == "link-sandbox-update"
        req = seen["req"]
        assert req.access_token == "tok-repair-me"
        assert req.update.account_selection_enabled is True
        assert "products" not in req.to_dict()

    def test_link_token_unknown_item_404(self, client, monkeypatch):
        _configure(monkeypatch)
        monkeypatch.setattr(plaid_client, "get_client", lambda: object())
        assert client.post("/api/plaid/link-token", json={"item_id": 99999}).status_code == 404

    def test_item_error_surfaces_on_status_route(self, client, test_session, monkeypatch):
        """The wallet reads the failure off GET /plaid/status to offer a reconnect."""
        _configure(monkeypatch)
        item = _seed_item(test_session)
        page = _sync_page()
        monkeypatch.setattr(
            plaid_client, "get_client",
            lambda: FakeMixedClient(page, failing_tokens={item.access_token}),
        )
        client.post("/api/plaid/sync-all")

        body = client.get("/api/plaid/status").json()
        entry = next(i for i in body["items"] if i["id"] == item.id)
        assert entry["status"] == "error"
        assert entry["error_code"] == "NO_ACCOUNTS"
        assert "No valid accounts" in entry["error_message"]


# ═══════════════════════════════════════════════════════════════════════════
# 4c. Duplicate Plaid rows (v13). Concurrent syncs used to insert the same
#     plaid_transaction_id twice — double-counting the charge in every stat, then
#     breaking that item's sync for good once the upsert's one-or-none lookup
#     started raising MultipleResultsFound (surfaced as a bogus 502 from Plaid).
# ═══════════════════════════════════════════════════════════════════════════

class TestPlaidDuplicateRows:

    def _seed_duplicate_pair(self, session, pid="p-dupe"):
        """Two rows for ONE Plaid transaction — only possible pre-index."""
        for _ in range(2):
            session.add(Transaction(
                date=date(2026, 6, 1), amount=6.25, type="expense", category="Food & Drink",
                description="Starbucks", account="My Checking", source="plaid",
                plaid_transaction_id=pid,
            ))
        session.commit()

    def test_unique_index_blocks_a_second_insert(self, test_session):
        """The DB itself now refuses the duplicate that races used to create."""
        test_session.add(Transaction(
            date=date(2026, 6, 1), amount=6.25, type="expense", category="Food & Drink",
            account="My Checking", source="plaid", plaid_transaction_id="p-solo",
        ))
        test_session.commit()

        test_session.add(Transaction(
            date=date(2026, 6, 1), amount=6.25, type="expense", category="Food & Drink",
            account="My Checking", source="plaid", plaid_transaction_id="p-solo",
        ))
        with pytest.raises(sqlalchemy.exc.IntegrityError):
            test_session.commit()
        test_session.rollback()

    def test_manual_rows_are_unaffected_by_the_index(self, test_session):
        """Manual/CSV rows all carry a NULL plaid id — NULLs must never collide."""
        for _ in range(3):
            test_session.add(Transaction(
                date=date(2026, 6, 1), amount=10.0, type="expense",
                category="Groceries", source="manual",
            ))
        test_session.commit()
        assert test_session.query(Transaction).count() == 3

    def test_upsert_heals_existing_duplicates_instead_of_raising(
        self, test_engine, test_session, monkeypatch,
    ):
        """An un-migrated DB must recover on its own rather than 502 forever."""
        _drop_unique_index(test_engine)
        item = _seed_item(test_session)
        self._seed_duplicate_pair(test_session)
        assert test_session.query(Transaction).count() == 2

        page = _sync_page(added=[_plaid_txn("FOOD_AND_DRINK", 6.25, txn_id="p-dupe")])
        monkeypatch.setattr(plaid_client, "get_client", lambda: FakeSyncClient([page]))

        result = sync_items(test_session, item.id)

        assert result["errors"] == []          # no MultipleResultsFound
        assert test_session.query(Transaction).count() == 1  # collapsed to one

    def test_delete_removes_every_copy(self, test_engine, test_session, monkeypatch):
        _drop_unique_index(test_engine)
        item = _seed_item(test_session)
        self._seed_duplicate_pair(test_session)

        page = _sync_page(removed=[{"transaction_id": "p-dupe"}])
        monkeypatch.setattr(plaid_client, "get_client", lambda: FakeSyncClient([page]))
        sync_items(test_session, item.id)

        assert test_session.query(Transaction).count() == 0

    def test_migration_dedupes_and_enforces_uniqueness(
        self, test_engine, test_session, monkeypatch,
    ):
        """The startup migration collapses pre-existing dupes (keeping the oldest)
        and leaves the unique index in place."""
        from src.api import main as app_main

        _drop_unique_index(test_engine)
        self._seed_duplicate_pair(test_session, pid="p-dupe-a")
        self._seed_duplicate_pair(test_session, pid="p-dupe-b")
        test_session.add(Transaction(
            date=date(2026, 6, 2), amount=1.0, type="expense", category="Groceries",
            source="manual",
        ))
        test_session.commit()
        kept_ids = [
            r[0] for r in test_session.execute(sqlalchemy.text(
                "SELECT MIN(id) FROM transactions WHERE plaid_transaction_id IS NOT NULL "
                "GROUP BY plaid_transaction_id ORDER BY 1"
            ))
        ]
        assert test_session.query(Transaction).count() == 5

        # Point the migration's engine at this test's in-memory DB.
        monkeypatch.setattr(app_main, "engine", test_engine)
        app_main._migrate_dedupe_plaid_transactions()

        rows = test_session.query(Transaction).order_by(Transaction.id).all()
        assert len(rows) == 3  # 2 deduped Plaid rows + the untouched manual one
        # The OLDEST row of each duplicate group survived.
        surviving_plaid = [r.id for r in rows if r.plaid_transaction_id is not None]
        assert surviving_plaid == kept_ids
        # ...and the index is now in place, so it can't happen again.
        assert UX_INDEX in _index_names(test_engine)

    def test_migration_is_idempotent(self, test_engine, monkeypatch):
        from src.api import main as app_main

        monkeypatch.setattr(app_main, "engine", test_engine)
        app_main._migrate_dedupe_plaid_transactions()
        app_main._migrate_dedupe_plaid_transactions()  # must not raise
        assert UX_INDEX in _index_names(test_engine)


# ═══════════════════════════════════════════════════════════════════════════
# 4d. Duplicate ACCOUNTS (v13). Re-running Link on a connected bank to pick up a
#     newly-opened account mints a NEW Plaid Item covering the accounts you
#     already had — so the old ones came back as duplicates, double-counting
#     their balances AND holdings in net worth, and re-importing their
#     transactions under fresh per-Item ids that no uniqueness rule can catch.
# ═══════════════════════════════════════════════════════════════════════════

class TestDuplicateInstitution:

    def _seed_schwab(self, session):
        item = PlaidItem(
            item_id="item-schwab-1",
            access_token="access-schwab-1",
            institution_id="ins_test",
            institution_name="Charles Schwab",
            accounts_json=json.dumps({"acc_roth_orig": {"app_account": "Roth IRA ••1111"}}),
            status="active",
        )
        session.add(item)
        session.commit()
        session.refresh(item)
        return item

    def test_relinking_a_connected_bank_is_refused(self, client, test_session, monkeypatch):
        _configure(monkeypatch)
        first = self._seed_schwab(test_session)
        fake = FakeReExchangeClient()
        monkeypatch.setattr(plaid_client, "get_client", lambda: fake)

        resp = client.post("/api/plaid/exchange", json={"public_token": "public-second"})

        assert resp.status_code == 409
        detail = resp.json()["detail"]
        assert detail["code"] == "INSTITUTION_ALREADY_LINKED"
        assert detail["item_id"] == first.id
        assert "already connected" in detail["message"]
        assert "Add accounts" in detail["message"]

        # No second row for the same bank...
        items = test_session.query(PlaidItem).all()
        assert len(items) == 1 and items[0].id == first.id
        # ...and the Item Plaid just minted was released, not left billing.
        assert fake.removed_tokens == ["access-sandbox-SECOND"]

    def test_refusal_does_not_disturb_the_existing_item(self, client, test_session, monkeypatch):
        """The original link keeps its token, accounts and labels untouched."""
        _configure(monkeypatch)
        first = self._seed_schwab(test_session)
        monkeypatch.setattr(plaid_client, "get_client", lambda: FakeReExchangeClient())

        client.post("/api/plaid/exchange", json={"public_token": "public-second"})

        test_session.refresh(first)
        assert first.access_token == "access-schwab-1"
        assert first.item_id == "item-schwab-1"
        assert json.loads(first.accounts_json) == {
            "acc_roth_orig": {"app_account": "Roth IRA ••1111"}
        }

    def test_relinking_the_SAME_item_still_upserts(self, client, test_session, monkeypatch):
        """Re-linking an item Plaid hands back with the SAME item_id is a genuine
        refresh, not a duplicate — it must still update in place."""
        _configure(monkeypatch)
        first = self._seed_schwab(test_session)
        monkeypatch.setattr(
            plaid_client, "get_client",
            lambda: FakeReExchangeClient(item_id="item-schwab-1"),
        )

        resp = client.post("/api/plaid/exchange", json={"public_token": "public-refresh"})

        assert resp.status_code == 200
        assert test_session.query(PlaidItem).count() == 1
        test_session.refresh(first)
        assert first.access_token == "access-sandbox-SECOND"  # token rotated in place
        assert set(json.loads(first.accounts_json)) == {"acc_roth", "acc_brok"}

    def test_a_different_bank_links_normally(self, client, test_session, monkeypatch):
        """The guard is per-institution — a genuinely new bank is unaffected."""
        _configure(monkeypatch)
        self._seed_schwab(test_session)
        monkeypatch.setattr(
            plaid_client, "get_client",
            lambda: FakeReExchangeClient(institution_id="ins_other", item_id="item-other"),
        )

        resp = client.post("/api/plaid/exchange", json={"public_token": "public-other"})

        assert resp.status_code == 200
        assert test_session.query(PlaidItem).count() == 2

    def test_bank_without_institution_id_is_not_blocked(self, client, test_session, monkeypatch):
        """institution_id can be absent; never refuse a link on a null match."""
        _configure(monkeypatch)
        item = PlaidItem(item_id="item-noinst", access_token="tok", institution_id=None,
                         status="active")
        test_session.add(item)
        test_session.commit()
        monkeypatch.setattr(
            plaid_client, "get_client",
            lambda: FakeReExchangeClient(institution_id=None, item_id="item-noinst-2"),
        )

        resp = client.post("/api/plaid/exchange", json={"public_token": "public-x"})

        assert resp.status_code == 200
        assert test_session.query(PlaidItem).count() == 2

    def test_add_accounts_uses_update_mode_on_the_same_item(
        self, client, test_session, monkeypatch,
    ):
        """The supported path: one Item, account selection reopened — so an
        account that's already linked can never be added a second time."""
        _configure(monkeypatch)
        item = self._seed_schwab(test_session)
        seen = {}

        class FakeLinkClient:
            def link_token_create(self, req):
                seen["req"] = req
                return _Resp({"link_token": "link-add-accounts", "expiration": "2026-08-09T00:00:00Z"})

        monkeypatch.setattr(plaid_client, "get_client", lambda: FakeLinkClient())

        resp = client.post("/api/plaid/link-token", json={"item_id": item.id})

        assert resp.status_code == 200
        assert seen["req"].access_token == "access-schwab-1"
        assert seen["req"].update.account_selection_enabled is True
        assert test_session.query(PlaidItem).count() == 1  # still ONE Schwab item


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

    def test_delete_removes_transactions(self, client, test_session, monkeypatch):
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

        # Unlinking deletes that item's transactions too.
        all_txns = client.get("/api/transactions").json()
        assert all(t["id"] != txn_id for t in all_txns)

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
