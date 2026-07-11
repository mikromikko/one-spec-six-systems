"""Domain services: all business rules live here.

Kept independent of FastAPI so the rules can be unit-tested directly and reused.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import email_stub
from app.models import (
    Approval,
    AuditLog,
    Decision,
    Invoice,
    InvoiceState,
    Role,
    Session as AuthSession,
    User,
)
from app.security import hash_password, new_token, verify_password

# Amounts strictly greater than this need two distinct approvers (10,000 EUR).
TWO_APPROVER_THRESHOLD_CENTS = 10_000_00


class ServiceError(Exception):
    """Base class for domain errors."""


class PermissionDenied(ServiceError):
    pass


class InvalidOperation(ServiceError):
    pass


class NotFound(ServiceError):
    pass


# --- audit helpers --------------------------------------------------------

def _audit(
    session: Session,
    *,
    invoice_id: int | None,
    actor_id: int | None,
    action: str,
    state_before: str | None = None,
    state_after: str | None = None,
    detail: str | None = None,
) -> AuditLog:
    """Append one audit entry. This is the ONLY write path for the audit log;
    no update or delete helper exists anywhere in the codebase."""
    entry = AuditLog(
        invoice_id=invoice_id,
        actor_id=actor_id,
        action=action,
        state_before=state_before,
        state_after=state_after,
        detail=detail,
    )
    session.add(entry)
    session.flush()
    return entry


def get_audit_log(session: Session, invoice_id: int | None = None) -> list[AuditLog]:
    stmt = select(AuditLog).order_by(AuditLog.id)
    if invoice_id is not None:
        stmt = stmt.where(AuditLog.invoice_id == invoice_id)
    return list(session.scalars(stmt))


# --- users & auth ---------------------------------------------------------

def create_user(session: Session, *, email: str, password: str, role: Role) -> User:
    if isinstance(role, str):
        role = Role(role)
    user = User(email=email, password_hash=hash_password(password), role=role)
    session.add(user)
    session.flush()
    return user


def get_user_by_email(session: Session, email: str) -> User | None:
    return session.scalar(select(User).where(User.email == email))


def authenticate(session: Session, *, email: str, password: str) -> str | None:
    user = get_user_by_email(session, email)
    if user is None or not verify_password(password, user.password_hash):
        return None
    token = new_token()
    session.add(AuthSession(token=token, user_id=user.id))
    session.flush()
    return token


def user_for_token(session: Session, token: str) -> User | None:
    auth = session.get(AuthSession, token)
    if auth is None:
        return None
    return session.get(User, auth.user_id)


# --- invoices -------------------------------------------------------------

def create_invoice(
    session: Session,
    *,
    submitter: User,
    amount_cents: int,
    counterparty_name: str,
    counterparty_contact_email: str,
    due_date: date,
    pdf_path: str,
) -> Invoice:
    # Any authenticated user may submit an invoice. Roles gate *approval*
    # authority; requirement 4 (an approver may not decide an invoice they
    # submitted) is only meaningful if approvers can also submit.
    if amount_cents <= 0:
        raise InvalidOperation("amount must be positive")
    invoice = Invoice(
        amount_cents=amount_cents,
        counterparty_name=counterparty_name,
        counterparty_contact_email=counterparty_contact_email,
        due_date=due_date,
        pdf_path=pdf_path,
        state=InvoiceState.submitted,
        submitter_id=submitter.id,
    )
    session.add(invoice)
    session.flush()
    _audit(
        session,
        invoice_id=invoice.id,
        actor_id=submitter.id,
        action="submitted",
        state_before=None,
        state_after=InvoiceState.submitted.value,
    )
    _notify(session, invoice, "submitted", InvoiceState.submitted)
    session.commit()
    return invoice


def get_invoice(session: Session, invoice_id: int) -> Invoice:
    invoice = session.get(Invoice, invoice_id)
    if invoice is None:
        raise NotFound("invoice not found")
    return invoice


def _required_approvals(invoice: Invoice) -> int:
    return 2 if invoice.amount_cents > TWO_APPROVER_THRESHOLD_CENTS else 1


def decide(
    session: Session, *, invoice_id: int, actor: User, decision: Decision
) -> Invoice:
    if isinstance(decision, str):
        decision = Decision(decision)
    invoice = get_invoice(session, invoice_id)

    if actor.role != Role.approver:
        raise PermissionDenied("only approvers can approve or reject")
    if invoice.submitter_id == actor.id:
        raise PermissionDenied("approvers cannot decide their own invoice")
    if invoice.state != InvoiceState.submitted:
        raise InvalidOperation(f"invoice is already {invoice.state.value}")

    already = session.scalar(
        select(Approval).where(
            Approval.invoice_id == invoice.id, Approval.approver_id == actor.id
        )
    )
    if already is not None:
        raise InvalidOperation("approver has already decided this invoice")

    session.add(
        Approval(invoice_id=invoice.id, approver_id=actor.id, decision=decision)
    )
    session.flush()

    before = invoice.state

    if decision == Decision.reject:
        invoice.state = InvoiceState.rejected
        _audit(
            session,
            invoice_id=invoice.id,
            actor_id=actor.id,
            action="rejected",
            state_before=before.value,
            state_after=invoice.state.value,
        )
        _notify(session, invoice, "rejected", invoice.state)
        session.commit()
        return invoice

    # decision == approve
    approvals = session.scalars(
        select(Approval).where(
            Approval.invoice_id == invoice.id, Approval.decision == Decision.approve
        )
    ).all()
    if len(approvals) >= _required_approvals(invoice):
        invoice.state = InvoiceState.approved
        _audit(
            session,
            invoice_id=invoice.id,
            actor_id=actor.id,
            action="approved",
            state_before=before.value,
            state_after=invoice.state.value,
        )
        _notify(session, invoice, "approved", invoice.state)
    else:
        # Partial approval: no state change yet, but record the action.
        _audit(
            session,
            invoice_id=invoice.id,
            actor_id=actor.id,
            action="approval_recorded",
            state_before=before.value,
            state_after=invoice.state.value,
            detail=f"{len(approvals)}/{_required_approvals(invoice)} approvals",
        )
    session.commit()
    return invoice


def pending_for_approver(
    session: Session,
    approver: User,
    *,
    name: str | None = None,
    min_amount_cents: int | None = None,
    max_amount_cents: int | None = None,
) -> list[Invoice]:
    stmt = select(Invoice).where(
        Invoice.state == InvoiceState.submitted,
        Invoice.submitter_id != approver.id,
    )
    if name:
        stmt = stmt.where(Invoice.counterparty_name.ilike(f"%{name}%"))
    if min_amount_cents is not None:
        stmt = stmt.where(Invoice.amount_cents >= min_amount_cents)
    if max_amount_cents is not None:
        stmt = stmt.where(Invoice.amount_cents <= max_amount_cents)

    decided_ids = set(
        session.scalars(
            select(Approval.invoice_id).where(Approval.approver_id == approver.id)
        )
    )
    return [inv for inv in session.scalars(stmt.order_by(Invoice.id)) if inv.id not in decided_ids]


# --- personal data (GDPR) -------------------------------------------------

def read_contact_email(session: Session, *, invoice_id: int, actor: User) -> str | None:
    invoice = get_invoice(session, invoice_id)
    _audit(
        session,
        invoice_id=invoice.id,
        actor_id=actor.id,
        action="contact_email_read",
        detail="counterparty_contact_email accessed",
    )
    session.commit()
    return invoice.counterparty_contact_email


def erase_contact_email(session: Session, *, invoice_id: int, actor: User) -> Invoice:
    invoice = get_invoice(session, invoice_id)
    invoice.counterparty_contact_email = None
    invoice.contact_email_erased = True
    _audit(
        session,
        invoice_id=invoice.id,
        actor_id=actor.id,
        action="contact_email_erased",
        detail="counterparty_contact_email erased on request",
    )
    session.commit()
    return invoice


# --- notifications --------------------------------------------------------

def _notify(session: Session, invoice: Invoice, event: str, state: InvoiceState) -> None:
    submitter = session.get(User, invoice.submitter_id)
    if submitter is None:
        return
    email_stub.send_email(
        to=submitter.email,
        subject=f"Invoice #{invoice.id} {event}",
        body=(
            f"Invoice #{invoice.id} for {invoice.counterparty_name} "
            f"(EUR {invoice.amount_cents / 100:.2f}) is now {state.value}."
        ),
    )
