from datetime import date, datetime
from typing import Optional, Literal
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import select, func, and_, case

from ..database import get_db
from ..models import Transaction, PlaidItem
from ..schemas import (
    SummaryResponse,
    CategoryStat,
    OverTimeStat,
    AccountStat,
    NetWorthOut,
    InvestmentFlowPoint,
)
from ..account_filter import account_filter_condition
from .. import plaid_client

router = APIRouter(prefix="/stats", tags=["stats"])

SAVINGS_CATEGORIES = ("Savings", "Investment")


def _date_filter(start_date, end_date, account=None, accounts=None):
    # v2: transfers (money between the user's own accounts) are EXCLUDED from
    # ALL stats — income, expense, net, savings, savings_rate, by-category,
    # over-time, and the summary row count.
    # v4: optional account filter — restrict to a single card/account when given.
    # v9: optional `accounts` comma-list filter (wins over single `account`).
    # v5.4: `refund` is NOT excluded — it participates as a NEGATIVE expense.
    conditions = [Transaction.type != "transfer"]
    if start_date:
        conditions.append(Transaction.date >= start_date)
    if end_date:
        conditions.append(Transaction.date <= end_date)
    acct_cond = account_filter_condition(account, accounts)
    if acct_cond is not None:
        conditions.append(acct_cond)
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
    accounts: Optional[str] = None,  # v9: comma-separated multi-account filter
    db: Session = Depends(get_db),
):
    cond = _date_filter(start_date, end_date, account, accounts)
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
    accounts: Optional[str] = None,  # v9: comma-separated multi-account filter
    db: Session = Depends(get_db),
):
    cond = _date_filter(start_date, end_date, account, accounts)

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
    accounts: Optional[str] = None,  # v9: comma-separated multi-account filter
    db: Session = Depends(get_db),
):
    cond = _date_filter(start_date, end_date, account, accounts)

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


# ── Investment flows (v11) ─────────────────────────────────────────────────────
# Investment cash flow per period, derived from the "Investment" category (the
# single category assigned to every investment transaction by plaid_mapping.py):
#   contributions = money directed INTO investments = Σ amount for rows where
#                   category = "Investment" AND type = "expense". Per the mapping,
#                   buys/deposits/contributions are neutralized as `transfer` EXCEPT
#                   cash contributions/deposits which map to `expense` (they count
#                   toward savings). Manual "Investment"/expense rows (e.g. an ETF
#                   purchase) are included too.
#   dividends     = investment income = Σ amount for rows where category =
#                   "Investment" AND type = "income" (dividend/interest cash rows).
# Transfers (portfolio buy/sell churn) are intentionally NOT counted in either.
# Mirrors /stats/over-time: returns ONLY periods with data (no zero-fill).

INVESTMENT_CATEGORY = "Investment"

_INV_CONTRIB = case(
    (and_(Transaction.category == INVESTMENT_CATEGORY, Transaction.type == "expense"),
     Transaction.amount),
    else_=0.0,
)
_INV_DIVIDEND = case(
    (and_(Transaction.category == INVESTMENT_CATEGORY, Transaction.type == "income"),
     Transaction.amount),
    else_=0.0,
)


@router.get("/investment-flows", response_model=list[InvestmentFlowPoint])
def get_investment_flows(
    granularity: Literal["year", "month", "week", "day"] = Query("month"),
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    db: Session = Depends(get_db),
):
    """Investment contributions vs dividends per period. See the module comment
    above for the exact definitions. Grouped by period label (YYYY / YYYY-MM /
    YYYY-Www / YYYY-MM-DD), ordered ascending, periods-with-data only."""
    period_expr = _period_expr(db, granularity)

    conditions = [Transaction.category == INVESTMENT_CATEGORY]
    if start_date:
        conditions.append(Transaction.date >= start_date)
    if end_date:
        conditions.append(Transaction.date <= end_date)

    stmt = (
        select(
            period_expr.label("period"),
            func.coalesce(func.sum(_INV_CONTRIB), 0.0).label("contributions"),
            func.coalesce(func.sum(_INV_DIVIDEND), 0.0).label("dividends"),
        )
        .where(and_(*conditions))
        .group_by(period_expr)
        .order_by(period_expr)
    )

    rows = db.execute(stmt).all()
    return [
        InvestmentFlowPoint(
            period=r.period,
            contributions=round(float(r.contributions), 2),
            dividends=round(float(r.dividends), 2),
        )
        for r in rows
    ]


# ── Net worth (v11b) ────────────────────────────────────────────────────────────
# Derived from each linked PlaidItem's accounts_json balances + live holdings.
#
# No-double-count rule: an investment/brokerage account's `current` balance
# usually ALREADY equals the sum of that account's holdings. To avoid counting
# the money twice we pick ONE source per account type:
#   * depository (checking/savings) -> use the cash balance `current`
#     (fallback `available`).
#   * investment / brokerage         -> IGNORE the cash `current` and instead use
#     the holdings-derived total from compute_holdings(db) (added ONCE, in full).
#   * credit / loan                  -> `current` counts as a LIABILITY.
#   * anything else                  -> ignored (neither asset nor liability).
# assets      = Σ depository current  +  Σ all holdings value
# liabilities = Σ (credit + loan) current
# net_worth   = assets − liabilities
_ASSET_DEPOSITORY = {"depository"}
_ASSET_INVESTMENT = {"investment", "brokerage"}
_LIABILITY_TYPES = {"credit", "loan"}


@router.get("/net-worth", response_model=NetWorthOut)
def get_net_worth(db: Session = Depends(get_db)):
    """Net worth from linked Plaid account balances + holdings. Returns 200 with
    zeros when Plaid is unconfigured / there are no items (graceful empty state,
    NOT 503). See the module comment above for the no-double-count rule."""
    now = datetime.utcnow()

    items = db.execute(select(PlaidItem)).scalars().all()
    if not plaid_client.is_configured() or not items:
        return NetWorthOut(
            net_worth=0.0, assets=0.0, liabilities=0.0, currency="USD", as_of=now
        )

    from .plaid_routes import _accounts_map, compute_holdings

    assets = 0.0
    liabilities = 0.0
    currency: Optional[str] = None

    for item in items:
        for info in _accounts_map(item).values():
            if not isinstance(info, dict):
                continue
            acct_type = str(info.get("type") or "").strip().lower()
            if currency is None and info.get("currency"):
                currency = info.get("currency")
            # investment/brokerage cash balance is intentionally skipped — the
            # holdings total (added below) is the single source for those.
            if acct_type in _ASSET_INVESTMENT:
                continue
            current = info.get("current")
            balance = current if current is not None else info.get("available")
            if balance is None:
                continue
            try:
                balance = float(balance)
            except (TypeError, ValueError):
                continue
            if acct_type in _ASSET_DEPOSITORY:
                assets += balance
            elif acct_type in _LIABILITY_TYPES:
                liabilities += balance

    # Holdings total (best-effort — never fail the endpoint on a Plaid error).
    try:
        holdings_total = sum(h.value for h in compute_holdings(db) if h.value is not None)
    except Exception:
        holdings_total = 0.0
    assets += holdings_total

    return NetWorthOut(
        net_worth=round(assets - liabilities, 2),
        assets=round(assets, 2),
        liabilities=round(liabilities, 2),
        currency=currency or "USD",
        as_of=now,
    )
