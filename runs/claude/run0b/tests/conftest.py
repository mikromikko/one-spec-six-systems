import os
import tempfile

import pytest
from werkzeug.security import generate_password_hash

from app import create_app
from app.auth import now_iso
from app.db import get_db


@pytest.fixture
def app():
    db_fd, db_path = tempfile.mkstemp(suffix=".db")
    upload_dir = tempfile.mkdtemp()
    email_fd, email_log = tempfile.mkstemp(suffix=".log")
    os.close(email_fd)

    application = create_app({
        "TESTING": True,
        "DATABASE": db_path,
        "UPLOAD_DIR": upload_dir,
        "EMAIL_LOG": email_log,
        "SECRET_KEY": "test",
    })

    yield application

    os.close(db_fd)
    os.unlink(db_path)


@pytest.fixture
def client(app):
    return app.test_client()


def _make_user(app, email, role, password="password123"):
    with app.app_context():
        db = get_db()
        db.execute(
            "INSERT INTO users (email, password_hash, role, created_at) VALUES (?, ?, ?, ?)",
            (email, generate_password_hash(password), role, now_iso()),
        )
        db.commit()
        return db.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()["id"]


@pytest.fixture
def users(app):
    return {
        "sub1": {"email": "sub1@nordvik.test", "role": "submitter",
                 "id": _make_user(app, "sub1@nordvik.test", "submitter")},
        "sub2": {"email": "sub2@nordvik.test", "role": "submitter",
                 "id": _make_user(app, "sub2@nordvik.test", "submitter")},
        "app1": {"email": "app1@nordvik.test", "role": "approver",
                 "id": _make_user(app, "app1@nordvik.test", "approver")},
        "app2": {"email": "app2@nordvik.test", "role": "approver",
                 "id": _make_user(app, "app2@nordvik.test", "approver")},
    }
