"""
Investments visualizations (v11).

Portfolio value over time. On every Plaid sync (and on demand via POST
/api/investments/snapshot) we compute current holdings, aggregate value per
account + a grand total, and upsert ONE PortfolioSnapshot per (account, calendar
day). `account=NULL` is the total series; a non-null value is that account's
series. At most one point per (account, day): re-recording the same day updates
the value in place, so the series is one clean point per day.

`record_portfolio_snapshot(db)` is the reusable recorder — called best-effort at
the end of the sync flows in plaid_routes.py (never raises there) and directly by
the POST endpoint. Everything network-touching is GATED by Plaid config; the POST
endpoint returns 503 when Plaid is unconfigured (mirrors the other Plaid routes).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import PortfolioSnapshot
from ..schemas import SnapshotOut
from .. import plaid_client
from .plaid_routes import _require_configured

router = APIRouter(prefix="/investments", tags=["investments"])


# ── Snapshot recorder (shared by the route + the sync flows) ──────────────────

def _day_bounds(day: date) -> tuple[datetime, datetime]:
    """[start-of-day, start-of-next-day) as datetimes, for the given calendar day."""
    start = datetime(day.year, day.month, day.day)
    return start, start + timedelta(days=1)


def _upsert_snapshot(
    db: Session, account: Optional[str], value: float, currency: Optional[str], day: date
) -> None:
    """Insert or update TODAY's PortfolioSnapshot for one account (NULL = total).
    At most one row per (account, calendar day): if today's row exists, update it."""
    start, nxt = _day_bounds(day)
    acct_cond = (
        PortfolioSnapshot.account.is_(None)
        if account is None
        else PortfolioSnapshot.account == account
    )
    existing = db.execute(
        select(PortfolioSnapshot).where(
            acct_cond,
            PortfolioSnapshot.captured_at >= start,
            PortfolioSnapshot.captured_at < nxt,
        )
    ).scalars().first()
    if existing is not None:
        existing.value = value
        existing.currency = currency
    else:
        db.add(
            PortfolioSnapshot(
                captured_at=datetime.utcnow(),
                account=account,
                value=value,
                currency=currency,
            )
        )


def record_portfolio_snapshot(db: Session) -> dict:
    """Compute current holdings, aggregate value per account + grand total, and
    upsert one PortfolioSnapshot per account plus one total row (account=NULL) for
    TODAY. Assumes Plaid is configured (callers gate first). Commits.
    Returns {captured, total_value}."""
    from .plaid_routes import compute_holdings

    holdings = compute_holdings(db)

    per_account: dict[str, float] = {}
    total = 0.0
    currency: Optional[str] = None
    for h in holdings:
        if h.value is None:
            continue
        acct = h.account or "Unassigned"
        per_account[acct] = per_account.get(acct, 0.0) + h.value
        total += h.value
        if currency is None and h.currency:
            currency = h.currency

    today = date.today()
    captured = 0
    for acct, val in per_account.items():
        _upsert_snapshot(db, acct, round(val, 2), currency, today)
        captured += 1
    # Always record the grand-total row (account=NULL), even at 0, so the total
    # series has a point for today.
    _upsert_snapshot(db, None, round(total, 2), currency, today)
    captured += 1

    db.commit()
    return {"captured": captured, "total_value": round(total, 2)}


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/snapshot")
def create_snapshot(db: Session = Depends(get_db)):
    """Capture the portfolio value NOW: upsert TODAY's per-account + total rows.
    503 if Plaid is unconfigured. Returns {captured, total_value}."""
    _require_configured()
    try:
        return record_portfolio_snapshot(db)
    except plaid_client.PlaidNotConfigured as e:
        from fastapi import HTTPException

        raise HTTPException(503, str(e))


@router.get("/snapshots", response_model=list[SnapshotOut])
def list_snapshots(
    account: Optional[str] = Query(None),
    days: Optional[int] = Query(None, ge=1),
    db: Session = Depends(get_db),
):
    """Portfolio value series ordered by captured_at asc. No `account` => the TOTAL
    series (account IS NULL). `account=<label>` => that account's series. `days`
    limits to the last N days."""
    acct_cond = (
        PortfolioSnapshot.account.is_(None)
        if account is None
        else PortfolioSnapshot.account == account
    )
    stmt = select(PortfolioSnapshot).where(acct_cond)
    if days is not None:
        cutoff = datetime.utcnow() - timedelta(days=days)
        stmt = stmt.where(PortfolioSnapshot.captured_at >= cutoff)
    stmt = stmt.order_by(PortfolioSnapshot.captured_at.asc())

    rows = db.execute(stmt).scalars().all()
    return [
        SnapshotOut(
            captured_at=r.captured_at,
            account=r.account,
            value=r.value,
            currency=r.currency,
        )
        for r in rows
    ]
