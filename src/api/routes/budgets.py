"""
Budgets (v9b): monthly category spending limits + savings goals.

Two independent budget kinds, both holistic (all accounts, independent of the
UI's account selection):
  * CategoryBudget  — a monthly spending limit for one category. Progress is the
    current CALENDAR-MONTH net expense for that category (Σ expense − Σ refund,
    reusing stats.py's net-expense expression). Calendar-month reset, NO rollover.
  * SavingsGoal     — a target amount (+ optional target date) tracked against a
    DESIGNATED connected account's balance growth. `starting_balance` is captured
    at creation from the account's current Plaid balance; progress = current
    balance − starting balance.

The create/upsert logic lives in module-level helpers (`create_category_budget`,
`create_savings_goal`) so the REST routes AND the Assistant budget-creation path
persist budgets identically. `_account_balance` is shared by goal creation +
progress. All computed fields are derived on read, never stored.
"""
from __future__ import annotations

import json
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, and_, func
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import CategoryBudget, SavingsGoal, Transaction, PlaidItem
from ..schemas import (
    CategoryBudgetCreate,
    CategoryBudgetUpdate,
    CategoryBudgetOut,
    SavingsGoalCreate,
    SavingsGoalUpdate,
    SavingsGoalOut,
)
from .stats import _NET_EXPENSE  # Σ expense − Σ refund per row (v5.4)

router = APIRouter(prefix="/budgets", tags=["budgets"])


# ── Shared date / balance helpers ─────────────────────────────────────────────

def _month_bounds(today: Optional[date] = None) -> tuple[date, date]:
    """[first-of-this-month, first-of-next-month) for the current calendar month."""
    today = today or date.today()
    start = today.replace(day=1)
    if start.month == 12:
        nxt = start.replace(year=start.year + 1, month=1)
    else:
        nxt = start.replace(month=start.month + 1)
    return start, nxt


def _month_spent(db: Session, category: str) -> float:
    """Current calendar-month net expense (Σ expense − Σ refund) for a category."""
    start, nxt = _month_bounds()
    total = db.execute(
        select(func.coalesce(func.sum(_NET_EXPENSE), 0.0)).where(
            and_(
                Transaction.category == category,
                Transaction.type.in_(("expense", "refund")),
                Transaction.date >= start,
                Transaction.date < nxt,
            )
        )
    ).scalar_one()
    return round(float(total or 0.0), 2)


def _account_balance(db: Session, app_account: Optional[str]) -> Optional[float]:
    """Latest balance for the connected account whose app_account label matches.
    Uses `current`, falling back to `available`. Returns None if no account is
    designated or no matching account is found. Shared by goal creation + progress."""
    if not app_account:
        return None
    items = db.execute(select(PlaidItem)).scalars().all()
    for item in items:
        if not item.accounts_json:
            continue
        try:
            accounts = json.loads(item.accounts_json)
        except (ValueError, TypeError):
            continue
        if not isinstance(accounts, dict):
            continue
        for info in accounts.values():
            if not isinstance(info, dict):
                continue
            if info.get("app_account") == app_account:
                bal = info.get("current")
                if bal is None:
                    bal = info.get("available")
                return float(bal) if bal is not None else None
    return None


def _account_labels(db: Session) -> list[str]:
    """Distinct connected-account labels (app_account) across all PlaidItems.
    Used to give the Assistant real account names to pick from."""
    labels: list[str] = []
    seen: set[str] = set()
    for item in db.execute(select(PlaidItem)).scalars().all():
        if not item.accounts_json:
            continue
        try:
            accounts = json.loads(item.accounts_json)
        except (ValueError, TypeError):
            continue
        if not isinstance(accounts, dict):
            continue
        for info in accounts.values():
            if isinstance(info, dict):
                label = info.get("app_account")
                if label and label not in seen:
                    seen.add(label)
                    labels.append(label)
    return labels


# ── Computed output builders ──────────────────────────────────────────────────

def _category_out(db: Session, b: CategoryBudget) -> CategoryBudgetOut:
    spent = _month_spent(db, b.category)
    remaining = round(b.limit_amount - spent, 2)
    pct = round(spent / b.limit_amount * 100, 2) if b.limit_amount else 0.0
    return CategoryBudgetOut(
        id=b.id,
        category=b.category,
        limit_amount=b.limit_amount,
        period=b.period,
        created_at=b.created_at,
        spent=spent,
        remaining=remaining,
        pct=pct,
        over=spent > b.limit_amount,
    )


def _months_until(target: date, today: Optional[date] = None) -> int:
    """Whole months from today to target_date, clamped to >= 1."""
    today = today or date.today()
    months = (target.year - today.year) * 12 + (target.month - today.month)
    if target.day < today.day:
        months -= 1
    return max(1, months)


def _goal_out(db: Session, g: SavingsGoal) -> SavingsGoalOut:
    current_balance = _account_balance(db, g.account)
    if current_balance is None:
        saved = 0.0
    else:
        saved = max(0.0, round(current_balance - g.starting_balance, 2))
    pct = round(saved / g.target_amount * 100, 2) if g.target_amount else 0.0
    remaining = round(max(0.0, g.target_amount - saved), 2)

    monthly_needed: Optional[float] = None
    on_track: Optional[bool] = None
    if g.target_date is not None:
        monthly_needed = round(remaining / _months_until(g.target_date), 2)
        # expected-by-now = target * elapsed_fraction of the creation→target timeline
        start = g.created_at.date() if g.created_at else date.today()
        total_days = (g.target_date - start).days
        if total_days <= 0:
            frac = 1.0
        else:
            elapsed = (date.today() - start).days
            frac = min(1.0, max(0.0, elapsed / total_days))
        on_track = saved >= g.target_amount * frac

    return SavingsGoalOut(
        id=g.id,
        name=g.name,
        target_amount=g.target_amount,
        target_date=g.target_date,
        account=g.account,
        starting_balance=g.starting_balance,
        created_at=g.created_at,
        current_balance=current_balance,
        saved=saved,
        pct=pct,
        remaining=remaining,
        monthly_needed=monthly_needed,
        on_track=on_track,
    )


# ── Shared create helpers (used by routes AND the Assistant) ──────────────────

def create_category_budget(
    db: Session, category: str, limit_amount: float, period: str = "monthly"
) -> CategoryBudget:
    """Upsert a category limit by category: update the limit if one already exists
    for that category, else insert. Commits. Returns the persisted row."""
    category = (category or "").strip()
    existing = db.execute(
        select(CategoryBudget).where(CategoryBudget.category == category)
    ).scalar_one_or_none()
    if existing is not None:
        existing.limit_amount = limit_amount
        if period:
            existing.period = period
        db.commit()
        db.refresh(existing)
        return existing
    obj = CategoryBudget(category=category, limit_amount=limit_amount, period=period or "monthly")
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


def create_savings_goal(
    db: Session,
    name: str,
    target_amount: float,
    target_date: Optional[date] = None,
    account: Optional[str] = None,
) -> SavingsGoal:
    """Create a savings goal, capturing the designated account's current Plaid
    balance as `starting_balance` (0 when unknown / no account). Commits."""
    starting = _account_balance(db, account) or 0.0
    obj = SavingsGoal(
        name=(name or "").strip(),
        target_amount=target_amount,
        target_date=target_date,
        account=account,
        starting_balance=float(starting),
    )
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


# ── Category-limit endpoints ──────────────────────────────────────────────────

@router.get("/categories", response_model=list[CategoryBudgetOut])
def list_category_budgets(db: Session = Depends(get_db)):
    rows = db.execute(
        select(CategoryBudget).order_by(CategoryBudget.category.asc())
    ).scalars().all()
    return [_category_out(db, b) for b in rows]


@router.post("/categories", response_model=CategoryBudgetOut, status_code=201)
def create_category_budget_route(body: CategoryBudgetCreate, db: Session = Depends(get_db)):
    obj = create_category_budget(db, body.category, body.limit_amount, body.period)
    return _category_out(db, obj)


@router.put("/categories/{budget_id}", response_model=CategoryBudgetOut)
def update_category_budget(budget_id: int, body: CategoryBudgetUpdate, db: Session = Depends(get_db)):
    obj = db.get(CategoryBudget, budget_id)
    if obj is None:
        raise HTTPException(404, f"Category budget {budget_id} not found")
    for field, value in body.model_dump(exclude_unset=True).items():
        if field == "category" and value is not None:
            value = value.strip()
        setattr(obj, field, value)
    db.commit()
    db.refresh(obj)
    return _category_out(db, obj)


@router.delete("/categories/{budget_id}", status_code=204)
def delete_category_budget(budget_id: int, db: Session = Depends(get_db)):
    obj = db.get(CategoryBudget, budget_id)
    if obj is None:
        raise HTTPException(404, f"Category budget {budget_id} not found")
    db.delete(obj)
    db.commit()


# ── Savings-goal endpoints ────────────────────────────────────────────────────

@router.get("/goals", response_model=list[SavingsGoalOut])
def list_savings_goals(db: Session = Depends(get_db)):
    rows = db.execute(
        select(SavingsGoal).order_by(SavingsGoal.created_at.asc(), SavingsGoal.id.asc())
    ).scalars().all()
    return [_goal_out(db, g) for g in rows]


@router.post("/goals", response_model=SavingsGoalOut, status_code=201)
def create_savings_goal_route(body: SavingsGoalCreate, db: Session = Depends(get_db)):
    obj = create_savings_goal(db, body.name, body.target_amount, body.target_date, body.account)
    return _goal_out(db, obj)


@router.put("/goals/{goal_id}", response_model=SavingsGoalOut)
def update_savings_goal(goal_id: int, body: SavingsGoalUpdate, db: Session = Depends(get_db)):
    obj = db.get(SavingsGoal, goal_id)
    if obj is None:
        raise HTTPException(404, f"Savings goal {goal_id} not found")
    for field, value in body.model_dump(exclude_unset=True).items():
        if field == "name" and value is not None:
            value = value.strip()
        setattr(obj, field, value)
    db.commit()
    db.refresh(obj)
    return _goal_out(db, obj)


@router.delete("/goals/{goal_id}", status_code=204)
def delete_savings_goal(goal_id: int, db: Session = Depends(get_db)):
    obj = db.get(SavingsGoal, goal_id)
    if obj is None:
        raise HTTPException(404, f"Savings goal {goal_id} not found")
    db.delete(obj)
    db.commit()
