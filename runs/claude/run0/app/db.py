"""Database engine and session management."""
from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.models import Base

DEFAULT_DB_URL = "sqlite:///./invoices.db"


def _make_engine(db_url: str | None = None):
    url = db_url or os.environ.get("DATABASE_URL", DEFAULT_DB_URL)
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    return create_engine(url, connect_args=connect_args, future=True)


engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def init_db(target_engine=None) -> None:
    """Create all tables. Safe to call repeatedly."""
    Base.metadata.create_all(bind=target_engine or engine)


def configure(db_url: str):
    """Rebind the engine/session factory to a new URL (used by tests)."""
    global engine, SessionLocal
    engine = _make_engine(db_url)
    SessionLocal.configure(bind=engine)
    return engine


@contextmanager
def session_scope() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session() -> Iterator[Session]:
    """FastAPI dependency: yields a session and commits on success."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
