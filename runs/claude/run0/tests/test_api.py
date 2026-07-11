"""HTTP API tests using FastAPI's TestClient."""
import io
import json

import pytest
from fastapi.testclient import TestClient

from app.models import Role


@pytest.fixture()
def client(tmp_env):
    from app import db as db_module

    db_module.configure(__import__("os").environ["DATABASE_URL"])
    db_module.init_db()
    from app.main import app

    return TestClient(app)


@pytest.fixture()
def seed(client):
    """Create users directly and return a helper to log in via the API."""
    from app import db as db_module
    from app import services

    session = db_module.SessionLocal()
    users = {
        "sub": services.create_user(session, email="sub@x.io", password="pw123456", role=Role.submitter),
        "ap1": services.create_user(session, email="ap1@x.io", password="pw123456", role=Role.approver),
        "ap2": services.create_user(session, email="ap2@x.io", password="pw123456", role=Role.approver),
    }
    session.commit()
    session.close()

    def token(who):
        r = client.post("/auth/login", json={"email": users[who].email, "password": "pw123456"})
        assert r.status_code == 200, r.text
        return r.json()["token"]

    return token


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _create_invoice(client, token, amount_eur="500.00", name="Acme Oy", email="v@vendor.io"):
    return client.post(
        "/invoices",
        headers=_auth(token),
        data={
            "amount_eur": amount_eur,
            "counterparty_name": name,
            "counterparty_contact_email": email,
            "due_date": "2026-12-31",
        },
        files={"pdf": ("inv.pdf", io.BytesIO(b"%PDF-1.4 fake"), "application/pdf")},
    )


def test_login_bad_credentials(client, seed):
    r = client.post("/auth/login", json={"email": "sub@x.io", "password": "nope"})
    assert r.status_code == 401


def test_requires_auth(client):
    assert client.get("/invoices/pending").status_code == 401


def test_create_and_get_invoice(client, seed):
    t = seed("sub")
    r = _create_invoice(client, t)
    assert r.status_code == 201, r.text
    inv = r.json()
    assert inv["state"] == "submitted"
    assert inv["amount_cents"] == 50000
    # The plain invoice view must not leak the personal-data email unlogged.
    assert "counterparty_contact_email" not in inv

    got = client.get(f"/invoices/{inv['id']}", headers=_auth(t))
    assert got.status_code == 200


def test_rejects_non_pdf(client, seed):
    t = seed("sub")
    r = client.post(
        "/invoices",
        headers=_auth(t),
        data={
            "amount_eur": "10.00",
            "counterparty_name": "Acme",
            "counterparty_contact_email": "v@vendor.io",
            "due_date": "2026-12-31",
        },
        files={"pdf": ("inv.txt", io.BytesIO(b"not a pdf"), "text/plain")},
    )
    assert r.status_code == 400


def test_approve_small_invoice(client, seed):
    st, at = seed("sub"), seed("ap1")
    inv = _create_invoice(client, st, amount_eur="1000.00").json()
    r = client.post(f"/invoices/{inv['id']}/approve", headers=_auth(at))
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "approved"


def test_approver_cannot_approve_own(client, seed):
    at = seed("ap1")
    inv = _create_invoice(client, at).json()
    r = client.post(f"/invoices/{inv['id']}/approve", headers=_auth(at))
    assert r.status_code == 403


def test_submitter_cannot_approve(client, seed):
    st = seed("sub")
    inv = _create_invoice(client, st).json()
    r = client.post(f"/invoices/{inv['id']}/approve", headers=_auth(st))
    assert r.status_code == 403


def test_large_invoice_two_approvers(client, seed):
    st, a1, a2 = seed("sub"), seed("ap1"), seed("ap2")
    inv = _create_invoice(client, st, amount_eur="15000.00").json()
    r1 = client.post(f"/invoices/{inv['id']}/approve", headers=_auth(a1))
    assert r1.json()["state"] == "submitted"
    r2 = client.post(f"/invoices/{inv['id']}/approve", headers=_auth(a2))
    assert r2.json()["state"] == "approved"


def test_pending_list_and_filters(client, seed):
    st, a1 = seed("sub"), seed("ap1")
    _create_invoice(client, st, amount_eur="100.00", name="Acme Oy")
    _create_invoice(client, st, amount_eur="9000.00", name="Globex Ltd")

    r = client.get("/invoices/pending", headers=_auth(a1))
    assert r.status_code == 200
    assert len(r.json()) == 2

    r = client.get("/invoices/pending", headers=_auth(a1), params={"name": "acme"})
    assert [i["counterparty_name"] for i in r.json()] == ["Acme Oy"]

    r = client.get("/invoices/pending", headers=_auth(a1), params={"min_amount_eur": "5000"})
    assert [i["counterparty_name"] for i in r.json()] == ["Globex Ltd"]


def test_contact_email_read_is_logged(client, seed):
    st, a1 = seed("sub"), seed("ap1")
    inv = _create_invoice(client, st, email="privacy@vendor.io").json()
    r = client.get(f"/invoices/{inv['id']}/contact-email", headers=_auth(a1))
    assert r.status_code == 200
    assert r.json()["counterparty_contact_email"] == "privacy@vendor.io"

    audit = client.get(f"/invoices/{inv['id']}/audit", headers=_auth(a1)).json()
    assert any(e["action"] == "contact_email_read" for e in audit)


def test_erase_contact_email(client, seed):
    st, a1 = seed("sub"), seed("ap1")
    inv = _create_invoice(client, st, email="privacy@vendor.io").json()
    r = client.delete(f"/invoices/{inv['id']}/contact-email", headers=_auth(st))
    assert r.status_code == 200
    after = client.get(f"/invoices/{inv['id']}/contact-email", headers=_auth(a1)).json()
    assert after["counterparty_contact_email"] is None

    audit = client.get(f"/invoices/{inv['id']}/audit", headers=_auth(a1)).json()
    assert any(e["action"] == "contact_email_erased" for e in audit)


def test_audit_log_is_read_only_over_http(client, seed):
    st = seed("sub")
    inv = _create_invoice(client, st).json()
    # No mutation verbs are exposed on the audit resource.
    assert client.post(f"/invoices/{inv['id']}/audit", headers=_auth(st)).status_code in (404, 405)
    assert client.delete(f"/invoices/{inv['id']}/audit", headers=_auth(st)).status_code in (404, 405)
