import os
import tempfile

import pytest


@pytest.fixture()
def tmp_env(monkeypatch, tmp_path):
    """Isolate DB, uploads and email outbox per test."""
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("EMAIL_OUTBOX", str(tmp_path / "emails" / "outbox.log"))
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    return tmp_path


@pytest.fixture()
def db(tmp_env):
    from app import db as db_module

    db_module.configure(os.environ["DATABASE_URL"])
    db_module.init_db()
    session = db_module.SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def make_user(db):
    from app import services

    def _make(email, role, password="pw-secret-123"):
        return services.create_user(db, email=email, password=password, role=role)

    return _make
