from datetime import date, datetime
from sqlalchemy import Integer, Float, String, Date, DateTime, Boolean, func
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
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="manual")  # manual | csv
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
