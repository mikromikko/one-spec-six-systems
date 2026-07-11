"""End-to-end tests covering every requirement in the specification."""
import sqlite3
from io import BytesIO

import pytest

from app.db import get_db

PDF_BYTES = b"%PDF-1.4 fake pdf content for testing"


# --- helpers --------------------------------------------------------------

def login(client, email, password="password123"):
    return client.post("/api/auth/login", json={"email": email, "password": password})


def logout(client):
    return client.post("/api/auth/logout")


def create_invoice(client, amount="100.00", name="Acme Oy",
                   email="contact@acme.test", due="2026-12-31", pdf=PDF_BYTES):
    return client.post(
        "/api/invoices",
        data={
            "amount": amount,
            "counterparty_name": name,
            "counterparty_email": email,
            "due_date": due,
            "pdf": (BytesIO(pdf), "invoice.pdf"),
        },
        content_type="multipart/form-data",
    )


def read_email_log(app):
    with open(app.config["EMAIL_LOG"], encoding="utf-8") as f:
        return f.read()


# --- Requirement 1: auth & roles -----------------------------------------

def test_login_success_returns_role(client, users):
    resp = login(client, "sub1@nordvik.test")
    assert resp.status_code == 200
    assert resp.get_json()["role"] == "submitter"


def test_login_bad_password_rejected(client, users):
    resp = login(client, "sub1@nordvik.test", "wrong")
    assert resp.status_code == 401


def test_unauthenticated_cannot_list(client, users):
    assert client.get("/api/invoices").status_code == 401


# --- Requirement 2: submitter creates an invoice --------------------------

def test_create_invoice(client, users):
    login(client, "sub1@nordvik.test")
    resp = create_invoice(client)
    assert resp.status_code == 201
    body = resp.get_json()
    assert body["state"] == "submitted"
    assert body["amount_eur"] == "100.00"
    assert body["counterparty_name"] == "Acme Oy"


def test_create_invoice_requires_pdf(client, users):
    login(client, "sub1@nordvik.test")
    resp = client.post(
        "/api/invoices",
        data={"amount": "10", "counterparty_name": "X",
              "counterparty_email": "x@y.test", "due_date": "2026-01-01"},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400


def test_create_invoice_rejects_non_pdf(client, users):
    login(client, "sub1@nordvik.test")
    resp = create_invoice(client, pdf=b"not a pdf")
    assert resp.status_code == 400


def test_create_invoice_validates_amount_and_date(client, users):
    login(client, "sub1@nordvik.test")
    assert create_invoice(client, amount="-5").status_code == 400
    assert create_invoice(client, amount="abc").status_code == 400
    assert create_invoice(client, due="31-12-2026").status_code == 400
    assert create_invoice(client, email="notanemail").status_code == 400


# --- Requirement 3: state machine & only approvers act --------------------

def test_only_approver_can_approve(client, users):
    login(client, "sub1@nordvik.test")
    inv = create_invoice(client).get_json()
    # submitter (non-approver) cannot approve
    resp = client.post(f"/api/invoices/{inv['id']}/approve")
    assert resp.status_code == 403


def test_reject_sets_state(client, users):
    login(client, "sub1@nordvik.test")
    inv = create_invoice(client).get_json()
    logout(client)
    login(client, "app1@nordvik.test")
    resp = client.post(f"/api/invoices/{inv['id']}/reject", json={"reason": "duplicate"})
    assert resp.status_code == 200
    assert resp.get_json()["invoice"]["state"] == "rejected"


def test_cannot_act_on_already_decided(client, users):
    login(client, "sub1@nordvik.test")
    inv = create_invoice(client).get_json()
    logout(client)
    login(client, "app1@nordvik.test")
    client.post(f"/api/invoices/{inv['id']}/approve")
    resp = client.post(f"/api/invoices/{inv['id']}/reject")
    assert resp.status_code == 409


# --- Requirement 4: approver cannot decide own invoice --------------------

def test_approver_cannot_approve_own_invoice(client, users):
    # An approver may also submit invoices; they must not approve their own.
    login(client, "app1@nordvik.test")
    inv = create_invoice(client).get_json()
    resp = client.post(f"/api/invoices/{inv['id']}/approve")
    assert resp.status_code == 403
    resp = client.post(f"/api/invoices/{inv['id']}/reject")
    assert resp.status_code == 403


# --- Requirement 5: dual approval above EUR 10,000 ------------------------

def test_small_invoice_one_approval(client, users):
    login(client, "sub1@nordvik.test")
    inv = create_invoice(client, amount="10000.00").get_json()  # exactly at threshold
    assert inv["approvals_required"] == 1
    logout(client)
    login(client, "app1@nordvik.test")
    resp = client.post(f"/api/invoices/{inv['id']}/approve")
    assert resp.get_json()["invoice"]["state"] == "approved"


def test_large_invoice_needs_two_distinct_approvers(client, users):
    login(client, "sub1@nordvik.test")
    inv = create_invoice(client, amount="10000.01").get_json()
    assert inv["approvals_required"] == 2
    logout(client)

    # First approver -> still submitted
    login(client, "app1@nordvik.test")
    r1 = client.post(f"/api/invoices/{inv['id']}/approve")
    assert r1.get_json()["result"] == "pending"
    assert r1.get_json()["invoice"]["state"] == "submitted"
    # Same approver cannot approve again
    assert client.post(f"/api/invoices/{inv['id']}/approve").status_code == 409
    logout(client)

    # Second distinct approver -> approved
    login(client, "app2@nordvik.test")
    r2 = client.post(f"/api/invoices/{inv['id']}/approve")
    assert r2.get_json()["invoice"]["state"] == "approved"
    assert r2.get_json()["invoice"]["approvals_count"] == 2


# --- Requirement 6: append-only audit log ---------------------------------

def test_state_changes_are_audited(client, users, app):
    login(client, "sub1@nordvik.test")
    inv = create_invoice(client, amount="500").get_json()
    logout(client)
    login(client, "app1@nordvik.test")
    client.post(f"/api/invoices/{inv['id']}/approve")
    audit = client.get(f"/api/invoices/{inv['id']}/audit").get_json()
    actions = [a["action"] for a in audit]
    assert "create" in actions
    state_changes = [a for a in audit if a["action"] == "state_change"]
    approved = [a for a in state_changes if a["state_after"] == "approved"]
    assert approved
    entry = approved[0]
    assert entry["state_before"] == "submitted"
    assert entry["actor_id"] == users["app1"]["id"]
    assert entry["created_at"]


def test_audit_log_is_append_only(client, users, app):
    login(client, "sub1@nordvik.test")
    inv = create_invoice(client).get_json()
    with app.app_context():
        db = get_db()
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE audit_log SET detail = 'tampered' WHERE invoice_id = ?",
                       (inv["id"],))
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("DELETE FROM audit_log WHERE invoice_id = ?", (inv["id"],))


# --- Requirement 7: personal data (erase + audited reads) -----------------

def test_reading_counterparty_email_is_audited(client, users):
    login(client, "sub1@nordvik.test")
    inv = create_invoice(client, email="person@acme.test").get_json()
    logout(client)
    login(client, "app1@nordvik.test")
    resp = client.get(f"/api/invoices/{inv['id']}/counterparty-email")
    assert resp.get_json()["counterparty_email"] == "person@acme.test"
    audit = client.get(f"/api/invoices/{inv['id']}/audit").get_json()
    reads = [a for a in audit if a["action"] == "read_counterparty_email"]
    assert len(reads) == 1
    # A second read adds another audit entry.
    client.get(f"/api/invoices/{inv['id']}/counterparty-email")
    audit = client.get(f"/api/invoices/{inv['id']}/audit").get_json()
    reads = [a for a in audit if a["action"] == "read_counterparty_email"]
    assert len(reads) == 2


def test_email_not_exposed_in_invoice_body(client, users):
    login(client, "sub1@nordvik.test")
    inv = create_invoice(client, email="secret@acme.test").get_json()
    assert "secret@acme.test" not in str(inv)
    assert inv["counterparty_email_present"] is True


def test_erase_counterparty_email(client, users):
    login(client, "sub1@nordvik.test")
    inv = create_invoice(client, email="erase@acme.test").get_json()
    resp = client.delete(f"/api/invoices/{inv['id']}/counterparty-email")
    assert resp.status_code == 200
    detail = client.get(f"/api/invoices/{inv['id']}").get_json()
    assert detail["counterparty_email_erased"] is True
    # After erasure the email is gone.
    read = client.get(f"/api/invoices/{inv['id']}/counterparty-email").get_json()
    assert read["counterparty_email"] is None
    audit = client.get(f"/api/invoices/{inv['id']}/audit").get_json()
    assert any(a["action"] == "erase_counterparty_email" for a in audit)


# --- Requirement 8: filterable pending list -------------------------------

def test_pending_list_filters(client, users):
    login(client, "sub1@nordvik.test")
    a = create_invoice(client, amount="500", name="Alpha Supplies").get_json()
    b = create_invoice(client, amount="20000", name="Beta Metals").get_json()
    logout(client)

    login(client, "app1@nordvik.test")
    ids = {i["id"] for i in client.get("/api/pending").get_json()}
    assert ids == {a["id"], b["id"]}

    by_name = client.get("/api/pending?counterparty=alpha").get_json()
    assert [i["id"] for i in by_name] == [a["id"]]

    by_amount = client.get("/api/pending?min_amount=1000").get_json()
    assert [i["id"] for i in by_amount] == [b["id"]]

    by_range = client.get("/api/pending?min_amount=100&max_amount=1000").get_json()
    assert [i["id"] for i in by_range] == [a["id"]]


def test_pending_excludes_own_and_already_approved(client, users):
    # app1 submits one (should not see it), and approves a big one (drops off).
    login(client, "app1@nordvik.test")
    own = create_invoice(client, amount="500", name="Own Co").get_json()
    logout(client)
    login(client, "sub1@nordvik.test")
    big = create_invoice(client, amount="50000", name="Big Co").get_json()
    logout(client)

    login(client, "app1@nordvik.test")
    ids = {i["id"] for i in client.get("/api/pending").get_json()}
    assert own["id"] not in ids
    assert big["id"] in ids
    client.post(f"/api/invoices/{big['id']}/approve")  # partial approval
    ids = {i["id"] for i in client.get("/api/pending").get_json()}
    assert big["id"] not in ids  # already approved by this approver


# --- Requirement 9: email notification on state change --------------------

def test_notification_sent_on_state_change(client, users, app):
    login(client, "sub1@nordvik.test")
    inv = create_invoice(client, amount="500").get_json()
    logout(client)
    login(client, "app1@nordvik.test")
    client.post(f"/api/invoices/{inv['id']}/approve")

    log = read_email_log(app)
    assert "sub1@nordvik.test" in log
    assert "approved" in log


# --- Requirement: HTML interface smoke test -------------------------------

def test_html_login_and_dashboard(client, users):
    resp = client.post("/login", data={"email": "app1@nordvik.test",
                                        "password": "password123"},
                       follow_redirects=True)
    assert resp.status_code == 200
    assert b"Pending" in resp.data
