from __future__ import annotations
from datetime import date, datetime
from typing import Literal, Optional
from pydantic import BaseModel, field_validator


class TransactionCreate(BaseModel):
    date: date
    amount: float
    type: Literal["income", "expense", "transfer", "refund"]
    category: str
    description: Optional[str] = None
    account: Optional[str] = None
    needs_review: bool = False
    review_reason: Optional[str] = None
    source: Literal["manual", "csv"] = "manual"

    @field_validator("amount")
    @classmethod
    def amount_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("amount must be > 0")
        return v

    @field_validator("category")
    @classmethod
    def category_nonempty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("category must not be empty")
        return v.strip()


class TransactionOut(TransactionCreate):
    id: int
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Stats schemas ────────────────────────────────────────────────────────────

class SummaryResponse(BaseModel):
    total_income: float
    total_expense: float
    net: float
    savings: float
    savings_rate: float
    count: int


class CategoryStat(BaseModel):
    category: str
    total: float
    count: int
    pct: float


class OverTimeStat(BaseModel):
    period: str          # YYYY / YYYY-MM / YYYY-Www / YYYY-MM-DD
    income: float
    expense: float
    net: float
    savings: float


class AccountStat(BaseModel):
    account: str         # "Unassigned" for null/empty (v4)
    income: float
    expense: float
    net: float
    count: int


# ── CSV import response ──────────────────────────────────────────────────────

class CSVErrorRow(BaseModel):
    row: int
    reason: str


class CSVImportResponse(BaseModel):
    imported: int
    skipped: int
    transfers: int
    needs_review: int = 0
    batch_id: Optional[int] = None  # v5.2
    errors: list[CSVErrorRow]


# ── Import batches (v5.2) ─────────────────────────────────────────────────────

class ImportBatchOut(BaseModel):
    id: int
    filename: str
    account: Optional[str]
    statement_type: Optional[str] = None  # card | bank (v5.3)
    imported: int
    skipped: int
    transfers: int
    needs_review: int
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Rules (v5) ─────────────────────────────────────────────────────────────

MatchField = Literal["description", "category", "account", "any"]
MatchOp = Literal["contains", "equals", "regex"]
SetType = Literal["income", "expense", "transfer", "refund"]


class RuleCreate(BaseModel):
    name: Optional[str] = None
    priority: int = 100
    enabled: bool = True
    match_field: MatchField
    match_op: MatchOp
    match_value: str
    amount_min: Optional[float] = None
    amount_max: Optional[float] = None
    set_type: Optional[SetType] = None
    set_category: Optional[str] = None
    set_account: Optional[str] = None

    @field_validator("match_value")
    @classmethod
    def match_value_nonempty(cls, v: str) -> str:
        if v is None or not str(v).strip():
            raise ValueError("match_value must not be empty")
        return v


class RuleUpdate(BaseModel):
    """All fields optional — partial update."""
    name: Optional[str] = None
    priority: Optional[int] = None
    enabled: Optional[bool] = None
    match_field: Optional[MatchField] = None
    match_op: Optional[MatchOp] = None
    match_value: Optional[str] = None
    amount_min: Optional[float] = None
    amount_max: Optional[float] = None
    set_type: Optional[SetType] = None
    set_category: Optional[str] = None
    set_account: Optional[str] = None


class RuleOut(BaseModel):
    id: int
    name: Optional[str]
    priority: int
    enabled: bool
    match_field: str
    match_op: str
    match_value: str
    amount_min: Optional[float]
    amount_max: Optional[float]
    set_type: Optional[str]
    set_category: Optional[str]
    set_account: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}
