"""
Monthly spend recap (v12).

POST /api/reports/monthly computes a recap of a calendar month (default: the
PREVIOUS month relative to today, UTC) and pushes it via ntfy (reusing
notify.send_ntfy). Designed to be hit by a cron on the 1st of each month.

Semantics MATCH /stats/summary:
  * transfers are EXCLUDED everywhere;
  * net expense = Σ(expense) − Σ(refund);
  * savings = Σ amount where category ∈ {Savings, Investment};
  * savings_rate = savings / income (0 when income ≤ 0).

AUTH mirrors POST /plaid/sync-all EXACTLY: the path is exempt from Clerk auth in
auth.py when X-Plaid-Sync-Token matches PLAID_SYNC_TOKEN (the general cron
secret), and the endpoint ALSO checks the token (401 on mismatch when the token
is set). When PLAID_SYNC_TOKEN is unset the request falls through to normal Clerk
auth (so tests with auth disabled can call it).
"""
from __future__ import annotations

import calendar
import os
from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, func, and_, case
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Transaction
from ..notify import send_ntfy

router = APIRouter(prefix="/reports", tags=["reports"])

# Categories that are NOT "spending" for the top-spending list: savings/investing
# buckets, transfers, card payments, and any income-labelled rows.
_NON_SPENDING_CATEGORIES = {
    "Savings",
    "Investment",
    "Transfer",
    "Payments & Credits",
    "Income",
}
SAVINGS_CATEGORIES = ("Savings", "Investment")

# Net-expense contribution per row: +amount for expense, −amount for refund.
_NET_EXPENSE = case(
    (Transaction.type == "expense", Transaction.amount),
    (Transaction.type == "refund", -Transaction.amount),
    else_=0.0,
)


class MonthlyReportRequest(BaseModel):
    month: Optional[str] = None  # "YYYY-MM"; default = previous calendar month


def _previous_month(today: date) -> tuple[int, int]:
    """(year, month) of the calendar month BEFORE `today`'s month."""
    if today.month == 1:
        return today.year - 1, 12
    return today.year, today.month - 1


def _parse_month(month: Optional[str]) -> tuple[int, int]:
    """Parse 'YYYY-MM' into (year, month), else default to the previous month."""
    if month and month.strip():
        try:
            y_s, m_s = month.strip().split("-", 1)
            y, m = int(y_s), int(m_s)
            if 1 <= m <= 12 and y >= 1900:
                return y, m
        except (ValueError, TypeError):
            pass
        raise HTTPException(422, f"Invalid month '{month}'; expected YYYY-MM.")
    return _previous_month(datetime.utcnow().date())


def _month_range(year: int, month: int) -> tuple[date, date]:
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last_day)


def compute_monthly_report(db: Session, year: int, month: int) -> dict:
    """Compute the recap figures for [first_day, last_day] of the given month.
    Pure computation — no notification. Reused by the endpoint + tests."""
    first_day, last_day = _month_range(year, month)
    date_cond = [
        Transaction.type != "transfer",
        Transaction.date >= first_day,
        Transaction.date <= last_day,
    ]

    row = db.execute(
        select(
            func.coalesce(
                func.sum(case((Transaction.type == "income", Transaction.amount), else_=0.0)),
                0.0,
            ).label("income"),
            func.coalesce(func.sum(_NET_EXPENSE), 0.0).label("spent"),
            func.coalesce(
                func.sum(
                    case(
                        (
                            and_(
                                Transaction.type == "expense",
                                Transaction.category.in_(SAVINGS_CATEGORIES),
                            ),
                            Transaction.amount,
                        ),
                        else_=0.0,
                    )
                ),
                0.0,
            ).label("savings"),
            func.coalesce(
                func.sum(
                    case(
                        (
                            and_(
                                Transaction.type == "expense",
                                Transaction.category == "Savings",
                            ),
                            Transaction.amount,
                        ),
                        else_=0.0,
                    )
                ),
                0.0,
            ).label("saved"),
            func.coalesce(
                func.sum(
                    case(
                        (
                            and_(
                                Transaction.type == "expense",
                                Transaction.category == "Investment",
                            ),
                            Transaction.amount,
                        ),
                        else_=0.0,
                    )
                ),
                0.0,
            ).label("invested"),
        ).where(and_(*date_cond))
    ).one()

    income = float(row.income)
    spent = float(row.spent)
    savings = float(row.savings)
    saved = float(row.saved)
    invested = float(row.invested)
    net = income - spent
    savings_rate = (savings / income) if income > 0 else 0.0

    # Top spending categories: net expense per category, excluding the
    # non-spending buckets, positive totals only, biggest first, top 6.
    cat_rows = db.execute(
        select(
            Transaction.category.label("category"),
            func.coalesce(func.sum(_NET_EXPENSE), 0.0).label("total"),
        )
        .where(
            and_(
                Transaction.type.in_(("expense", "refund")),
                Transaction.date >= first_day,
                Transaction.date <= last_day,
                Transaction.category.notin_(tuple(_NON_SPENDING_CATEGORIES)),
            )
        )
        .group_by(Transaction.category)
        .order_by(func.coalesce(func.sum(_NET_EXPENSE), 0.0).desc())
    ).all()

    categories = [
        {"category": r.category, "amount": round(float(r.total), 2)}
        for r in cat_rows
        if float(r.total) > 0
    ][:6]

    return {
        "month": f"{year:04d}-{month:02d}",
        "first_day": first_day,
        "last_day": last_day,
        "income": round(income, 2),
        "spent": round(spent, 2),
        "saved": round(saved, 2),
        "invested": round(invested, 2),
        "savings": round(savings, 2),
        "savings_rate": round(savings_rate, 4),
        "net": round(net, 2),
        "categories": categories,
    }


def _format_message(report: dict) -> tuple[str, str]:
    """Build the (title, body) ntfy strings from a computed report."""
    first_day: date = report["first_day"]
    title = f"📊 {first_day.strftime('%B %Y')} recap"

    pct = round(report["savings_rate"] * 100)
    lines = [
        f"Income ${report['income']:,.0f} · Spent ${report['spent']:,.0f}",
        f"Saved ${report['saved']:,.0f} ({pct}%) · Invested ${report['invested']:,.0f}",
        "Top spending:",
    ]
    for c in report["categories"]:
        lines.append(f"• {c['category']} ${c['amount']:,.0f}")
    return title, "\n".join(lines)


@router.post("/monthly")
def monthly_report(
    body: Optional[MonthlyReportRequest] = None,
    month: Optional[str] = Query(None),
    x_plaid_sync_token: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
):
    """Compute the monthly recap and push it via ntfy. `month` may come from the
    query string or the JSON body ("YYYY-MM"); default = the previous calendar
    month. Auth mirrors /plaid/sync-all: when PLAID_SYNC_TOKEN is set the caller
    must send a matching X-Plaid-Sync-Token header (401 otherwise); when unset it
    falls through to normal Clerk auth."""
    required = (os.environ.get("PLAID_SYNC_TOKEN") or "").strip()
    if required and x_plaid_sync_token != required:
        raise HTTPException(401, "Invalid or missing X-Plaid-Sync-Token")

    month_str = month or (body.month if body else None)
    year, m = _parse_month(month_str)
    report = compute_monthly_report(db, year, m)

    title, message = _format_message(report)
    app_url = (os.environ.get("APP_URL") or "").strip().rstrip("/")
    sent = send_ntfy(
        title,
        message,
        click=f"{app_url}/dashboard" if app_url else None,
        tags="bar_chart",
    )

    return {
        "month": report["month"],
        "income": report["income"],
        "spent": report["spent"],
        "saved": report["saved"],
        "invested": report["invested"],
        "savings_rate": report["savings_rate"],
        "net": report["net"],
        "categories": report["categories"],
        "sent": bool(sent),
    }
