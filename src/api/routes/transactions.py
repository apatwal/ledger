from datetime import date
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import select, and_

from ..database import get_db
from ..models import Transaction
from ..schemas import TransactionCreate, TransactionOut
from ..account_filter import account_filter_condition, parse_accounts

router = APIRouter(prefix="/transactions", tags=["transactions"])


def _build_filters(
    db_query,
    start_date: Optional[date],
    end_date: Optional[date],
    type_: Optional[str],
    category: Optional[str],
    account: Optional[str] = None,
    needs_review: Optional[bool] = None,
    accounts: Optional[str] = None,
    exclude_types: Optional[str] = None,
    exclude_categories: Optional[str] = None,
):
    conditions = []
    if start_date:
        conditions.append(Transaction.date >= start_date)
    if end_date:
        conditions.append(Transaction.date <= end_date)
    if type_:
        conditions.append(Transaction.type == type_)
    if category:
        conditions.append(Transaction.category == category)
    acct_cond = account_filter_condition(account, accounts)
    if acct_cond is not None:
        conditions.append(acct_cond)
    if needs_review is not None:
        conditions.append(Transaction.needs_review == needs_review)
    # v9: comma-separated exclusion filters (trim/ignore empty tokens, mirror `accounts`).
    excluded_types = parse_accounts(exclude_types)
    if excluded_types is not None:
        conditions.append(Transaction.type.not_in(excluded_types))
    excluded_categories = parse_accounts(exclude_categories)
    if excluded_categories is not None:
        conditions.append(Transaction.category.not_in(excluded_categories))
    if conditions:
        db_query = db_query.where(and_(*conditions))
    return db_query


@router.post("", response_model=TransactionOut, status_code=201)
def create_transaction(body: TransactionCreate, db: Session = Depends(get_db)):
    obj = Transaction(**body.model_dump())
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


@router.get("", response_model=list[TransactionOut])
def list_transactions(
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    type: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    account: Optional[str] = Query(None),
    accounts: Optional[str] = Query(None),   # v9: comma-separated multi-account filter
    needs_review: Optional[bool] = Query(None),
    exclude_types: Optional[str] = Query(None),       # v9: comma-separated types to HIDE
    exclude_categories: Optional[str] = Query(None),  # v9: comma-separated categories to HIDE
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    stmt = select(Transaction).order_by(Transaction.date.desc(), Transaction.id.desc())
    stmt = _build_filters(
        stmt, start_date, end_date, type, category, account, needs_review, accounts,
        exclude_types=exclude_types, exclude_categories=exclude_categories,
    )
    stmt = stmt.offset(offset).limit(limit)
    return db.execute(stmt).scalars().all()


@router.get("/{transaction_id}", response_model=TransactionOut)
def get_transaction(transaction_id: int, db: Session = Depends(get_db)):
    obj = db.get(Transaction, transaction_id)
    if obj is None:
        raise HTTPException(status_code=404, detail=f"Transaction {transaction_id} not found")
    return obj


@router.put("/{transaction_id}", response_model=TransactionOut)
def update_transaction(
    transaction_id: int, body: TransactionCreate, db: Session = Depends(get_db)
):
    obj = db.get(Transaction, transaction_id)
    if obj is None:
        raise HTTPException(status_code=404, detail=f"Transaction {transaction_id} not found")
    for field, value in body.model_dump().items():
        setattr(obj, field, value)
    db.commit()
    db.refresh(obj)
    return obj


@router.delete("/{transaction_id}", status_code=204)
def delete_transaction(transaction_id: int, db: Session = Depends(get_db)):
    obj = db.get(Transaction, transaction_id)
    if obj is None:
        raise HTTPException(status_code=404, detail=f"Transaction {transaction_id} not found")
    db.delete(obj)
    db.commit()
