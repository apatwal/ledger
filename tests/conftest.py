"""
conftest.py — shared fixtures for the expense-tracker test suite.

Strategy
--------
* Before importing the app, point DATABASE_URL at a throwaway temp-file SQLite
  DB so the app's own engine + its @app.on_event("startup") seeding never touch
  the real expense_tracker.db at project root.
* For each test, build a FRESH in-memory SQLite engine (StaticPool, so the
  in-memory DB is shared across every connection the sessionmaker opens) and
  override the app's get_db dependency to use it. This gives every test a clean,
  empty database that is fully isolated from disk and from other tests.

Why StaticPool?
    A plain `sqlite://` (in-memory) engine creates a NEW empty database for every
    connection. The sessionmaker opens its own connections, so tables created on
    one connection are invisible to the next -> "no such table". StaticPool keeps
    a single underlying connection so the schema + data persist for the test.
"""

from __future__ import annotations

import sys
import os
import io
import tempfile

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Ensure the project root is on sys.path so `src.api.main` can be imported
# regardless of how pytest is invoked.
# ---------------------------------------------------------------------------
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# ---------------------------------------------------------------------------
# Redirect the app's real engine to a throwaway temp file BEFORE importing the
# app, so its startup seeding does not write to the real expense_tracker.db.
# This temp DB is created once for the whole test session and is never queried
# by the tests (tests use the per-test in-memory engine instead).
# ---------------------------------------------------------------------------
_TMP_DB_FD, _TMP_DB_PATH = tempfile.mkstemp(suffix="_test_expense.db")
os.close(_TMP_DB_FD)
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP_DB_PATH}"


def _import_app():
    """Import and return the FastAPI app from src.api.main."""
    from src.api.main import app  # noqa: PLC0415
    return app


def _import_get_db():
    """Import and return the get_db dependency from src.api.database."""
    from src.api.database import get_db  # noqa: PLC0415
    return get_db


def _import_base():
    """Import and return the SQLAlchemy declarative Base.

    Importing src.api.main first guarantees every model module (and therefore
    every table) is registered on Base.metadata BEFORE create_all() runs.
    Without this, importing only src.api.database yields an empty metadata and
    create_all() silently creates no tables -> "no such table: transactions"
    when a test file is run in isolation. (When the full suite runs, an earlier
    file happens to import the app first, masking the issue.)
    """
    import src.api.main  # noqa: F401, PLC0415  (ensures models are registered)
    from src.api.database import Base  # noqa: PLC0415
    return Base


def pytest_unconfigure(config):
    """Remove the throwaway temp DB file at the end of the session."""
    try:
        os.unlink(_TMP_DB_PATH)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Core fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="function")
def test_engine():
    """
    Fresh in-memory SQLite engine with all tables created.

    StaticPool ensures every connection shares the same in-memory database so
    the schema (and inserted rows) survive across the multiple connections the
    sessionmaker opens during a single test.
    """
    Base = _import_base()
    engine = create_engine(
        "sqlite://",                       # in-memory
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    yield engine
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


@pytest.fixture(scope="function")
def test_session(test_engine):
    """A SQLAlchemy session bound to the in-memory engine."""
    TestingSession = sessionmaker(
        autocommit=False, autoflush=False, bind=test_engine
    )
    session = TestingSession()
    yield session
    session.close()


@pytest.fixture(scope="function")
def client(test_engine):
    """
    TestClient with the real FastAPI app wired to the per-test in-memory DB.

    The get_db dependency is overridden so every API request uses a session
    bound to the in-memory engine created for this test.
    """
    app = _import_app()
    get_db = _import_get_db()

    TestingSession = sessionmaker(
        autocommit=False, autoflush=False, bind=test_engine
    )

    def override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as c:
        yield c

    app.dependency_overrides.pop(get_db, None)


# ---------------------------------------------------------------------------
# Sample data helpers (used by multiple test files)
# ---------------------------------------------------------------------------

SAMPLE_TRANSACTIONS = [
    {"date": "2024-01-15", "amount": 5000.00, "type": "income",  "category": "Salary",     "description": "January salary"},
    {"date": "2024-01-20", "amount": 1200.00, "type": "expense", "category": "Rent",       "description": "Monthly rent"},
    {"date": "2024-01-22", "amount": 300.00,  "type": "expense", "category": "Groceries",  "description": "Weekly groceries"},
    {"date": "2024-01-25", "amount": 500.00,  "type": "expense", "category": "Savings",    "description": "Monthly savings"},
    {"date": "2024-01-28", "amount": 200.00,  "type": "expense", "category": "Investment", "description": "Index fund"},
    {"date": "2024-02-15", "amount": 5000.00, "type": "income",  "category": "Salary",     "description": "February salary"},
    {"date": "2024-02-20", "amount": 1200.00, "type": "expense", "category": "Rent",       "description": "Monthly rent"},
    {"date": "2024-02-22", "amount": 150.00,  "type": "expense", "category": "Dining",     "description": "Dinner out"},
]


@pytest.fixture(scope="function")
def seeded_client(client):
    """
    A TestClient with SAMPLE_TRANSACTIONS already inserted via the API.

    Returns (client, list_of_created_transaction_dicts) so tests can reference
    the IDs assigned by the server.
    """
    created = []
    for tx in SAMPLE_TRANSACTIONS:
        resp = client.post("/api/transactions", json=tx)
        assert resp.status_code == 201, f"Seed failed: {resp.text}"
        created.append(resp.json())
    return client, created


# ---------------------------------------------------------------------------
# CSV helpers
# ---------------------------------------------------------------------------

def make_csv_file(content: str, filename: str = "test.csv") -> dict:
    """Build the files dict expected by TestClient for a multipart CSV upload."""
    return {
        "file": (filename, io.BytesIO(content.encode("utf-8")), "text/csv")
    }


FIXTURES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def read_fixture_bytes(name: str) -> bytes:
    """Read a fixture file from tests/fixtures/ as raw bytes."""
    with open(os.path.join(FIXTURES_DIR, name), "rb") as fh:
        return fh.read()


def fixture_csv_file(name: str) -> dict:
    """Build the files dict for uploading a real fixture CSV by filename."""
    return {"file": (name, io.BytesIO(read_fixture_bytes(name)), "text/csv")}
