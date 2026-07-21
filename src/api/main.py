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
from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from .database import engine, SessionLocal, Base, get_db
from .models import Transaction
from .routes import transactions, stats, csv_import, assistant, rules, imports, duplicates, plaid_routes, budgets
from . import plaid_client

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
app.include_router(duplicates.router, prefix=API_PREFIX)
app.include_router(plaid_routes.router, prefix=API_PREFIX)   # /api/plaid/* (v8)
app.include_router(budgets.router, prefix=API_PREFIX)        # /api/budgets/* (v9b)


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


# ── Static frontend (v6 deploy: single-origin — FastAPI serves the built SPA) ──
# In production the Docker image builds the React app to ./dist and this process
# serves it alongside /api from ONE origin (no CORS needed in prod). Registered
# AFTER all /api routers so the SPA catch-all never shadows the API. Skipped
# entirely when dist/ is absent (local dev with the Vite server on :3000).

_STATIC_DIR = Path(
    os.environ.get(
        "STATIC_DIR",
        str(Path(__file__).resolve().parents[2] / "dist"),
    )
).resolve()

if (_STATIC_DIR / "index.html").is_file():
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    _INDEX_HTML = _STATIC_DIR / "index.html"

    # Mount hashed build assets (JS/CSS) at /assets.
    _assets_dir = _STATIC_DIR / "assets"
    if _assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=str(_assets_dir)), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa_fallback(full_path: str):
        """SPA fallback: serve index.html for any non-API path so client-side
        routes (/dashboard, /transactions, /rules) work on refresh. Real API,
        docs, and openapi paths are handled by their own routes registered above;
        guard here too in case dist ships a static file with a matching name."""
        # Never let the catch-all answer API/docs/schema paths.
        if (
            full_path == "api"
            or full_path.startswith("api/")
            or full_path in ("docs", "redoc", "openapi.json")
        ):
            raise HTTPException(status_code=404, detail="Not found")
        # Serve a real static file if one exists at that path (e.g. vite.svg,
        # favicon), otherwise fall back to index.html for client-side routing.
        candidate = (_STATIC_DIR / full_path).resolve()
        if full_path and candidate.is_file() and str(candidate).startswith(str(_STATIC_DIR)):
            return FileResponse(str(candidate))
        return FileResponse(str(_INDEX_HTML))


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


def _migrate_add_dup_dismissed_column() -> None:
    """v7 migration: add `dup_dismissed` to transactions (portable)."""
    _add_column_if_missing("transactions", "dup_dismissed", "BOOLEAN NOT NULL DEFAULT FALSE")


def _migrate_add_plaid_columns() -> None:
    """v8 migration: add Plaid provenance columns to transactions (portable).
    `plaid_items` is created automatically by create_all()."""
    _add_column_if_missing("transactions", "plaid_transaction_id", "VARCHAR")
    _add_column_if_missing("transactions", "plaid_account_id", "VARCHAR")
    _add_column_if_missing("transactions", "plaid_item_id", "INTEGER")


def _migrate_add_v9_enrichment_columns() -> None:
    """v9 migration: add Plaid transaction-enrichment columns to transactions and
    institution-branding columns to plaid_items (portable)."""
    _add_column_if_missing("transactions", "merchant_name", "VARCHAR")
    _add_column_if_missing("transactions", "logo_url", "VARCHAR")
    _add_column_if_missing("transactions", "pending", "BOOLEAN NOT NULL DEFAULT FALSE")
    _add_column_if_missing("transactions", "pending_transaction_id", "VARCHAR")
    _add_column_if_missing("transactions", "category_icon_url", "VARCHAR")
    _add_column_if_missing("plaid_items", "institution_logo", "TEXT")
    _add_column_if_missing("plaid_items", "institution_color", "VARCHAR")


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
    _migrate_add_dup_dismissed_column()   # v7: ensure transactions.dup_dismissed exists
    _migrate_add_plaid_columns()          # v8: ensure transactions.plaid_* columns exist
    _migrate_add_v9_enrichment_columns()  # v9: ensure enrichment + branding columns exist
    # Auto-seeding is OPT-IN. By default the DB starts empty so real user data
    # is never re-created on restart. Set SEED_DB=1 (for dev/demo) to seed the
    # sample transactions into an empty DB.
    if os.getenv("SEED_DB") in {"1", "true", "True", "yes"}:
        with SessionLocal() as db:
            seed_data(db)
    _maybe_start_plaid_scheduler()        # v8: optional in-process auto-sync


# ── Plaid auto-sync scheduler (v8) ─────────────────────────────────────────────
# In-process APScheduler job that periodically calls sync_items(). GATED: only
# starts when Plaid is configured AND PLAID_AUTOSYNC_INTERVAL_MINUTES is a
# positive int. Never runs in tests (env unset) and never breaks startup — any
# failure is swallowed. On Render's free tier (which sleeps) use a Cron Job
# hitting POST /api/plaid/sync-all instead (see docs/api-contract.md).

_plaid_scheduler = None


def _maybe_start_plaid_scheduler() -> None:
    global _plaid_scheduler
    try:
        if not plaid_client.is_configured():
            return
        raw = (os.getenv("PLAID_AUTOSYNC_INTERVAL_MINUTES") or "").strip()
        if not raw:
            return
        try:
            interval = int(raw)
        except ValueError:
            return
        if interval <= 0:
            return

        from apscheduler.schedulers.background import BackgroundScheduler
        from .routes.plaid_routes import sync_items

        def _job():
            try:
                with SessionLocal() as db:
                    sync_items(db)
            except Exception as e:  # never let a sync failure crash the scheduler
                print(f"[plaid] auto-sync failed: {e}")

        _plaid_scheduler = BackgroundScheduler(daemon=True)
        _plaid_scheduler.add_job(_job, "interval", minutes=interval, id="plaid_autosync")
        _plaid_scheduler.start()
        print(f"[plaid] auto-sync scheduler started ({interval} min interval).")
    except Exception as e:
        print(f"[plaid] scheduler not started: {e}")
