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
from ..models import AccountNickname
from ..schemas import AccountNicknameIn, AccountNicknameOut

router = APIRouter(prefix="/accounts", tags=["accounts"])


@router.get("/nicknames", response_model=list[AccountNicknameOut])
def list_nicknames(db: Session = Depends(get_db)):
    """All account nicknames, ordered by label."""
    rows = db.execute(
        select(AccountNickname).order_by(AccountNickname.label.asc())
    ).scalars().all()
    return [AccountNicknameOut(label=r.label, nickname=r.nickname) for r in rows]


@router.put("/nicknames", response_model=list[AccountNicknameOut])
def upsert_nickname(body: AccountNicknameIn, db: Session = Depends(get_db)):
    """Upsert a nickname by `label`. The nickname is trimmed. If it is blank /
    whitespace-only, the row is DELETED instead (revert to the raw label).

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
        # Blank nickname => delete the row (revert to raw label). Idempotent.
        if existing is not None:
            db.delete(existing)
            db.commit()
    elif existing is not None:
        existing.nickname = nickname
        db.commit()
    else:
        db.add(AccountNickname(label=label, nickname=nickname))
        db.commit()

    rows = db.execute(
        select(AccountNickname).order_by(AccountNickname.label.asc())
    ).scalars().all()
    return [AccountNicknameOut(label=r.label, nickname=r.nickname) for r in rows]


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
