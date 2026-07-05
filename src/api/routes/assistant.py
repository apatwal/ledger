"""
AI assistant routes — Gemini-backed Q&A, auto-categorize, and insights.

All AI endpoints degrade gracefully: when GEMINI_API_KEY is unset they return 503
so the frontend can hide the affordances. Provider errors map to 502.
"""
from datetime import date
from typing import Optional

import sqlalchemy
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, and_
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Transaction
from .. import ai

# Minimum confidence to auto-apply an AI category suggestion + clear needs_review.
CATEGORIZE_CONFIDENCE_THRESHOLD = 0.6

router = APIRouter(prefix="/assistant", tags=["assistant"])

_FALLBACK_CATEGORIES = [
    "Salary", "Freelance", "Investment Returns", "Rent", "Groceries", "Dining",
    "Transport", "Utilities", "Healthcare", "Entertainment", "Clothing",
    "Savings", "Investment", "Other",
]


# ── Request models ─────────────────────────────────────────────────────────────

class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]


class CategorizeRequest(BaseModel):
    description: str = ""
    amount: Optional[float] = None
    type: str = "expense"


class CategorizeBatchRequest(BaseModel):
    ids: Optional[list[int]] = None
    only_uncategorized: bool = False
    account: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    limit: int = 100   # safety cap on how many txns one batch will categorize


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.get("/status")
def status():
    """Whether AI features are configured (drives the frontend's show/hide)."""
    return {"enabled": ai.is_enabled()}


@router.post("/chat")
def chat(body: ChatRequest, db: Session = Depends(get_db)):
    if not ai.is_enabled():
        raise HTTPException(503, "AI assistant is not configured. Set GEMINI_API_KEY on the server.")
    if not body.messages:
        raise HTTPException(400, "messages must not be empty")
    try:
        return ai.run_assistant(db, [m.model_dump() for m in body.messages])
    except ai.AINotConfigured as e:
        raise HTTPException(503, str(e))
    except Exception as e:  # provider / network errors
        raise HTTPException(502, f"AI request failed: {e}")


@router.post("/categorize")
def categorize(body: CategorizeRequest, db: Session = Depends(get_db)):
    if not ai.is_enabled():
        raise HTTPException(503, "AI features are not configured. Set GEMINI_API_KEY on the server.")
    rows = db.execute(
        sqlalchemy.text("SELECT DISTINCT category FROM transactions ORDER BY category")
    ).scalars().all()
    known = list(rows) or _FALLBACK_CATEGORIES
    try:
        return ai.suggest_category(body.description, body.amount, body.type, known)
    except ai.AINotConfigured as e:
        raise HTTPException(503, str(e))
    except Exception as e:
        raise HTTPException(502, f"AI request failed: {e}")


@router.post("/categorize-batch")
def categorize_batch(body: CategorizeBatchRequest, db: Session = Depends(get_db)):
    """AI-categorize a set of transactions on demand (v5). Updates each txn's
    category and clears needs_review when the suggestion is confident."""
    if not ai.is_enabled():
        raise HTTPException(503, "AI features are not configured. Set GEMINI_API_KEY on the server.")

    # Build the target set (direct query — no FastAPI Query sentinels passed in).
    conditions = []
    if body.ids:
        conditions.append(Transaction.id.in_(body.ids))
    if body.only_uncategorized:
        conditions.append(Transaction.category == "Uncategorized")
    if body.account:
        conditions.append(Transaction.account == body.account)
    if body.start_date:
        conditions.append(Transaction.date >= body.start_date)
    if body.end_date:
        conditions.append(Transaction.date <= body.end_date)

    stmt = select(Transaction).order_by(Transaction.date.desc(), Transaction.id.desc())
    if conditions:
        stmt = stmt.where(and_(*conditions))
    stmt = stmt.limit(min(max(body.limit, 1), 500))
    targets = db.execute(stmt).scalars().all()

    known = db.execute(
        sqlalchemy.text("SELECT DISTINCT category FROM transactions ORDER BY category")
    ).scalars().all()
    known = [c for c in known if c and c != "Uncategorized"] or _FALLBACK_CATEGORIES

    results = []
    for t in targets:
        try:
            suggestion = ai.suggest_category(t.description or "", t.amount, t.type, known)
        except ai.AINotConfigured as e:
            raise HTTPException(503, str(e))
        except Exception as e:
            raise HTTPException(502, f"AI request failed: {e}")
        category = suggestion["category"]
        confidence = suggestion["confidence"]
        if confidence >= CATEGORIZE_CONFIDENCE_THRESHOLD and category:
            t.category = category
            t.needs_review = False
            t.review_reason = None
        results.append({"id": t.id, "category": category, "confidence": confidence})

    db.commit()
    return {"results": results}


@router.get("/insights")
def insights(
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    db: Session = Depends(get_db),
):
    if not ai.is_enabled():
        raise HTTPException(503, "AI features are not configured. Set GEMINI_API_KEY on the server.")
    try:
        return {"insights": ai.generate_insights(db, start_date, end_date)}
    except ai.AINotConfigured as e:
        raise HTTPException(503, str(e))
    except Exception as e:
        raise HTTPException(502, f"AI request failed: {e}")
