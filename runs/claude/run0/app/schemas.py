"""Pydantic response/request schemas for the HTTP API."""
from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, EmailStr


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    token: str
    role: str


class InvoiceOut(BaseModel):
    """Default invoice view. Deliberately omits counterparty_contact_email so
    reads of that personal-data field always go through the audited endpoint."""

    id: int
    amount_cents: int
    counterparty_name: str
    contact_email_erased: bool
    due_date: date
    state: str
    submitter_id: int
    created_at: datetime


class ContactEmailOut(BaseModel):
    invoice_id: int
    counterparty_contact_email: str | None


class AuditOut(BaseModel):
    id: int
    invoice_id: int | None
    actor_id: int | None
    action: str
    state_before: str | None
    state_after: str | None
    detail: str | None
    created_at: datetime
