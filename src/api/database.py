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

if _IS_SQLITE:
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
else:
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
