"""FastAPI application: HTTP surface for the invoice approval service."""
from __future__ import annotations

import os
import uuid
from contextlib import asynccontextmanager
from datetime import date
from decimal import Decimal, InvalidOperation as DecimalError

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

from app import services
from app.db import get_session, init_db
from app.models import Decision, User
from app.schemas import (
    AuditOut,
    ContactEmailOut,
    InvoiceOut,
    LoginRequest,
    TokenResponse,
)

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Nordvik Invoice Approval", lifespan=lifespan)


def _upload_dir() -> str:
    return os.environ.get("UPLOAD_DIR", "./uploads")


# --- auth dependency ------------------------------------------------------

def current_user(
    authorization: str | None = Header(default=None),
    session: Session = Depends(get_session),
) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = authorization.split(" ", 1)[1].strip()
    user = services.user_for_token(session, token)
    if user is None:
        raise HTTPException(status_code=401, detail="invalid token")
    return user


def _invoice_out(inv) -> InvoiceOut:
    return InvoiceOut(
        id=inv.id,
        amount_cents=inv.amount_cents,
        counterparty_name=inv.counterparty_name,
        contact_email_erased=inv.contact_email_erased,
        due_date=inv.due_date,
        state=inv.state.value,
        submitter_id=inv.submitter_id,
        created_at=inv.created_at,
    )


# --- routes ---------------------------------------------------------------

@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/auth/login", response_model=TokenResponse)
def login(body: LoginRequest, session: Session = Depends(get_session)) -> TokenResponse:
    token = services.authenticate(session, email=body.email, password=body.password)
    if token is None:
        raise HTTPException(status_code=401, detail="invalid credentials")
    user = services.get_user_by_email(session, body.email)
    return TokenResponse(token=token, role=user.role.value)


@app.post("/invoices", response_model=InvoiceOut, status_code=201)
async def create_invoice(
    amount_eur: str = Form(...),
    counterparty_name: str = Form(...),
    counterparty_contact_email: str = Form(...),
    due_date: date = Form(...),
    pdf: UploadFile = File(...),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> InvoiceOut:
    try:
        amount = Decimal(amount_eur)
    except (DecimalError, ValueError):
        raise HTTPException(status_code=400, detail="invalid amount")
    if amount <= 0:
        raise HTTPException(status_code=400, detail="amount must be positive")
    amount_cents = int((amount * 100).to_integral_value())

    filename = (pdf.filename or "").lower()
    if not filename.endswith(".pdf") and pdf.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="attachment must be a PDF")

    os.makedirs(_upload_dir(), exist_ok=True)
    stored = os.path.join(_upload_dir(), f"{uuid.uuid4().hex}.pdf")
    with open(stored, "wb") as fh:
        fh.write(await pdf.read())

    try:
        inv = services.create_invoice(
            session,
            submitter=user,
            amount_cents=amount_cents,
            counterparty_name=counterparty_name,
            counterparty_contact_email=counterparty_contact_email,
            due_date=due_date,
            pdf_path=stored,
        )
    except services.InvalidOperation as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _invoice_out(inv)


@app.get("/invoices/pending", response_model=list[InvoiceOut])
def pending(
    name: str | None = Query(default=None),
    min_amount_eur: str | None = Query(default=None),
    max_amount_eur: str | None = Query(default=None),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> list[InvoiceOut]:
    if user.role.value != "approver":
        raise HTTPException(status_code=403, detail="only approvers have a pending queue")

    def to_cents(v: str | None) -> int | None:
        if v is None:
            return None
        try:
            return int((Decimal(v) * 100).to_integral_value())
        except (DecimalError, ValueError):
            raise HTTPException(status_code=400, detail="invalid amount filter")

    invoices = services.pending_for_approver(
        session,
        user,
        name=name,
        min_amount_cents=to_cents(min_amount_eur),
        max_amount_cents=to_cents(max_amount_eur),
    )
    return [_invoice_out(i) for i in invoices]


@app.get("/invoices/{invoice_id}", response_model=InvoiceOut)
def get_invoice(
    invoice_id: int,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> InvoiceOut:
    try:
        inv = services.get_invoice(session, invoice_id)
    except services.NotFound:
        raise HTTPException(status_code=404, detail="invoice not found")
    return _invoice_out(inv)


def _decide(invoice_id: int, decision: Decision, user: User, session: Session) -> InvoiceOut:
    try:
        inv = services.decide(session, invoice_id=invoice_id, actor=user, decision=decision)
    except services.NotFound:
        raise HTTPException(status_code=404, detail="invoice not found")
    except services.PermissionDenied as e:
        raise HTTPException(status_code=403, detail=str(e))
    except services.InvalidOperation as e:
        raise HTTPException(status_code=409, detail=str(e))
    return _invoice_out(inv)


@app.post("/invoices/{invoice_id}/approve", response_model=InvoiceOut)
def approve(
    invoice_id: int,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> InvoiceOut:
    return _decide(invoice_id, Decision.approve, user, session)


@app.post("/invoices/{invoice_id}/reject", response_model=InvoiceOut)
def reject(
    invoice_id: int,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> InvoiceOut:
    return _decide(invoice_id, Decision.reject, user, session)


@app.get("/invoices/{invoice_id}/contact-email", response_model=ContactEmailOut)
def read_contact_email(
    invoice_id: int,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> ContactEmailOut:
    try:
        value = services.read_contact_email(session, invoice_id=invoice_id, actor=user)
    except services.NotFound:
        raise HTTPException(status_code=404, detail="invoice not found")
    return ContactEmailOut(invoice_id=invoice_id, counterparty_contact_email=value)


@app.delete("/invoices/{invoice_id}/contact-email", response_model=ContactEmailOut)
def erase_contact_email(
    invoice_id: int,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> ContactEmailOut:
    try:
        services.erase_contact_email(session, invoice_id=invoice_id, actor=user)
    except services.NotFound:
        raise HTTPException(status_code=404, detail="invoice not found")
    return ContactEmailOut(invoice_id=invoice_id, counterparty_contact_email=None)


@app.get("/invoices/{invoice_id}/audit", response_model=list[AuditOut])
def audit(
    invoice_id: int,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> list[AuditOut]:
    entries = services.get_audit_log(session, invoice_id=invoice_id)
    return [
        AuditOut(
            id=e.id,
            invoice_id=e.invoice_id,
            actor_id=e.actor_id,
            action=e.action,
            state_before=e.state_before,
            state_after=e.state_after,
            detail=e.detail,
            created_at=e.created_at,
        )
        for e in entries
    ]
