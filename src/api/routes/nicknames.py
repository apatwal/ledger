"""
Account nicknames (v11).

A user-editable friendly name for a connected account. Keyed by the canonical
account `label` (the app_account string stored on transactions / holdings). The
UI overlays the nickname wherever the raw label would appear.

Prefix "/accounts" so this coexists with the standalone GET /api/accounts
endpoint in main.py — nicknames live under /api/accounts/nicknames, so there is
no path collision. Registered with require_user like the other feature routers.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import AccountNickname, PlaidItem
from ..schemas import AccountNicknameIn, AccountNicknameOut, SavingsToggleIn
from .. import plaid_client

router = APIRouter(prefix="/accounts", tags=["accounts"])


def _all_nicknames(db: Session) -> list[AccountNicknameOut]:
    rows = db.execute(
        select(AccountNickname).order_by(AccountNickname.label.asc())
    ).scalars().all()
    return [
        AccountNicknameOut(label=r.label, nickname=r.nickname, is_savings=r.is_savings)
        for r in rows
    ]


@router.get("/nicknames", response_model=list[AccountNicknameOut])
def list_nicknames(db: Session = Depends(get_db)):
    """All account nicknames, ordered by label. Includes `is_savings` (v11b)."""
    return _all_nicknames(db)


@router.put("/nicknames", response_model=list[AccountNicknameOut])
def upsert_nickname(body: AccountNicknameIn, db: Session = Depends(get_db)):
    """Upsert a nickname by `label`. The nickname is trimmed. If it is blank /
    whitespace-only, the row is DELETED instead (revert to the raw label) — UNLESS
    the row is a designated savings account (`is_savings` True), in which case the
    row is kept (nickname cleared) so the savings flag is never wiped (v11b).

    Returns 200 with the FULL current nicknames list (the now-empty state after a
    blank-delete is reflected by the label's absence), so the client can refresh
    its overlay from one response.
    """
    label = (body.label or "").strip()
    nickname = (body.nickname or "").strip()

    existing = db.execute(
        select(AccountNickname).where(AccountNickname.label == label)
    ).scalar_one_or_none()

    if not nickname:
        # Blank nickname => delete the row (revert to raw label) ONLY when it is
        # not a savings account; otherwise keep the row but clear the nickname so
        # the savings flag survives. Idempotent.
        if existing is not None:
            if existing.is_savings:
                existing.nickname = ""
            else:
                db.delete(existing)
            db.commit()
    elif existing is not None:
        existing.nickname = nickname
        db.commit()
    else:
        db.add(AccountNickname(label=label, nickname=nickname))
        db.commit()

    return _all_nicknames(db)


@router.put("/savings", response_model=list[AccountNicknameOut])
def toggle_savings(body: SavingsToggleIn, db: Session = Depends(get_db)):
    """Designate (or un-designate) an account as a savings destination (v11b).

    Upserts the row's `is_savings` (creating a row with nickname="" if none
    exists). If the result is `is_savings` False AND the nickname is blank, the
    row is deleted (nothing left to persist).

    After the toggle, BEST-EFFORT re-maps that account's transaction history so
    existing rows pick up the new savings logic: for each PlaidItem whose
    accounts_json contains an account whose app_account == label, reset the item
    cursor and re-run the item's transaction sync. Because _upsert_transaction
    matches on plaid_transaction_id and overwrites type/category, this re-maps
    existing rows idempotently (no duplicates). Never raises.

    Returns 200 with the FULL current nicknames list.
    """
    label = (body.label or "").strip()

    existing = db.execute(
        select(AccountNickname).where(AccountNickname.label == label)
    ).scalar_one_or_none()

    if existing is not None:
        existing.is_savings = body.is_savings
        if not body.is_savings and not (existing.nickname or "").strip():
            db.delete(existing)
    elif body.is_savings:
        db.add(AccountNickname(label=label, nickname="", is_savings=True))
    # (is_savings False + no existing row => nothing to persist)
    db.commit()

    _backfill_savings_remap(db, label)

    return _all_nicknames(db)


def _backfill_savings_remap(db: Session, label: str) -> None:
    """Best-effort: re-run transaction sync for every PlaidItem that owns `label`
    so its existing rows pick up the new savings mapping. Never raises; no-ops
    when Plaid is unconfigured."""
    if not plaid_client.is_configured():
        return
    try:
        from .plaid_routes import (
            _sync_transactions_for_item,
            _savings_labels,
            _accounts_map,
        )

        client = plaid_client.get_client()
        savings_labels = _savings_labels(db)
        items = db.execute(select(PlaidItem)).scalars().all()
        touched = False
        for item in items:
            accounts = _accounts_map(item)
            owns = any(
                isinstance(info, dict) and info.get("app_account") == label
                for info in accounts.values()
            )
            if not owns:
                continue
            item.cursor = None  # replay full history so existing rows re-map
            _sync_transactions_for_item(client, db, item, savings_labels)
            touched = True
        if touched:
            db.commit()
    except Exception:
        # Backfill is best-effort — a Plaid/network failure must not fail the toggle.
        pass


@router.delete("/nicknames/{label}", status_code=204)
def delete_nickname(label: str, db: Session = Depends(get_db)):
    """Delete a nickname by label. Idempotent: 204 whether or not it existed."""
    existing = db.execute(
        select(AccountNickname).where(AccountNickname.label == label)
    ).scalar_one_or_none()
    if existing is not None:
        db.delete(existing)
        db.commit()
    return Response(status_code=204)
