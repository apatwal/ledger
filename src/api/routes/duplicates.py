"""Duplicate charge detection (v7).

A "duplicate group" = 2+ EXPENSE transactions where ALL of these match:
  - type == "expense" (income/transfer/refund are ignored)
  - exact same `date`
  - exact same `amount` rounded to 2 decimals
  - same NORMALIZED description: " ".join((description or "").split()).lower()
  - same NORMALIZED account:     (account or "").strip().lower()

Rows with `dup_dismissed == True` are EXCLUDED from grouping entirely, so a
group only forms among non-dismissed rows (count >= 2). This single dynamic rule
catches BOTH real merchant double-charges AND accidentally re-imported /
overlapping-statement duplicates (a re-import yields identical
date+amount+merchant+account rows).

Grouping is done IN PYTHON (portable across SQLite/Postgres — no reliance on DB
string functions).
"""
from datetime import date
from typing import Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, and_, update
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Transaction
from ..schemas import DuplicateGroup, DismissDuplicatesRequest, TransactionOut
from ..account_filter import account_filter_condition

router = APIRouter(prefix="/duplicates", tags=["duplicates"])


def _norm_desc(description: Optional[str]) -> str:
    return " ".join((description or "").split()).lower()


def _norm_acct(account: Optional[str]) -> str:
    return (account or "").strip().lower()


@router.get("", response_model=list[DuplicateGroup])
def list_duplicates(
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    account: Optional[str] = Query(None),
    accounts: Optional[str] = Query(None),   # v9: comma-separated multi-account filter
    db: Session = Depends(get_db),
):
    """Find groups of 2+ non-dismissed expense transactions that share the same
    date, amount (2dp), normalized description, and normalized account.

    Optional `start_date`/`end_date`/`account`/`accounts` filters restrict the
    candidate rows (same style as stats.py). `accounts` (comma list) wins over
    the single `account`."""
    conditions = [Transaction.type == "expense", Transaction.dup_dismissed == False]  # noqa: E712
    if start_date:
        conditions.append(Transaction.date >= start_date)
    if end_date:
        conditions.append(Transaction.date <= end_date)
    acct_cond = account_filter_condition(account, accounts)
    if acct_cond is not None:
        conditions.append(acct_cond)

    stmt = (
        select(Transaction)
        .where(and_(*conditions))
        .order_by(Transaction.date.desc(), Transaction.id.desc())
    )
    rows = db.execute(stmt).scalars().all()

    # Group in Python by the normalized key.
    groups: dict[tuple, list[Transaction]] = {}
    for r in rows:
        amt = round(float(r.amount), 2)
        key = (r.date, amt, _norm_desc(r.description), _norm_acct(r.account))
        groups.setdefault(key, []).append(r)

    out: list[DuplicateGroup] = []
    for (grp_date, amt, normdesc, normacct), txns in groups.items():
        if len(txns) < 2:
            continue
        # rows already sorted newest-first by the query; keep that order.
        count = len(txns)
        out.append(
            DuplicateGroup(
                group_key=f"{grp_date}|{amt:.2f}|{normdesc}|{normacct}",
                date=grp_date,
                amount=amt,
                description=txns[0].description,
                account=txns[0].account,
                count=count,
                total_extra=round((count - 1) * amt, 2),
                transactions=[TransactionOut.model_validate(t) for t in txns],
            )
        )

    # Sort groups by total_extra desc (biggest wasted spend first).
    out.sort(key=lambda g: g.total_extra, reverse=True)
    return out


@router.post("/dismiss")
def dismiss_duplicates(body: DismissDuplicatesRequest, db: Session = Depends(get_db)):
    """Mark the given transactions as dismissed so they no longer form duplicate
    groups. Returns the number of rows actually updated (ids that exist)."""
    if not body.ids:
        return {"dismissed": 0}
    result = db.execute(
        update(Transaction)
        .where(Transaction.id.in_(body.ids))
        .values(dup_dismissed=True)
    )
    db.commit()
    return {"dismissed": int(result.rowcount or 0)}
