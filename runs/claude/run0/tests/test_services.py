"""Business-logic tests for the invoice approval domain."""
import json
from datetime import date

import pytest

from app import services
from app.models import Decision, InvoiceState, Role
from app.security import verify_password


def _invoice(db, submitter, amount_cents=50_00, email="ap@vendor.example"):
    return services.create_invoice(
        db,
        submitter=submitter,
        amount_cents=amount_cents,
        counterparty_name="Acme Oy",
        counterparty_contact_email=email,
        due_date=date(2026, 12, 31),
        pdf_path="/tmp/x.pdf",
    )


# --- users & auth ---------------------------------------------------------

def test_create_user_hashes_password(db):
    user = services.create_user(db, email="a@x.io", password="s3cret!!", role=Role.submitter)
    assert user.password_hash != "s3cret!!"
    assert verify_password("s3cret!!", user.password_hash)


def test_authenticate_returns_token_for_valid_credentials(db):
    services.create_user(db, email="a@x.io", password="s3cret!!", role=Role.submitter)
    token = services.authenticate(db, email="a@x.io", password="s3cret!!")
    assert token
    assert services.user_for_token(db, token).email == "a@x.io"


def test_authenticate_rejects_bad_password(db):
    services.create_user(db, email="a@x.io", password="s3cret!!", role=Role.submitter)
    assert services.authenticate(db, email="a@x.io", password="wrong") is None


# --- invoice creation -----------------------------------------------------

def test_create_invoice_starts_submitted_and_audits(db, make_user):
    sub = make_user("sub@x.io", Role.submitter)
    inv = _invoice(db, sub)
    assert inv.state == InvoiceState.submitted
    log = services.get_audit_log(db, invoice_id=inv.id)
    assert any(e.action == "submitted" and e.state_after == "submitted" for e in log)


def test_create_invoice_notifies_submitter(db, make_user, tmp_env):
    sub = make_user("sub@x.io", Role.submitter)
    _invoice(db, sub)
    outbox = tmp_env / "emails" / "outbox.log"
    lines = outbox.read_text().splitlines()
    assert any(json.loads(l)["to"] == "sub@x.io" for l in lines)


# --- approval rules -------------------------------------------------------

def test_approver_cannot_decide_own_invoice(db, make_user):
    ap = make_user("ap@x.io", Role.approver)
    inv = _invoice(db, ap)
    with pytest.raises(services.PermissionDenied):
        services.decide(db, invoice_id=inv.id, actor=ap, decision=Decision.approve)


def test_submitter_role_cannot_approve(db, make_user):
    sub = make_user("sub@x.io", Role.submitter)
    other = make_user("sub2@x.io", Role.submitter)
    inv = _invoice(db, sub)
    with pytest.raises(services.PermissionDenied):
        services.decide(db, invoice_id=inv.id, actor=other, decision=Decision.approve)


def test_single_approval_approves_small_invoice(db, make_user):
    sub = make_user("sub@x.io", Role.submitter)
    ap = make_user("ap@x.io", Role.approver)
    inv = _invoice(db, sub, amount_cents=10_000_00)  # exactly 10,000 EUR
    inv = services.decide(db, invoice_id=inv.id, actor=ap, decision=Decision.approve)
    assert inv.state == InvoiceState.approved


def test_large_invoice_needs_two_distinct_approvers(db, make_user):
    sub = make_user("sub@x.io", Role.submitter)
    ap1 = make_user("ap1@x.io", Role.approver)
    ap2 = make_user("ap2@x.io", Role.approver)
    inv = _invoice(db, sub, amount_cents=10_000_01)  # over 10,000 EUR
    inv = services.decide(db, invoice_id=inv.id, actor=ap1, decision=Decision.approve)
    assert inv.state == InvoiceState.submitted  # still pending second approval
    inv = services.decide(db, invoice_id=inv.id, actor=ap2, decision=Decision.approve)
    assert inv.state == InvoiceState.approved


def test_same_approver_cannot_approve_twice(db, make_user):
    sub = make_user("sub@x.io", Role.submitter)
    ap1 = make_user("ap1@x.io", Role.approver)
    inv = _invoice(db, sub, amount_cents=20_000_00)
    services.decide(db, invoice_id=inv.id, actor=ap1, decision=Decision.approve)
    with pytest.raises(services.InvalidOperation):
        services.decide(db, invoice_id=inv.id, actor=ap1, decision=Decision.approve)


def test_single_rejection_rejects(db, make_user):
    sub = make_user("sub@x.io", Role.submitter)
    ap1 = make_user("ap1@x.io", Role.approver)
    inv = _invoice(db, sub, amount_cents=20_000_00)
    inv = services.decide(db, invoice_id=inv.id, actor=ap1, decision=Decision.reject)
    assert inv.state == InvoiceState.rejected


def test_cannot_decide_already_finalized_invoice(db, make_user):
    sub = make_user("sub@x.io", Role.submitter)
    ap1 = make_user("ap1@x.io", Role.approver)
    ap2 = make_user("ap2@x.io", Role.approver)
    inv = _invoice(db, sub, amount_cents=100_00)
    services.decide(db, invoice_id=inv.id, actor=ap1, decision=Decision.approve)
    with pytest.raises(services.InvalidOperation):
        services.decide(db, invoice_id=inv.id, actor=ap2, decision=Decision.approve)


def test_state_change_is_audited_with_actor_and_transition(db, make_user):
    sub = make_user("sub@x.io", Role.submitter)
    ap1 = make_user("ap1@x.io", Role.approver)
    inv = _invoice(db, sub, amount_cents=100_00)
    services.decide(db, invoice_id=inv.id, actor=ap1, decision=Decision.approve)
    log = services.get_audit_log(db, invoice_id=inv.id)
    approved = [e for e in log if e.action == "approved"]
    assert approved
    assert approved[0].actor_id == ap1.id
    assert approved[0].state_before == "submitted"
    assert approved[0].state_after == "approved"


def test_state_change_notifies_submitter(db, make_user, tmp_env):
    sub = make_user("sub@x.io", Role.submitter)
    ap1 = make_user("ap1@x.io", Role.approver)
    inv = _invoice(db, sub, amount_cents=100_00)
    services.decide(db, invoice_id=inv.id, actor=ap1, decision=Decision.approve)
    lines = (tmp_env / "emails" / "outbox.log").read_text().splitlines()
    subjects = [json.loads(l)["subject"] for l in lines if json.loads(l)["to"] == "sub@x.io"]
    assert any("approved" in s.lower() for s in subjects)


# --- pending list & filters ----------------------------------------------

def test_pending_excludes_own_and_already_decided(db, make_user):
    sub = make_user("sub@x.io", Role.submitter)
    ap1 = make_user("ap1@x.io", Role.approver)
    ap2 = make_user("ap2@x.io", Role.approver)
    own = _invoice(db, ap1, amount_cents=100_00)  # submitted by ap1
    other = _invoice(db, sub, amount_cents=50_00)
    big = _invoice(db, sub, amount_cents=20_000_00)
    services.decide(db, invoice_id=big.id, actor=ap1, decision=Decision.approve)

    pending = services.pending_for_approver(db, ap1)
    ids = {i.id for i in pending}
    assert other.id in ids
    assert own.id not in ids  # cannot approve own
    assert big.id not in ids  # already approved by ap1


def test_pending_filter_by_name_and_amount(db, make_user):
    sub = make_user("sub@x.io", Role.submitter)
    ap1 = make_user("ap1@x.io", Role.approver)
    a = services.create_invoice(
        db, submitter=sub, amount_cents=100_00, counterparty_name="Acme Oy",
        counterparty_contact_email="a@v.io", due_date=date(2026, 1, 1), pdf_path="/tmp/a.pdf")
    b = services.create_invoice(
        db, submitter=sub, amount_cents=900_00, counterparty_name="Globex Ltd",
        counterparty_contact_email="b@v.io", due_date=date(2026, 1, 1), pdf_path="/tmp/b.pdf")

    by_name = services.pending_for_approver(db, ap1, name="acme")
    assert {i.id for i in by_name} == {a.id}

    by_amount = services.pending_for_approver(db, ap1, min_amount_cents=500_00)
    assert {i.id for i in by_amount} == {b.id}


# --- personal data: erasure & read logging --------------------------------

def test_read_contact_email_is_audited(db, make_user):
    sub = make_user("sub@x.io", Role.submitter)
    ap1 = make_user("ap1@x.io", Role.approver)
    inv = _invoice(db, sub, email="privacy@vendor.io")
    value = services.read_contact_email(db, invoice_id=inv.id, actor=ap1)
    assert value == "privacy@vendor.io"
    log = services.get_audit_log(db, invoice_id=inv.id)
    reads = [e for e in log if e.action == "contact_email_read"]
    assert reads and reads[-1].actor_id == ap1.id


def test_erase_contact_email(db, make_user):
    sub = make_user("sub@x.io", Role.submitter)
    inv = _invoice(db, sub, email="privacy@vendor.io")
    inv = services.erase_contact_email(db, invoice_id=inv.id, actor=sub)
    assert inv.counterparty_contact_email is None
    assert inv.contact_email_erased is True
    log = services.get_audit_log(db, invoice_id=inv.id)
    assert any(e.action == "contact_email_erased" for e in log)


def test_audit_log_has_no_mutation_api(db):
    # The audit module must not expose update/delete helpers.
    assert not hasattr(services, "update_audit_entry")
    assert not hasattr(services, "delete_audit_entry")
