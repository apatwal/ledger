from datetime import date, datetime
from sqlalchemy import Integer, Float, String, Text, Date, DateTime, Boolean, func
from sqlalchemy.orm import Mapped, mapped_column
from .database import Base


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    type: Mapped[str] = mapped_column(String(20), nullable=False)       # income | expense | transfer
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    account: Mapped[str | None] = mapped_column(String(100), nullable=True)  # which card/account (v4)
    needs_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)  # v5
    review_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)        # v5
    batch_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # import batch (v5.2); null = manual
    dup_dismissed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)  # v7
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="manual")  # manual | csv | plaid
    # v8 Plaid: null for manual/csv rows; set for bank-synced rows.
    plaid_transaction_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    plaid_account_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    plaid_item_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # v9 Plaid enrichment: null for manual/csv rows; populated for bank-synced rows.
    merchant_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    logo_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    pending: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    pending_transaction_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    category_icon_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())


class Rule(Base):
    """User-editable classification rule (v5). First enabled match (priority asc, then id) wins."""
    __tablename__ = "rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)  # lower runs first
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    match_field: Mapped[str] = mapped_column(String(20), nullable=False)   # description|category|account|any
    match_op: Mapped[str] = mapped_column(String(20), nullable=False)      # contains|equals|regex
    match_value: Mapped[str] = mapped_column(String(300), nullable=False)
    amount_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    amount_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    set_type: Mapped[str | None] = mapped_column(String(20), nullable=True)        # income|expense|transfer
    set_category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    set_account: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())


class PlaidItem(Base):
    """One linked Plaid Item (a bank login) (v8). The access_token is stored
    server-side ONLY and is NEVER returned by the API. `accounts_json` holds a
    JSON map of {account_id: {name, mask, type, subtype, app_account, available,
    current, currency}} so a sync can label each transaction's account and expose
    per-account balances. `cursor` persists the /transactions/sync incremental
    cursor. v9 adds institution branding (`institution_logo`/`institution_color`)."""
    __tablename__ = "plaid_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    item_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    access_token: Mapped[str] = mapped_column(String(512), nullable=False)
    institution_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    institution_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # v9 institution branding (best-effort; null when unavailable).
    institution_logo: Mapped[str | None] = mapped_column(Text, nullable=True)   # base64 PNG
    institution_color: Mapped[str | None] = mapped_column(String(32), nullable=True)  # hex color
    accounts_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    cursor: Mapped[str | None] = mapped_column(String(512), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())


class CategoryBudget(Base):
    """A monthly category spending limit (v9b). Calendar-month reset, NO rollover.
    `category` is treated as unique-ish: POST upserts (updates the limit if a
    budget already exists for that category). `spent`/`remaining`/`pct`/`over` are
    computed on read from the current calendar month's net expense, never stored."""
    __tablename__ = "category_budgets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    limit_amount: Mapped[float] = mapped_column(Float, nullable=False)
    period: Mapped[str] = mapped_column(String(20), nullable=False, default="monthly")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())


class SavingsGoal(Base):
    """A savings goal (v9b): a target amount + optional target date, with progress
    tracked against a DESIGNATED connected account's balance growth. `account` is
    the app_account label of the designated PlaidItem account; `starting_balance`
    is captured at creation from that account's current Plaid balance so progress
    = current_balance − starting_balance. Progress fields are computed on read."""
    __tablename__ = "savings_goals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    target_amount: Mapped[float] = mapped_column(Float, nullable=False)
    target_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    account: Mapped[str | None] = mapped_column(String(100), nullable=True)
    starting_balance: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())


class ImportBatch(Base):
    """One CSV import (v5.2). Records the file + resulting counts; transactions
    link back via Transaction.batch_id so an import can be reassigned or undone."""
    __tablename__ = "import_batches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    filename: Mapped[str] = mapped_column(String(300), nullable=False)
    account: Mapped[str | None] = mapped_column(String(100), nullable=True)
    statement_type: Mapped[str | None] = mapped_column(String(20), nullable=True)  # card | bank (v5.3)
    imported: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    skipped: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    transfers: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    needs_review: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
