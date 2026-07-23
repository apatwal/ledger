"""Rules CRUD + apply + preview (v5)."""
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, delete
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Rule, Transaction
from ..schemas import RuleCreate, RuleUpdate, RuleOut, RuleBulkDeleteRequest, RuleBulkDeleteResponse
from .. import rules_engine

router = APIRouter(prefix="/rules", tags=["rules"])


def _enabled_rules(db: Session) -> list[Rule]:
    return list(
        db.execute(
            select(Rule).where(Rule.enabled == True).order_by(Rule.priority.asc(), Rule.id.asc())  # noqa: E712
        ).scalars().all()
    )


# ── CRUD ──────────────────────────────────────────────────────────────────────

@router.post("", response_model=RuleOut, status_code=201)
def create_rule(body: RuleCreate, db: Session = Depends(get_db)):
    obj = Rule(**body.model_dump())
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


@router.get("", response_model=list[RuleOut])
def list_rules(enabled: Optional[bool] = Query(None), db: Session = Depends(get_db)):
    stmt = select(Rule).order_by(Rule.priority.asc(), Rule.id.asc())
    if enabled is not None:
        stmt = stmt.where(Rule.enabled == enabled)
    return db.execute(stmt).scalars().all()


@router.post("/bulk-delete", response_model=RuleBulkDeleteResponse)
def bulk_delete_rules(body: RuleBulkDeleteRequest, db: Session = Depends(get_db)):
    """Delete every Rule whose id is in `ids`, in one query. Empty list deletes
    nothing; unknown ids are silently ignored. Returns the count actually deleted."""
    if not body.ids:
        return RuleBulkDeleteResponse(deleted=0)
    # Single bulk DELETE (one round-trip) instead of load-then-delete-per-row —
    # rowcount is the number actually removed; unknown ids are simply not matched.
    result = db.execute(delete(Rule).where(Rule.id.in_(body.ids)))
    db.commit()
    return RuleBulkDeleteResponse(deleted=result.rowcount or 0)


@router.get("/{rule_id}", response_model=RuleOut)
def get_rule(rule_id: int, db: Session = Depends(get_db)):
    obj = db.get(Rule, rule_id)
    if obj is None:
        raise HTTPException(status_code=404, detail=f"Rule {rule_id} not found")
    return obj


@router.put("/{rule_id}", response_model=RuleOut)
def update_rule(rule_id: int, body: RuleUpdate, db: Session = Depends(get_db)):
    obj = db.get(Rule, rule_id)
    if obj is None:
        raise HTTPException(status_code=404, detail=f"Rule {rule_id} not found")
    # partial update — only set provided fields
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(obj, field, value)
    db.commit()
    db.refresh(obj)
    return obj


@router.delete("/{rule_id}", status_code=204)
def delete_rule(rule_id: int, db: Session = Depends(get_db)):
    obj = db.get(Rule, rule_id)
    if obj is None:
        raise HTTPException(status_code=404, detail=f"Rule {rule_id} not found")
    db.delete(obj)
    db.commit()


# ── Apply / Preview ─────────────────────────────────────────────────────────

class ApplyRequest(BaseModel):
    account: Optional[str] = None
    only_review: bool = False


class ApplyResponse(BaseModel):
    updated: int


@router.post("/apply", response_model=ApplyResponse)
def apply_rules_to_existing(body: ApplyRequest, db: Session = Depends(get_db)):
    """Re-apply enabled rules to existing transactions. Updates type/category/account
    where a rule's non-null action differs; clears needs_review when a rule matches."""
    rules = _enabled_rules(db)
    stmt = select(Transaction)
    if body.account:
        stmt = stmt.where(Transaction.account == body.account)
    if body.only_review:
        stmt = stmt.where(Transaction.needs_review == True)  # noqa: E712
    txns = db.execute(stmt).scalars().all()

    updated = 0
    for t in txns:
        hit = rules_engine.apply_rules(
            rules,
            description=t.description,
            category=t.category,
            account=t.account,
            amount=t.amount,
        )
        if hit is None:
            continue
        changed = False
        if hit.set_type and hit.set_type != t.type:
            t.type = hit.set_type
            changed = True
        if hit.set_category and hit.set_category != t.category:
            t.category = hit.set_category
            changed = True
        if hit.set_account and hit.set_account != t.account:
            t.account = hit.set_account
            changed = True
        # a matching rule resolves the row — clear the review flag
        if t.needs_review:
            t.needs_review = False
            t.review_reason = None
            changed = True
        if changed:
            updated += 1

    db.commit()
    return ApplyResponse(updated=updated)


class PreviewResponse(BaseModel):
    matches: int


@router.post("/preview", response_model=PreviewResponse)
def preview_rule(body: RuleCreate, db: Session = Depends(get_db)):
    """Count how many existing transactions this (not-yet-saved) rule would hit."""
    # Build a transient rule-like object; id ordering is irrelevant for a single rule.
    candidate = Rule(id=0, **body.model_dump())
    txns = db.execute(select(Transaction)).scalars().all()
    matches = sum(
        1
        for t in txns
        if rules_engine.rule_matches(
            candidate,
            description=t.description,
            category=t.category,
            account=t.account,
            amount=t.amount,
        )
    )
    return PreviewResponse(matches=matches)
