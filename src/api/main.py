"""
Expense Tracker — FastAPI backend
Run: uvicorn src.api.main:app --reload --port 8000
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment (e.g. GEMINI_API_KEY) from a .env file in the project root,
# before anything reads os.environ. Robust to the current working directory.
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

import sqlalchemy
from datetime import date
from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from .database import engine, SessionLocal, Base, get_db
from .models import Transaction
from .routes import transactions, stats, csv_import, assistant, rules, imports

# ── App ──────────────────────────────────────────────────────────────────────
app = FastAPI(title="Expense Tracker API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ──────────────────────────────────────────────────────────────────
API_PREFIX = "/api"

app.include_router(transactions.router, prefix=API_PREFIX)
app.include_router(csv_import.router, prefix=API_PREFIX)   # /api/transactions/csv*
app.include_router(stats.router, prefix=API_PREFIX)
app.include_router(assistant.router, prefix=API_PREFIX)
app.include_router(rules.router, prefix=API_PREFIX)
app.include_router(imports.router, prefix=API_PREFIX)


# ── Standalone endpoints ──────────────────────────────────────────────────────

@app.get(f"{API_PREFIX}/health")
def health():
    return {"status": "ok"}


DEFAULT_CATEGORIES = [
    "Salary", "Freelance", "Investment Returns",
    "Rent", "Groceries", "Dining", "Transport",
    "Utilities", "Healthcare", "Entertainment",
    "Clothing", "Savings", "Investment", "Other",
]


@app.get(f"{API_PREFIX}/categories", response_model=list[str])
def get_categories(db: Session = Depends(get_db)):
    """Return distinct categories from DB, merged with sensible defaults."""
    rows = db.execute(
        sqlalchemy.text("SELECT DISTINCT category FROM transactions ORDER BY category")
    ).scalars().all()
    # DB categories first (they exist), then defaults not already present
    seen = set(rows)
    merged = list(rows) + [c for c in DEFAULT_CATEGORIES if c not in seen]
    return merged


@app.get(f"{API_PREFIX}/accounts", response_model=list[str])
def get_accounts(db: Session = Depends(get_db)):
    """Return distinct non-empty accounts/cards seen in the DB (v4)."""
    rows = db.execute(
        sqlalchemy.text(
            "SELECT DISTINCT account FROM transactions "
            "WHERE account IS NOT NULL AND TRIM(account) != '' "
            "ORDER BY account"
        )
    ).scalars().all()
    return list(rows)


# ── DB init + seed ───────────────────────────────────────────────────────────

def _existing_columns(table: str) -> set[str]:
    """v6: portable column introspection via SQLAlchemy inspector — works on BOTH
    SQLite and Postgres (no SQLite-only PRAGMA). Returns an empty set if the table
    doesn't exist yet (fresh DB — create_all builds the full schema)."""
    inspector = sqlalchemy.inspect(engine)
    if table not in inspector.get_table_names():
        return set()
    return {col["name"] for col in inspector.get_columns(table)}


def _add_column_if_missing(table: str, column: str, ddl_type: str) -> None:
    """v6: portable ALTER TABLE ADD COLUMN, only when the column is missing and
    the table already exists. Uses portable DDL types (VARCHAR/BOOLEAN/INTEGER)
    valid on both SQLite and Postgres. On a fresh DB the table doesn't exist yet
    (create_all makes the full schema), so this is a no-op — never crashes."""
    existing = _existing_columns(table)
    if not existing:
        return  # fresh table — create_all already added every column
    if column in existing:
        return  # already present
    with engine.begin() as conn:
        conn.execute(sqlalchemy.text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}"))
    print(f"[migrate] Added '{column}' column to {table}.")


def _migrate_add_account_column() -> None:
    """v4 migration: add `account` to an existing transactions table (portable)."""
    _add_column_if_missing("transactions", "account", "VARCHAR")


def _migrate_add_needs_review_column() -> None:
    """v5 migration: add `needs_review` + `review_reason` (portable)."""
    _add_column_if_missing("transactions", "needs_review", "BOOLEAN NOT NULL DEFAULT FALSE")
    _add_column_if_missing("transactions", "review_reason", "VARCHAR")


def _migrate_add_batch_id_column() -> None:
    """v5.2 migration: add `batch_id` to transactions (portable)."""
    _add_column_if_missing("transactions", "batch_id", "INTEGER")


def _migrate_add_statement_type_column() -> None:
    """v5.3 migration: add `statement_type` to import_batches (portable)."""
    _add_column_if_missing("import_batches", "statement_type", "VARCHAR")


def seed_data(db: Session) -> None:
    """Insert ~19 realistic transactions if the table is empty."""
    count = db.execute(sqlalchemy.text("SELECT COUNT(*) FROM transactions")).scalar()
    if count and count > 0:
        return

    samples = [
        # Jan income
        Transaction(date=date(2026, 1, 2),  amount=5500.00, type="income",  category="Salary",       description="January salary",          source="manual"),
        # Jan expenses
        Transaction(date=date(2026, 1, 5),  amount=1400.00, type="expense", category="Rent",         description="Apartment rent",           source="manual"),
        Transaction(date=date(2026, 1, 10), amount=220.00,  type="expense", category="Groceries",    description="Supermarket run",          source="manual"),
        Transaction(date=date(2026, 1, 15), amount=500.00,  type="expense", category="Savings",      description="Monthly savings transfer", source="manual"),
        Transaction(date=date(2026, 1, 20), amount=85.00,   type="expense", category="Dining",       description="Dinner with friends",      source="manual"),
        Transaction(date=date(2026, 1, 25), amount=60.00,   type="expense", category="Transport",    description="Monthly transit pass",     source="manual"),
        # Feb income
        Transaction(date=date(2026, 2, 3),  amount=5500.00, type="income",  category="Salary",       description="February salary",          source="manual"),
        # Feb expenses
        Transaction(date=date(2026, 2, 5),  amount=1400.00, type="expense", category="Rent",         description="Apartment rent",           source="manual"),
        Transaction(date=date(2026, 2, 12), amount=195.00,  type="expense", category="Groceries",    description="Groceries",                source="manual"),
        Transaction(date=date(2026, 2, 14), amount=110.00,  type="expense", category="Dining",       description="Valentine's dinner",       source="manual"),
        Transaction(date=date(2026, 2, 18), amount=300.00,  type="expense", category="Investment",   description="ETF purchase",             source="manual"),
        Transaction(date=date(2026, 2, 20), amount=750.00,  type="income",  category="Freelance",    description="Web design project",       source="manual"),
        # Mar income
        Transaction(date=date(2026, 3, 3),  amount=5500.00, type="income",  category="Salary",       description="March salary",             source="manual"),
        # Mar expenses
        Transaction(date=date(2026, 3, 5),  amount=1400.00, type="expense", category="Rent",         description="Apartment rent",           source="manual"),
        Transaction(date=date(2026, 3, 8),  amount=240.00,  type="expense", category="Groceries",    description="Weekly groceries",         source="manual"),
        Transaction(date=date(2026, 3, 15), amount=500.00,  type="expense", category="Savings",      description="Monthly savings",          source="manual"),
        Transaction(date=date(2026, 3, 20), amount=45.00,   type="expense", category="Entertainment",description="Streaming subscriptions",  source="manual"),
        Transaction(date=date(2026, 3, 22), amount=90.00,   type="expense", category="Transport",    description="Fuel",                     source="manual"),
        Transaction(date=date(2026, 3, 28), amount=320.00,  type="expense", category="Healthcare",   description="Dentist appointment",      source="manual"),
    ]
    db.add_all(samples)
    db.commit()
    print(f"[seed] Inserted {len(samples)} sample transactions.")


@app.on_event("startup")
def startup():
    Base.metadata.create_all(bind=engine)
    _migrate_add_account_column()       # v4: ensure `account` column exists on legacy DBs
    _migrate_add_needs_review_column()  # v5: ensure needs_review/review_reason exist
    _migrate_add_batch_id_column()      # v5.2: ensure batch_id exists
    _migrate_add_statement_type_column()  # v5.3: ensure import_batches.statement_type exists
    # Auto-seeding is OPT-IN. By default the DB starts empty so real user data
    # is never re-created on restart. Set SEED_DB=1 (for dev/demo) to seed the
    # sample transactions into an empty DB.
    if os.getenv("SEED_DB") in {"1", "true", "True", "yes"}:
        with SessionLocal() as db:
            seed_data(db)
