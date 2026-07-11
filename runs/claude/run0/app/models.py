"""SQLAlchemy ORM models.

Domain notes:
- An invoice moves through states submitted -> approved/rejected.
- Approvals are recorded per (invoice, approver) so the two-approver rule for
  amounts over 10,000 EUR can count *distinct* approvers.
- AuditLog is append-only: the application exposes no update or delete path.
- counterparty_contact_email is personal data (GDPR). It can be erased and
  every read is logged.
"""
from __future__ import annotations

import enum
from datetime import date, datetime, timezone

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Role(str, enum.Enum):
    submitter = "submitter"
    approver = "approver"


class InvoiceState(str, enum.Enum):
    submitted = "submitted"
    approved = "approved"
    rejected = "rejected"


class Decision(str, enum.Enum):
    approve = "approve"
    reject = "reject"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[Role] = mapped_column(Enum(Role), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Invoice(Base):
    __tablename__ = "invoices"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Amount stored in integer cents to avoid floating point money errors.
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    counterparty_name: Mapped[str] = mapped_column(String, nullable=False)
    counterparty_contact_email: Mapped[str | None] = mapped_column(String, nullable=True)
    contact_email_erased: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    pdf_path: Mapped[str] = mapped_column(String, nullable=False)
    state: Mapped[InvoiceState] = mapped_column(
        Enum(InvoiceState), default=InvoiceState.submitted, nullable=False
    )
    submitter_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    submitter: Mapped[User] = relationship("User")
    approvals: Mapped[list["Approval"]] = relationship(
        "Approval", back_populates="invoice", cascade="all, delete-orphan"
    )


class Approval(Base):
    __tablename__ = "approvals"
    __table_args__ = (
        # A given approver decides at most once per invoice.
        UniqueConstraint("invoice_id", "approver_id", name="uq_invoice_approver"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"), nullable=False)
    approver_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    decision: Mapped[Decision] = mapped_column(Enum(Decision), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    invoice: Mapped[Invoice] = relationship("Invoice", back_populates="approvals")
    approver: Mapped[User] = relationship("User")


class AuditLog(Base):
    """Append-only audit trail. No update/delete path is exposed anywhere."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int | None] = mapped_column(ForeignKey("invoices.id"), nullable=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    action: Mapped[str] = mapped_column(String, nullable=False)
    state_before: Mapped[str | None] = mapped_column(String, nullable=True)
    state_after: Mapped[str | None] = mapped_column(String, nullable=True)
    detail: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Session(Base):
    """Server-side auth session token."""

    __tablename__ = "sessions"

    token: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
