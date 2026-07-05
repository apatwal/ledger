import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "sqlite:////Users/adityapatwal/Documents/projects/expense tracker/expense_tracker.db",
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
