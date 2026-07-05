from datetime import date
from typing import Optional, Literal
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import select, func, and_, case

from ..database import get_db
from ..models import Transaction
from ..schemas import SummaryResponse, CategoryStat, OverTimeStat, AccountStat

router = APIRouter(prefix="/stats", tags=["stats"])

SAVINGS_CATEGORIES = ("Savings", "Investment")


def _date_filter(start_date, end_date, account=None):
    # v2: transfers (money between the user's own accounts) are EXCLUDED from
    # ALL stats — income, expense, net, savings, savings_rate, by-category,
    # over-time, and the summary row count.
    # v4: optional account filter — restrict to a single card/account when given.
    # v5.4: `refund` is NOT excluded — it participates as a NEGATIVE expense.
    conditions = [Transaction.type != "transfer"]
    if start_date:
        conditions.append(Transaction.date >= start_date)
    if end_date:
        conditions.append(Transaction.date <= end_date)
    if account:
        conditions.append(Transaction.account == account)
    return conditions


# v5.4: net-expense contribution of a row = +amount for expense, −amount for
# refund, 0 otherwise. Summing this over a group yields Σ(expense) − Σ(refund).
_NET_EXPENSE = case(
    (Transaction.type == "expense", Transaction.amount),
    (Transaction.type == "refund", -Transaction.amount),
    else_=0.0,
)

# v6: dialect-aware period-label formats for /stats/over-time. SQLite uses
# strftime; Postgres uses to_char. Both produce the SAME output labels:
#   year YYYY / month YYYY-MM / week YYYY-Www / day YYYY-MM-DD.
_SQLITE_PERIOD_FMT = {
    "year": "%Y",
    "month": "%Y-%m",
    "week": "%Y-W%W",
    "day": "%Y-%m-%d",
}
_POSTGRES_PERIOD_FMT = {
    "year": "YYYY",
    "month": "YYYY-MM",
    "week": 'IYYY-"W"IW',
    "day": "YYYY-MM-DD",
}


def _period_expr(db, granularity: str):
    """Return a SQL expression producing the period label for the DB's dialect."""
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        return func.to_char(Transaction.date, _POSTGRES_PERIOD_FMT[granularity])
    # sqlite (and any other dialect that supports strftime) — default/existing path
    return func.strftime(_SQLITE_PERIOD_FMT[granularity], Transaction.date)


@router.get("/summary", response_model=SummaryResponse)
def get_summary(
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    account: Optional[str] = None,   # plain default so direct (non-HTTP) callers like ai.py work
    db: Session = Depends(get_db),
):
    cond = _date_filter(start_date, end_date, account)
    base = select(
        func.coalesce(
            func.sum(case((Transaction.type == "income", Transaction.amount), else_=0.0)), 0.0
        ).label("total_income"),
        # v5.4: total_expense = Σ(expense) − Σ(refund)
        func.coalesce(func.sum(_NET_EXPENSE), 0.0).label("total_expense"),
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
        func.count(Transaction.id).label("count"),
    )
    if cond:
        base = base.where(and_(*cond))

    row = db.execute(base).one()
    total_income = float(row.total_income)
    total_expense = float(row.total_expense)
    savings = float(row.savings)
    net = total_income - total_expense
    savings_rate = (savings / total_income) if total_income > 0 else 0.0

    return SummaryResponse(
        total_income=total_income,
        total_expense=total_expense,
        net=net,
        savings=savings,
        savings_rate=round(savings_rate, 4),
        count=int(row.count),
    )


@router.get("/by-category", response_model=list[CategoryStat])
def get_by_category(
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    type: Optional[str] = Query("expense"),
    account: Optional[str] = None,   # plain default so direct (non-HTTP) callers like ai.py work
    db: Session = Depends(get_db),
):
    cond = _date_filter(start_date, end_date, account)

    if type == "expense":
        # v5.4: net expense per category = Σ(expense) − Σ(refund). Include both
        # expense and refund rows; the amount expression handles the sign.
        cond.append(Transaction.type.in_(("expense", "refund")))
        total_expr = func.sum(_NET_EXPENSE)
    else:
        if type:
            cond.append(Transaction.type == type)
        total_expr = func.sum(Transaction.amount)

    stmt = (
        select(
            Transaction.category,
            total_expr.label("total"),
            func.count(Transaction.id).label("count"),
        )
        .group_by(Transaction.category)
        .order_by(total_expr.desc())
    )
    if cond:
        stmt = stmt.where(and_(*cond))

    rows = db.execute(stmt).all()
    grand_total = sum(r.total for r in rows) or 1.0  # avoid div-by-zero

    return [
        CategoryStat(
            category=r.category,
            total=round(float(r.total), 2),
            count=int(r.count),
            pct=round(float(r.total) / grand_total * 100, 2),
        )
        for r in rows
    ]


@router.get("/over-time", response_model=list[OverTimeStat])
def get_over_time(
    granularity: Literal["year", "month", "week", "day"] = Query("month"),
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    account: Optional[str] = None,   # plain default so direct (non-HTTP) callers like ai.py work
    db: Session = Depends(get_db),
):
    cond = _date_filter(start_date, end_date, account)

    # v6: dialect-aware period label — same output formats on SQLite and Postgres.
    period_expr = _period_expr(db, granularity)

    stmt = (
        select(
            period_expr.label("period"),
            func.coalesce(
                func.sum(case((Transaction.type == "income", Transaction.amount), else_=0.0)), 0.0
            ).label("income"),
            # v5.4: expense = Σ(expense) − Σ(refund) for the period
            func.coalesce(func.sum(_NET_EXPENSE), 0.0).label("expense"),
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
        )
        .group_by(period_expr)
        .order_by(period_expr)
    )
    if cond:
        stmt = stmt.where(and_(*cond))

    rows = db.execute(stmt).all()
    return [
        OverTimeStat(
            period=r.period,
            income=round(float(r.income), 2),
            expense=round(float(r.expense), 2),
            net=round(float(r.income) - float(r.expense), 2),
            savings=round(float(r.savings), 2),
        )
        for r in rows
    ]


@router.get("/by-account", response_model=list[AccountStat])
def get_by_account(
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    db: Session = Depends(get_db),
):
    """Per-card breakdown (v4). Excludes transfers. Null/empty account => 'Unassigned'.
    Descending by expense."""
    cond = _date_filter(start_date, end_date)

    # Normalize null/empty account to "Unassigned" for grouping + output.
    account_label = func.coalesce(func.nullif(Transaction.account, ""), "Unassigned")

    stmt = (
        select(
            account_label.label("account"),
            func.coalesce(
                func.sum(case((Transaction.type == "income", Transaction.amount), else_=0.0)), 0.0
            ).label("income"),
            # v5.4: expense nets refunds = Σ(expense) − Σ(refund)
            func.coalesce(func.sum(_NET_EXPENSE), 0.0).label("expense"),
            func.count(Transaction.id).label("count"),
        )
        .where(and_(*cond))
        .group_by(account_label)
        .order_by(func.coalesce(func.sum(_NET_EXPENSE), 0.0).desc())
    )

    rows = db.execute(stmt).all()
    return [
        AccountStat(
            account=r.account,
            income=round(float(r.income), 2),
            expense=round(float(r.expense), 2),
            net=round(float(r.income) - float(r.expense), 2),
            count=int(r.count),
        )
        for r in rows
    ]
