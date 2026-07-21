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
    dup_dismissed: bool = False   # v7
    source: Literal["manual", "csv", "plaid"] = "manual"

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
    # v8 Plaid provenance (null for manual/csv rows).
    plaid_transaction_id: Optional[str] = None
    plaid_account_id: Optional[str] = None
    plaid_item_id: Optional[int] = None
    # v9 Plaid enrichment (null for manual/csv rows).
    merchant_name: Optional[str] = None
    logo_url: Optional[str] = None
    pending: bool = False
    pending_transaction_id: Optional[str] = None
    category_icon_url: Optional[str] = None

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


# ── Duplicate detection (v7) ─────────────────────────────────────────────────

class DuplicateGroup(BaseModel):
    group_key: str
    date: date
    amount: float
    description: Optional[str]
    account: Optional[str]
    count: int
    total_extra: float
    transactions: list[TransactionOut]


class DismissDuplicatesRequest(BaseModel):
    ids: list[int]


# ── Plaid integration (v8) ────────────────────────────────────────────────────

class PlaidAccount(BaseModel):
    account_id: str
    name: Optional[str] = None
    mask: Optional[str] = None
    type: Optional[str] = None
    subtype: Optional[str] = None
    app_account: Optional[str] = None   # the label stored on synced transactions
    # v9 per-account balances (refreshed on exchange + every sync).
    available: Optional[float] = None
    current: Optional[float] = None
    currency: Optional[str] = None


class PlaidItemOut(BaseModel):
    """A linked bank Item. NEVER includes the access_token."""
    id: int
    item_id: str
    institution_id: Optional[str] = None
    institution_name: Optional[str] = None
    # v9 institution branding (best-effort; null when unavailable).
    institution_logo: Optional[str] = None   # base64 PNG string
    institution_color: Optional[str] = None  # hex color
    accounts: list[PlaidAccount] = []
    status: str
    last_synced_at: Optional[datetime] = None
    created_at: datetime


class PlaidStatus(BaseModel):
    configured: bool
    env: str
    products: list[str]
    items: list[PlaidItemOut] = []


class ExchangeRequest(BaseModel):
    public_token: str


class PlaidSyncRequest(BaseModel):
    item_id: Optional[int] = None   # null = sync all linked items


class PlaidSyncResult(BaseModel):
    items_synced: int
    added: int
    modified: int
    removed: int


# ── Budgets (v9b) ─────────────────────────────────────────────────────────────

class CategoryBudgetCreate(BaseModel):
    category: str
    limit_amount: float
    period: str = "monthly"

    @field_validator("category")
    @classmethod
    def category_nonempty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("category must not be empty")
        return v.strip()

    @field_validator("limit_amount")
    @classmethod
    def limit_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("limit_amount must be > 0")
        return v


class CategoryBudgetUpdate(BaseModel):
    """Partial update — all fields optional."""
    category: Optional[str] = None
    limit_amount: Optional[float] = None
    period: Optional[str] = None


class CategoryBudgetOut(BaseModel):
    id: int
    category: str
    limit_amount: float
    period: str
    created_at: datetime
    # computed for the current calendar month (not stored)
    spent: float
    remaining: float
    pct: float
    over: bool

    model_config = {"from_attributes": True}


class SavingsGoalCreate(BaseModel):
    name: str
    target_amount: float
    target_date: Optional[date] = None
    account: Optional[str] = None

    @field_validator("name")
    @classmethod
    def name_nonempty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("name must not be empty")
        return v.strip()

    @field_validator("target_amount")
    @classmethod
    def target_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("target_amount must be > 0")
        return v


class SavingsGoalUpdate(BaseModel):
    """Partial update — all fields optional. Does NOT recompute starting_balance."""
    name: Optional[str] = None
    target_amount: Optional[float] = None
    target_date: Optional[date] = None
    account: Optional[str] = None


class SavingsGoalOut(BaseModel):
    id: int
    name: str
    target_amount: float
    target_date: Optional[date]
    account: Optional[str]
    starting_balance: float
    created_at: datetime
    # computed progress (not stored)
    current_balance: Optional[float]
    saved: float
    pct: float
    remaining: float
    monthly_needed: Optional[float]
    on_track: Optional[bool]

    model_config = {"from_attributes": True}


# ── Assistant budget-creation (v9b) ───────────────────────────────────────────

class ChatMessageIn(BaseModel):
    role: str
    content: str


class BudgetChatRequest(BaseModel):
    """Chat turn(s) whose intent may be to CREATE budgets/goals. Mirrors the plain
    /assistant/chat shape (a list of role/content messages)."""
    messages: list[ChatMessageIn]


class BudgetCreated(BaseModel):
    goals: list[SavingsGoalOut] = []
    category_limits: list[CategoryBudgetOut] = []


class BudgetChatResponse(BaseModel):
    reply: str
    created: BudgetCreated


class HoldingOut(BaseModel):
    """One investment holding (computed on demand from investments/holdings/get)."""
    account: Optional[str] = None        # friendly app_account label
    institution: Optional[str] = None    # institution_name
    security_name: Optional[str] = None
    ticker_symbol: Optional[str] = None
    quantity: Optional[float] = None
    price: Optional[float] = None
    value: Optional[float] = None
    currency: Optional[str] = None
