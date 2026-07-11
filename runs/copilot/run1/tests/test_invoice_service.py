import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.main import app, database_path, init_db


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("INVOICE_DB_PATH", str(tmp_path / "invoices.sqlite3"))
    monkeypatch.setenv("INVOICE_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("NOTIFICATION_LOG_PATH", str(tmp_path / "notifications.log"))
    with TestClient(app) as test_client:
        yield test_client


def create_user(client, email, role):
    response = client.post(
        "/users",
        json={"email": email, "password": "correct horse battery staple", "role": role},
    )
    assert response.status_code == 201, response.text
    return response.json()


def token_for(client, email):
    response = client.post(
        "/auth/login",
        json={"email": email, "password": "correct horse battery staple"},
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def create_invoice(client, token, amount="1000.00", name="Parts GmbH", contact="ap@parts.example"):
    response = client.post(
        "/invoices",
        headers=auth(token),
        data={
            "amount_euros": amount,
            "counterparty_name": name,
            "counterparty_contact_email": contact,
            "due_date": "2027-01-31",
        },
        files={"pdf": ("invoice.pdf", b"%PDF-1.4\ncontent\n%%EOF", "application/pdf")},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_auth_roles_and_submitter_invoice_creation(client):
    create_user(client, "submitter@nordvik.example", "submitter")
    submitter_token = token_for(client, "submitter@nordvik.example")
    invoice = create_invoice(client, submitter_token)

    assert invoice["state"] == "submitted"
    assert invoice["required_approvals"] == 1
    assert invoice["amount_euros"] == "1000.00"

    rejected = client.post("/invoices", json={}, headers=auth(submitter_token))
    assert rejected.status_code == 422


def test_only_approvers_can_decide_and_submitter_cannot_approve_own_invoice(client):
    create_user(client, "submitter@nordvik.example", "submitter")
    create_user(client, "approver@nordvik.example", "approver")
    submitter_token = token_for(client, "submitter@nordvik.example")
    approver_token = token_for(client, "approver@nordvik.example")
    invoice = create_invoice(client, submitter_token)

    submitter_decision = client.post(f"/invoices/{invoice['id']}/approve", headers=auth(submitter_token))
    assert submitter_decision.status_code == 403

    approved = client.post(f"/invoices/{invoice['id']}/approve", headers=auth(approver_token))
    assert approved.status_code == 200, approved.text
    assert approved.json()["state"] == "approved"


def test_large_invoice_requires_two_distinct_approvers(client):
    create_user(client, "submitter@nordvik.example", "submitter")
    create_user(client, "approver1@nordvik.example", "approver")
    create_user(client, "approver2@nordvik.example", "approver")
    submitter_token = token_for(client, "submitter@nordvik.example")
    approver1_token = token_for(client, "approver1@nordvik.example")
    approver2_token = token_for(client, "approver2@nordvik.example")
    invoice = create_invoice(client, submitter_token, amount="10000.01")

    first = client.post(f"/invoices/{invoice['id']}/approve", headers=auth(approver1_token))
    assert first.status_code == 200, first.text
    assert first.json()["state"] == "submitted"
    assert first.json()["approvals_count"] == 1

    duplicate = client.post(f"/invoices/{invoice['id']}/approve", headers=auth(approver1_token))
    assert duplicate.status_code == 409

    second = client.post(f"/invoices/{invoice['id']}/approve", headers=auth(approver2_token))
    assert second.status_code == 200, second.text
    assert second.json()["state"] == "approved"
    assert second.json()["approvals_count"] == 2


def test_rejection_audit_log_and_notification(client, tmp_path):
    create_user(client, "submitter@nordvik.example", "submitter")
    create_user(client, "approver@nordvik.example", "approver")
    submitter_token = token_for(client, "submitter@nordvik.example")
    approver_token = token_for(client, "approver@nordvik.example")
    invoice = create_invoice(client, submitter_token)

    rejected = client.post(
        f"/invoices/{invoice['id']}/reject",
        headers=auth(approver_token),
        json={"reason": "missing purchase order"},
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["state"] == "rejected"

    logs = client.get(f"/audit-logs?invoice_id={invoice['id']}", headers=auth(approver_token)).json()
    state_changes = [log for log in logs if log["action"] == "state_changed"]
    assert [(log["state_before"], log["state_after"]) for log in state_changes] == [
        (None, "submitted"),
        ("submitted", "rejected"),
    ]

    notifications = [
        json.loads(line)
        for line in (tmp_path / "notifications.log").read_text(encoding="utf-8").splitlines()
    ]
    assert notifications[-1]["to"] == "submitter@nordvik.example"
    assert "rejected" in notifications[-1]["subject"]


def test_contact_email_read_is_audited_and_can_be_erased(client):
    create_user(client, "submitter@nordvik.example", "submitter")
    create_user(client, "approver@nordvik.example", "approver")
    submitter_token = token_for(client, "submitter@nordvik.example")
    approver_token = token_for(client, "approver@nordvik.example")
    invoice = create_invoice(client, submitter_token, contact="personal@supplier.example")

    contact = client.get(
        f"/invoices/{invoice['id']}/counterparty-contact-email",
        headers=auth(approver_token),
    )
    assert contact.status_code == 200, contact.text
    assert contact.json()["counterparty_contact_email"] == "personal@supplier.example"

    erased = client.post(
        f"/invoices/{invoice['id']}/erase-counterparty-contact-email",
        headers=auth(submitter_token),
    )
    assert erased.status_code == 200, erased.text
    contact_after = client.get(
        f"/invoices/{invoice['id']}/counterparty-contact-email",
        headers=auth(approver_token),
    )
    assert contact_after.json()["counterparty_contact_email"] is None

    logs = client.get(f"/audit-logs?invoice_id={invoice['id']}", headers=auth(approver_token)).json()
    actions = [log["action"] for log in logs]
    assert actions.count("read_counterparty_contact_email") == 2
    assert "erase_counterparty_contact_email" in actions


def test_pending_invoices_are_filterable_and_exclude_own_or_already_approved(client):
    create_user(client, "submitter@nordvik.example", "submitter")
    create_user(client, "approver1@nordvik.example", "approver")
    create_user(client, "approver2@nordvik.example", "approver")
    submitter_token = token_for(client, "submitter@nordvik.example")
    approver1_token = token_for(client, "approver1@nordvik.example")
    approver2_token = token_for(client, "approver2@nordvik.example")
    small = create_invoice(client, submitter_token, amount="500.00", name="Tools Ltd")
    large = create_invoice(client, submitter_token, amount="20000.00", name="Parts GmbH")

    filtered = client.get(
        "/invoices/pending?counterparty_name=parts&min_amount_euros=10000",
        headers=auth(approver1_token),
    )
    assert filtered.status_code == 200, filtered.text
    assert [invoice["id"] for invoice in filtered.json()] == [large["id"]]

    client.post(f"/invoices/{large['id']}/approve", headers=auth(approver1_token))
    approver1_pending = client.get("/invoices/pending", headers=auth(approver1_token)).json()
    approver2_pending = client.get("/invoices/pending", headers=auth(approver2_token)).json()
    assert large["id"] not in [invoice["id"] for invoice in approver1_pending]
    assert large["id"] in [invoice["id"] for invoice in approver2_pending]
    assert small["id"] in [invoice["id"] for invoice in approver1_pending]


def test_audit_logs_are_append_only_at_database_level(client):
    create_user(client, "submitter@nordvik.example", "submitter")
    submitter_token = token_for(client, "submitter@nordvik.example")
    create_invoice(client, submitter_token)
    init_db()

    conn = sqlite3.connect(database_path())
    with pytest.raises(sqlite3.DatabaseError):
        conn.execute("UPDATE audit_logs SET action = 'tampered' WHERE id = 1")
    with pytest.raises(sqlite3.DatabaseError):
        conn.execute("DELETE FROM audit_logs WHERE id = 1")
    conn.close()
