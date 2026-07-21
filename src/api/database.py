import os
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase

# Default SQLite path resolves to <project root>/expense_tracker.db — the same
# location locally AND inside the Docker image (/app/expense_tracker.db), so an
# unset DATABASE_URL works in both. Set DATABASE_URL (e.g. postgresql://…) to
# override. Computed relative to this file (src/api/database.py -> parents[2]).
_DEFAULT_SQLITE_PATH = Path(__file__).resolve().parents[2] / "expense_tracker.db"

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    f"sqlite:///{_DEFAULT_SQLITE_PATH}",
)

# v6: dialect selected by DATABASE_URL. SQLite (unset default or sqlite://…) needs
# check_same_thread=False for the threaded dev server; Postgres (Neon) needs no
# such arg but wants pool_pre_ping=True to survive serverless idle-connection drops.
_IS_SQLITE = DATABASE_URL.startswith("sqlite")


def _int_env(name: str, default: int) -> int:
    """Read a positive-ish int from env, falling back to `default` on unset/blank
    or non-numeric values. Keeps the Postgres pool knobs overridable without
    letting a typo crash engine creation at import time."""
    try:
        return int((os.environ.get(name) or "").strip() or default)
    except ValueError:
        return default


if _IS_SQLITE:
    # SQLite path unchanged: check_same_thread=False for the threaded dev server.
    # (create_engine keeps its default StaticPool/SingletonThreadPool behavior.)
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
else:
    # Postgres (Neon) pool tuning (feature/performance): pool_pre_ping re-validates
    # a checked-out connection with a cheap SELECT 1, so Neon's serverless layer
    # dropping an idle connection surfaces as a silent reconnect, not a stall/error.
    # pool_recycle proactively discards connections older than N seconds so we never
    # hand out one the Neon proxy has already idle-closed. Overridable via env.
    engine = create_engine(
        DATABASE_URL,
        pool_pre_ping=True,
        pool_size=_int_env("DB_POOL_SIZE", 5),
        max_overflow=_int_env("DB_MAX_OVERFLOW", 5),
        pool_recycle=_int_env("DB_POOL_RECYCLE", 300),
    )

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
