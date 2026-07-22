"""
Plaid integration routes (v8).

Pull transactions + investment transactions directly from banks via Plaid Link.
Everything network-touching is GATED by plaid_client.is_configured(): with
PLAID_CLIENT_ID/PLAID_SECRET unset the app still boots and these endpoints return
503 (mirrors assistant.py). GET /plaid/status is the one exception — it always
works (reports configured=false and lists any items already in the DB).

Design decisions (v8):
  * Sandbox-first (PLAID_ENV, default "sandbox").
  * Data: Transactions + Investments.
  * Sync: manual POST /plaid/sync AND a cron-friendly POST /plaid/sync-all, plus
    an optional in-process scheduler (see main.py). All three reuse sync_items().
  * Categorization: TRUST Plaid — map personal_finance_category directly. Plaid
    rows never touch the rules engine / needs-review / AI (see plaid_mapping.py).

The access_token is stored server-side on PlaidItem ONLY and is NEVER returned.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Transaction, PlaidItem
from ..schemas import (
    PlaidAccount,
    PlaidItemOut,
    PlaidStatus,
    ExchangeRequest,
    PlaidSyncRequest,
    PlaidSyncResult,
    HoldingOut,
)
from .. import plaid_client
from ..plaid_mapping import map_transaction, map_investment_transaction

router = APIRouter(prefix="/plaid", tags=["plaid"])

CLIENT_NAME = "Expense Tracker"
CLIENT_USER_ID = "local-user"
# Products every linked institution MUST support (sent in Link `products`).
# Anything else in PLAID_PRODUCTS (e.g. "investments") is requested only if the
# institution supports it, so cards/banks aren't rejected at Link time.
BASELINE_PRODUCTS = frozenset({"transactions"})
# Rolling window for investments/transactions/get (~2 years).
INVESTMENTS_WINDOW_DAYS = 730
_TXN_SYNC_COUNT = 500


# ── Helpers ────────────────────────────────────────────────────────────────────

def _require_configured() -> None:
    if not plaid_client.is_configured():
        raise HTTPException(
            503,
            "Plaid is not configured. Set PLAID_CLIENT_ID and PLAID_SECRET on the server.",
        )


def _accounts_map(item: PlaidItem) -> dict:
    """Parse the stored accounts_json into {account_id: {...}} (empty on error)."""
    if not item.accounts_json:
        return {}
    try:
        data = json.loads(item.accounts_json)
        return data if isinstance(data, dict) else {}
    except (ValueError, TypeError):
        return {}


def _account_label(accounts: dict, account_id: str, fallback: str) -> str:
    """Resolve a friendly account label for a synced transaction."""
    info = accounts.get(account_id) if account_id else None
    if isinstance(info, dict) and info.get("app_account"):
        return info["app_account"]
    return fallback


def _account_info(acct: dict, existing: dict | None) -> dict | None:
    """Build one accounts_json entry from an accounts_get account dict, preserving
    an existing app_account label on re-link/refresh and capturing balances (v9)."""
    aid = acct.get("account_id")
    if not aid:
        return None
    name = acct.get("name") or acct.get("official_name")
    mask = acct.get("mask")
    prev = (existing or {}).get(aid)
    if isinstance(prev, dict) and prev.get("app_account"):
        label = prev["app_account"]
    else:
        label = f"{name} ••{mask}" if name and mask else (name or aid)
    balances = acct.get("balances")
    if not isinstance(balances, dict):
        balances = {}
    return {
        "name": name,
        "mask": mask,
        "type": str(acct.get("type")) if acct.get("type") is not None else None,
        "subtype": str(acct.get("subtype")) if acct.get("subtype") is not None else None,
        "app_account": label,
        "available": balances.get("available"),
        "current": balances.get("current"),
        "currency": balances.get("iso_currency_code"),
    }


def _build_accounts_map(accounts_resp: dict, existing: dict | None = None) -> dict:
    """Build the {account_id: {...}} map from an accounts_get response, merging in
    per-account balances and preserving existing app_account labels (v9)."""
    accounts: dict = {}
    for acct in accounts_resp.get("accounts", []):
        info = _account_info(acct, existing)
        if info is not None:
            accounts[acct.get("account_id")] = info
    return accounts


def _refresh_account_balances(client, item: PlaidItem) -> None:
    """Best-effort refresh of per-account balances into item.accounts_json (v9).
    Never raises — a balance-fetch failure must not break a sync."""
    from plaid.model.accounts_get_request import AccountsGetRequest

    try:
        resp = client.accounts_get(AccountsGetRequest(access_token=item.access_token)).to_dict()
    except Exception:
        return
    accounts = _build_accounts_map(resp, _accounts_map(item))
    if accounts:
        item.accounts_json = json.dumps(accounts)


def _item_to_out(item: PlaidItem) -> PlaidItemOut:
    accounts = _accounts_map(item)
    account_list = [
        PlaidAccount(
            account_id=aid,
            name=info.get("name"),
            mask=info.get("mask"),
            type=info.get("type"),
            subtype=info.get("subtype"),
            app_account=info.get("app_account"),
            available=info.get("available"),
            current=info.get("current"),
            currency=info.get("currency"),
        )
        for aid, info in accounts.items()
        if isinstance(info, dict)
    ]
    return PlaidItemOut(
        id=item.id,
        item_id=item.item_id,
        institution_id=item.institution_id,
        institution_name=item.institution_name,
        institution_logo=item.institution_logo,
        institution_color=item.institution_color,
        accounts=account_list,
        status=item.status,
        last_synced_at=item.last_synced_at,
        created_at=item.created_at,
    )


def _plaid_error(e: Exception) -> HTTPException:
    """Map a Plaid SDK / network error to a 502 (never leak the access_token)."""
    return HTTPException(502, f"Plaid request failed: {e}")


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.get("/status", response_model=PlaidStatus)
def status(db: Session = Depends(get_db)):
    """Whether Plaid is configured + the linked items (works WITHOUT keys)."""
    items = db.execute(select(PlaidItem).order_by(PlaidItem.id.asc())).scalars().all()
    return PlaidStatus(
        configured=plaid_client.is_configured(),
        env=plaid_client.get_env(),
        products=plaid_client.get_products(),
        items=[_item_to_out(i) for i in items],
    )


@router.post("/link-token")
def create_link_token():
    """Create a Link token to open Plaid Link on the client. 503 if unconfigured."""
    _require_configured()
    from plaid.model.link_token_create_request import LinkTokenCreateRequest
    from plaid.model.link_token_create_request_user import LinkTokenCreateRequestUser
    from plaid.model.link_token_transactions import LinkTokenTransactions
    from plaid.model.products import Products
    from plaid.model.country_code import CountryCode

    client = plaid_client.get_client()

    # Plaid requires the chosen institution to support EVERY product in `products`,
    # so requesting "investments" up front rejects credit-card/bank-only banks with
    # "not an investment account". Keep only baseline products (transactions) as
    # required, and request the rest via `required_if_supported_products` so cards/
    # banks link fine while brokerages still return holdings. Config-driven: whatever
    # PLAID_PRODUCTS lists that isn't a baseline product is treated as optional.
    all_products = plaid_client.get_products()
    products = [p for p in all_products if p in BASELINE_PRODUCTS]
    if not products:  # never send an empty `products`; fall back to transactions
        products = ["transactions"]
    supported_if_possible = [
        p for p in all_products if p not in BASELINE_PRODUCTS and p not in products
    ]

    kwargs = dict(
        products=[Products(p) for p in products],
        client_name=CLIENT_NAME,
        country_codes=[CountryCode(c) for c in plaid_client.get_country_codes()],
        language="en",
        user=LinkTokenCreateRequestUser(client_user_id=CLIENT_USER_ID),
    )
    if supported_if_possible:
        kwargs["required_if_supported_products"] = [
            Products(p) for p in supported_if_possible
        ]
    # Ask for the full history window (Plaid's max, 730 days / 24 months) instead
    # of the default 90 days, so the initial sync backfills well over a year of
    # spend. The window is fixed at Link time, so this only affects NEW links —
    # existing items must be re-linked to backfill. Only meaningful when
    # transactions is requested (it always is, as the baseline product).
    if "transactions" in products:
        kwargs["transactions"] = LinkTokenTransactions(days_requested=730)
    redirect_uri = plaid_client.get_redirect_uri()
    if redirect_uri:
        kwargs["redirect_uri"] = redirect_uri
    try:
        resp = client.link_token_create(LinkTokenCreateRequest(**kwargs))
    except Exception as e:  # plaid.ApiException / network
        raise _plaid_error(e)
    data = resp.to_dict()
    return {"link_token": data.get("link_token"), "expiration": data.get("expiration")}


@router.post("/exchange", response_model=PlaidItemOut)
def exchange_public_token(body: ExchangeRequest, db: Session = Depends(get_db)):
    """Exchange a Link public_token for an access_token, fetch the accounts +
    institution name, and persist a PlaidItem. 503 if unconfigured."""
    _require_configured()
    from plaid.model.item_public_token_exchange_request import ItemPublicTokenExchangeRequest
    from plaid.model.accounts_get_request import AccountsGetRequest
    from plaid.model.institutions_get_by_id_request import InstitutionsGetByIdRequest
    from plaid.model.institutions_get_by_id_request_options import InstitutionsGetByIdRequestOptions
    from plaid.model.country_code import CountryCode

    client = plaid_client.get_client()
    try:
        exchange = client.item_public_token_exchange(
            ItemPublicTokenExchangeRequest(public_token=body.public_token)
        )
        access_token = exchange.access_token
        item_id = exchange.item_id

        accounts_resp = client.accounts_get(AccountsGetRequest(access_token=access_token)).to_dict()
    except Exception as e:
        raise _plaid_error(e)

    institution_id = (accounts_resp.get("item") or {}).get("institution_id")

    # Upsert by item_id (re-linking the same bank updates the token/accounts).
    existing = db.execute(
        select(PlaidItem).where(PlaidItem.item_id == item_id)
    ).scalar_one_or_none()

    # Build the {account_id: {...}} map (labels + balances), preserving any
    # existing app_account labels when re-linking the same bank.
    prev_accounts = _accounts_map(existing) if existing is not None else None
    accounts = _build_accounts_map(accounts_resp, prev_accounts)

    # Best-effort institution name + branding (never break exchange if absent).
    institution_name = None
    institution_logo = None
    institution_color = None
    if institution_id:
        try:
            inst = client.institutions_get_by_id(
                InstitutionsGetByIdRequest(
                    institution_id=institution_id,
                    country_codes=[CountryCode(c) for c in plaid_client.get_country_codes()],
                    options=InstitutionsGetByIdRequestOptions(include_optional_metadata=True),
                )
            ).to_dict()
            institution = inst.get("institution") or {}
            institution_name = institution.get("name")
            institution_logo = institution.get("logo")
            institution_color = institution.get("primary_color")
        except Exception:
            pass  # resilient: leave name/logo/color null, never break exchange

    if existing is not None:
        existing.access_token = access_token
        existing.institution_id = institution_id
        existing.institution_name = institution_name
        existing.institution_logo = institution_logo
        existing.institution_color = institution_color
        existing.accounts_json = json.dumps(accounts)
        existing.status = "active"
        item = existing
    else:
        item = PlaidItem(
            item_id=item_id,
            access_token=access_token,
            institution_id=institution_id,
            institution_name=institution_name,
            institution_logo=institution_logo,
            institution_color=institution_color,
            accounts_json=json.dumps(accounts),
            status="active",
        )
        db.add(item)
    db.commit()
    db.refresh(item)
    return _item_to_out(item)


# ── Sync (shared by the route + scheduler) ───────────────────────────────────

def _sync_transactions_for_item(client, db: Session, item: PlaidItem) -> dict:
    """Incrementally sync /transactions/sync for one item. UPSERT by
    plaid_transaction_id; delete removed rows. Persists the new cursor."""
    from plaid.model.transactions_sync_request import TransactionsSyncRequest

    accounts = _accounts_map(item)
    fallback = item.institution_name or "Plaid"
    added = modified = removed = 0
    cursor = item.cursor or None
    has_more = True

    while has_more:
        req_kwargs = dict(access_token=item.access_token, count=_TXN_SYNC_COUNT)
        if cursor:
            req_kwargs["cursor"] = cursor
        resp = client.transactions_sync(TransactionsSyncRequest(**req_kwargs)).to_dict()

        for txn in resp.get("added", []):
            label = _account_label(accounts, txn.get("account_id"), fallback)
            if _upsert_transaction(db, item, map_transaction(txn, label)):
                added += 1
        for txn in resp.get("modified", []):
            label = _account_label(accounts, txn.get("account_id"), fallback)
            _upsert_transaction(db, item, map_transaction(txn, label))
            modified += 1
        for removed_txn in resp.get("removed", []):
            tid = removed_txn.get("transaction_id")
            if tid and _delete_transaction(db, tid):
                removed += 1

        cursor = resp.get("next_cursor") or cursor
        has_more = bool(resp.get("has_more"))

    item.cursor = cursor
    return {"added": added, "modified": modified, "removed": removed}


def _sync_investments_for_item(client, db: Session, item: PlaidItem) -> dict:
    """Pull /investments/transactions/get over a rolling window and UPSERT by
    investment_transaction_id. Best-effort: items without investments products
    simply raise and we skip them."""
    from plaid.model.investments_transactions_get_request import InvestmentsTransactionsGetRequest
    from plaid.model.investments_transactions_get_request_options import (
        InvestmentsTransactionsGetRequestOptions,
    )

    accounts = _accounts_map(item)
    fallback = item.institution_name or "Plaid"
    end = date.today()
    start = end - timedelta(days=INVESTMENTS_WINDOW_DAYS)
    added = modified = 0
    offset = 0
    total = None

    while total is None or offset < total:
        req = InvestmentsTransactionsGetRequest(
            access_token=item.access_token,
            start_date=start,
            end_date=end,
            options=InvestmentsTransactionsGetRequestOptions(count=100, offset=offset),
        )
        resp = client.investments_transactions_get(req).to_dict()
        total = resp.get("total_investment_transactions", 0)
        batch = resp.get("investment_transactions", [])
        if not batch:
            break
        for inv in batch:
            label = _account_label(accounts, inv.get("account_id"), fallback)
            if _upsert_transaction(db, item, map_investment_transaction(inv, label)):
                added += 1
            else:
                modified += 1
        offset += len(batch)

    return {"added": added, "modified": modified, "removed": 0}


def _upsert_transaction(db: Session, item: PlaidItem, mapped: dict) -> bool:
    """Insert (True) or update (False) a Transaction by plaid_transaction_id.
    Skips rows with no usable id or date."""
    pid = mapped.get("plaid_transaction_id")
    if not pid or mapped.get("date") is None:
        return False
    existing = db.execute(
        select(Transaction).where(Transaction.plaid_transaction_id == pid)
    ).scalar_one_or_none()
    if existing is None:
        obj = Transaction(
            date=mapped["date"],
            amount=mapped["amount"],
            type=mapped["type"],
            category=mapped["category"],
            description=mapped["description"],
            account=mapped["account"],
            source="plaid",
            plaid_transaction_id=pid,
            plaid_account_id=mapped.get("plaid_account_id"),
            plaid_item_id=item.id,
            merchant_name=mapped.get("merchant_name"),
            logo_url=mapped.get("logo_url"),
            pending=bool(mapped.get("pending", False)),
            pending_transaction_id=mapped.get("pending_transaction_id"),
            category_icon_url=mapped.get("category_icon_url"),
        )
        db.add(obj)
        return True
    existing.date = mapped["date"]
    existing.amount = mapped["amount"]
    existing.type = mapped["type"]
    existing.category = mapped["category"]
    existing.description = mapped["description"]
    existing.account = mapped["account"]
    existing.source = "plaid"
    existing.plaid_account_id = mapped.get("plaid_account_id")
    existing.plaid_item_id = item.id
    existing.merchant_name = mapped.get("merchant_name")
    existing.logo_url = mapped.get("logo_url")
    existing.pending = bool(mapped.get("pending", False))
    existing.pending_transaction_id = mapped.get("pending_transaction_id")
    existing.category_icon_url = mapped.get("category_icon_url")
    return False


def _delete_transaction(db: Session, plaid_transaction_id: str) -> bool:
    obj = db.execute(
        select(Transaction).where(Transaction.plaid_transaction_id == plaid_transaction_id)
    ).scalar_one_or_none()
    if obj is None:
        return False
    db.delete(obj)
    return True


def sync_items(db: Session, item_id: int | None = None) -> dict:
    """Sync one item (item_id given) or ALL items. Reused by the route AND the
    scheduler. Assumes Plaid is configured (callers gate first). Returns a dict
    matching PlaidSyncResult: items_synced, added, modified, removed."""
    client = plaid_client.get_client()

    stmt = select(PlaidItem)
    if item_id is not None:
        stmt = stmt.where(PlaidItem.id == item_id)
    items = db.execute(stmt.order_by(PlaidItem.id.asc())).scalars().all()

    items_synced = 0
    added = modified = removed = 0
    for item in items:
        # v9: refresh per-account balances (best-effort — never fails the sync).
        _refresh_account_balances(client, item)

        txn_res = _sync_transactions_for_item(client, db, item)
        added += txn_res["added"]
        modified += txn_res["modified"]
        removed += txn_res["removed"]

        # Investments are best-effort: an item without the investments product
        # raises PRODUCT_NOT_READY/NOT_SUPPORTED — skip it, don't fail the sync.
        try:
            inv_res = _sync_investments_for_item(client, db, item)
            added += inv_res["added"]
            modified += inv_res["modified"]
        except Exception:
            pass

        item.last_synced_at = datetime.utcnow()
        item.status = "active"
        items_synced += 1
        db.commit()

    return {
        "items_synced": items_synced,
        "added": added,
        "modified": modified,
        "removed": removed,
    }


@router.post("/sync", response_model=PlaidSyncResult)
def sync(body: PlaidSyncRequest, db: Session = Depends(get_db)):
    """Sync a single item (body.item_id) or all items (item_id null). 503 if
    unconfigured."""
    _require_configured()
    if body.item_id is not None:
        exists = db.get(PlaidItem, body.item_id)
        if exists is None:
            raise HTTPException(404, f"Plaid item {body.item_id} not found")
    try:
        result = sync_items(db, body.item_id)
    except plaid_client.PlaidNotConfigured as e:
        raise HTTPException(503, str(e))
    except Exception as e:
        raise _plaid_error(e)
    return PlaidSyncResult(**result)


@router.post("/sync-all", response_model=PlaidSyncResult)
def sync_all(
    x_plaid_sync_token: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    """Sync ALL items. Cron/scheduler friendly. When PLAID_SYNC_TOKEN is set the
    caller must send a matching X-Plaid-Sync-Token header (401 otherwise).
    503 if unconfigured."""
    _require_configured()
    import os

    required = (os.environ.get("PLAID_SYNC_TOKEN") or "").strip()
    if required and x_plaid_sync_token != required:
        raise HTTPException(401, "Invalid or missing X-Plaid-Sync-Token")
    try:
        result = sync_items(db, None)
    except plaid_client.PlaidNotConfigured as e:
        raise HTTPException(503, str(e))
    except Exception as e:
        raise _plaid_error(e)
    return PlaidSyncResult(**result)


@router.get("/items", response_model=list[PlaidItemOut])
def list_items(db: Session = Depends(get_db)):
    """List linked items (works WITHOUT keys)."""
    items = db.execute(select(PlaidItem).order_by(PlaidItem.id.asc())).scalars().all()
    return [_item_to_out(i) for i in items]


@router.get("/holdings", response_model=list[HoldingOut])
def list_holdings(db: Session = Depends(get_db)):
    """Investment holdings across all items with the investments product, computed
    on demand (no storage). Joins holdings→securities. 503 if unconfigured; empty
    list if no investment accounts. Items without investments are skipped."""
    _require_configured()
    from plaid.model.investments_holdings_get_request import InvestmentsHoldingsGetRequest

    client = plaid_client.get_client()
    items = db.execute(select(PlaidItem).order_by(PlaidItem.id.asc())).scalars().all()

    out: list[HoldingOut] = []
    for item in items:
        accounts = _accounts_map(item)
        fallback = item.institution_name or "Plaid"
        try:
            resp = client.investments_holdings_get(
                InvestmentsHoldingsGetRequest(access_token=item.access_token)
            ).to_dict()
        except Exception:
            continue  # item without investments product / transient error — skip

        securities = {
            s.get("security_id"): s
            for s in resp.get("securities", [])
            if isinstance(s, dict) and s.get("security_id")
        }
        for h in resp.get("holdings", []):
            sec = securities.get(h.get("security_id")) or {}
            label = _account_label(accounts, h.get("account_id"), fallback)
            quantity = h.get("quantity")
            price = h.get("institution_price")
            value = h.get("institution_value")
            currency = h.get("iso_currency_code") or sec.get("iso_currency_code")
            out.append(
                HoldingOut(
                    account=label,
                    institution=item.institution_name,
                    security_name=sec.get("name"),
                    ticker_symbol=sec.get("ticker_symbol"),
                    quantity=float(quantity) if quantity is not None else None,
                    price=float(price) if price is not None else None,
                    value=float(value) if value is not None else None,
                    currency=currency,
                )
            )
    return out


@router.delete("/items/{item_id}", status_code=204)
def delete_item(item_id: int, db: Session = Depends(get_db)):
    """Unlink an item: best-effort Plaid item/remove, then DELETE that item's
    imported transactions and the PlaidItem row."""
    item = db.get(PlaidItem, item_id)
    if item is None:
        raise HTTPException(404, f"Plaid item {item_id} not found")

    if plaid_client.is_configured():
        try:
            from plaid.model.item_remove_request import ItemRemoveRequest

            client = plaid_client.get_client()
            client.item_remove(ItemRemoveRequest(access_token=item.access_token))
        except Exception:
            pass  # best-effort — always remove our local row

    # Delete the transactions imported for this item, then the item itself.
    for txn in db.execute(
        select(Transaction).where(Transaction.plaid_item_id == item_id)
    ).scalars().all():
        db.delete(txn)

    db.delete(item)
    db.commit()
