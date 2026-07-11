from __future__ import annotations

import base64
import json
import os
import secrets
import sqlite3
from contextlib import asynccontextmanager
from datetime import date, datetime, UTC
from decimal import Decimal, InvalidOperation
from hashlib import pbkdf2_hmac
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile, status
from pydantic import BaseModel, EmailStr, Field


APP_ROOT = Path(__file__).resolve().parent.parent


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def database_path() -> Path:
    return Path(os.environ.get("INVOICE_DB_PATH", APP_ROOT / "data" / "invoices.sqlite3"))


def upload_dir() -> Path:
    return Path(os.environ.get("INVOICE_UPLOAD_DIR", APP_ROOT / "data" / "uploads"))


def notification_log_path() -> Path:
    return Path(os.environ.get("NOTIFICATION_LOG_PATH", APP_ROOT / "data" / "notifications.log"))


def connect() -> sqlite3.Connection:
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                password_salt TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('submitter', 'approver')),
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS invoices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                submitter_id INTEGER NOT NULL REFERENCES users(id),
                amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
                counterparty_name TEXT NOT NULL,
                counterparty_contact_email TEXT,
                due_date TEXT NOT NULL,
                pdf_path TEXT NOT NULL,
                state TEXT NOT NULL CHECK (state IN ('submitted', 'approved', 'rejected')),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS invoice_approvals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                invoice_id INTEGER NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
                approver_id INTEGER NOT NULL REFERENCES users(id),
                created_at TEXT NOT NULL,
                UNIQUE(invoice_id, approver_id)
            );

            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                invoice_id INTEGER REFERENCES invoices(id),
                actor_user_id INTEGER REFERENCES users(id),
                action TEXT NOT NULL,
                state_before TEXT,
                state_after TEXT,
                occurred_at TEXT NOT NULL,
                details TEXT NOT NULL DEFAULT '{}'
            );

            CREATE TRIGGER IF NOT EXISTS audit_logs_no_update
            BEFORE UPDATE ON audit_logs
            BEGIN
                SELECT RAISE(ABORT, 'audit logs are append-only');
            END;

            CREATE TRIGGER IF NOT EXISTS audit_logs_no_delete
            BEFORE DELETE ON audit_logs
            BEGIN
                SELECT RAISE(ABORT, 'audit logs are append-only');
            END;
            """
        )


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    upload_dir().mkdir(parents=True, exist_ok=True)
    notification_log_path().parent.mkdir(parents=True, exist_ok=True)
    yield


APP = FastAPI(title="Nordvik Invoice Approval Service", lifespan=lifespan)
app = APP


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    role: str = Field(pattern="^(submitter|approver)$")


class UserPublic(BaseModel):
    id: int
    email: EmailStr
    role: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class InvoicePublic(BaseModel):
    id: int
    submitter_id: int
    amount_euros: str
    counterparty_name: str
    due_date: str
    state: str
    approvals_count: int
    required_approvals: int


class ContactEmailResponse(BaseModel):
    invoice_id: int
    counterparty_contact_email: EmailStr | None


class AuditLogPublic(BaseModel):
    id: int
    invoice_id: int | None
    actor_user_id: int | None
    action: str
    state_before: str | None
    state_after: str | None
    occurred_at: str
    details: dict[str, Any]


class RejectRequest(BaseModel):
    reason: str | None = None


def hash_password(password: str, salt: bytes | None = None) -> tuple[str, str]:
    actual_salt = salt or secrets.token_bytes(16)
    digest = pbkdf2_hmac("sha256", password.encode("utf-8"), actual_salt, 390_000)
    return (
        base64.b64encode(actual_salt).decode("ascii"),
        base64.b64encode(digest).decode("ascii"),
    )


def verify_password(password: str, salt_b64: str, expected_hash_b64: str) -> bool:
    salt = base64.b64decode(salt_b64.encode("ascii"))
    _, actual_hash_b64 = hash_password(password, salt)
    return secrets.compare_digest(actual_hash_b64, expected_hash_b64)


def auth_user(authorization: Annotated[str | None, Header()] = None) -> sqlite3.Row:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")
    token = authorization.removeprefix("Bearer ").strip()
    with connect() as conn:
        user = conn.execute(
            """
            SELECT users.* FROM sessions
            JOIN users ON users.id = sessions.user_id
            WHERE sessions.token = ?
            """,
            (token,),
        ).fetchone()
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid bearer token")
    return user


def require_role(user: sqlite3.Row, role: str) -> None:
    if user["role"] != role:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"Requires {role} role")


def amount_to_cents(amount: Decimal) -> int:
    try:
        cents = amount * Decimal("100")
        if cents != cents.to_integral_value() or cents <= 0:
            raise ValueError
        return int(cents)
    except (InvalidOperation, ValueError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Amount must be a positive euro amount with at most two decimals",
        ) from None


def cents_to_euros(cents: int) -> str:
    return f"{Decimal(cents) / Decimal('100'):.2f}"


def required_approvals(amount_cents: int) -> int:
    return 2 if amount_cents > 1_000_000 else 1


def audit(
    conn: sqlite3.Connection,
    *,
    invoice_id: int | None,
    actor_user_id: int | None,
    action: str,
    state_before: str | None,
    state_after: str | None,
    details: dict[str, Any] | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO audit_logs (
            invoice_id, actor_user_id, action, state_before, state_after, occurred_at, details
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            invoice_id,
            actor_user_id,
            action,
            state_before,
            state_after,
            now_iso(),
            json.dumps(details or {}, sort_keys=True),
        ),
    )


def send_notification(submitter_email: str, invoice_id: int, state_before: str | None, state_after: str) -> None:
    entry = {
        "to": submitter_email,
        "subject": f"Invoice {invoice_id} state changed to {state_after}",
        "body": f"Invoice {invoice_id} moved from {state_before or 'none'} to {state_after}.",
        "sent_at": now_iso(),
    }
    path = notification_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(entry, sort_keys=True) + "\n")


def invoice_public(conn: sqlite3.Connection, row: sqlite3.Row) -> InvoicePublic:
    approvals_count = conn.execute(
        "SELECT COUNT(*) FROM invoice_approvals WHERE invoice_id = ?", (row["id"],)
    ).fetchone()[0]
    return InvoicePublic(
        id=row["id"],
        submitter_id=row["submitter_id"],
        amount_euros=cents_to_euros(row["amount_cents"]),
        counterparty_name=row["counterparty_name"],
        due_date=row["due_date"],
        state=row["state"],
        approvals_count=approvals_count,
        required_approvals=required_approvals(row["amount_cents"]),
    )


def load_invoice(conn: sqlite3.Connection, invoice_id: int) -> sqlite3.Row:
    invoice = conn.execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
    if invoice is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found")
    return invoice


@app.post("/users", response_model=UserPublic, status_code=status.HTTP_201_CREATED)
def create_user(payload: UserCreate) -> UserPublic:
    salt_b64, hash_b64 = hash_password(payload.password)
    with connect() as conn:
        try:
            cursor = conn.execute(
                """
                INSERT INTO users (email, password_salt, password_hash, role, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (payload.email.lower(), salt_b64, hash_b64, payload.role, now_iso()),
            )
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already exists") from None
        user_id = cursor.lastrowid
    return UserPublic(id=user_id, email=payload.email.lower(), role=payload.role)


@app.post("/auth/login", response_model=LoginResponse)
def login(payload: LoginRequest) -> LoginResponse:
    with connect() as conn:
        user = conn.execute("SELECT * FROM users WHERE email = ?", (payload.email.lower(),)).fetchone()
        if user is None or not verify_password(payload.password, user["password_salt"], user["password_hash"]):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
        token = secrets.token_urlsafe(32)
        conn.execute(
            "INSERT INTO sessions (token, user_id, created_at) VALUES (?, ?, ?)",
            (token, user["id"], now_iso()),
        )
    return LoginResponse(access_token=token)


@app.post("/invoices", response_model=InvoicePublic, status_code=status.HTTP_201_CREATED)
async def create_invoice(
    user: Annotated[sqlite3.Row, Depends(auth_user)],
    amount_euros: Annotated[Decimal, Form()],
    counterparty_name: Annotated[str, Form(min_length=1)],
    counterparty_contact_email: Annotated[EmailStr, Form()],
    due_date: Annotated[str, Form()],
    pdf: Annotated[UploadFile, File()],
) -> InvoicePublic:
    require_role(user, "submitter")
    amount_cents = amount_to_cents(amount_euros)
    try:
        date.fromisoformat(due_date)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Due date must be YYYY-MM-DD") from None
    contents = await pdf.read()
    if not contents.startswith(b"%PDF"):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Attachment must be a PDF")
    filename = f"{secrets.token_urlsafe(18)}.pdf"
    target = upload_dir() / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(contents)

    with connect() as conn:
        created_at = now_iso()
        cursor = conn.execute(
            """
            INSERT INTO invoices (
                submitter_id, amount_cents, counterparty_name, counterparty_contact_email,
                due_date, pdf_path, state, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'submitted', ?, ?)
            """,
            (
                user["id"],
                amount_cents,
                counterparty_name,
                str(counterparty_contact_email).lower(),
                due_date,
                str(target),
                created_at,
                created_at,
            ),
        )
        invoice_id = cursor.lastrowid
        audit(
            conn,
            invoice_id=invoice_id,
            actor_user_id=user["id"],
            action="state_changed",
            state_before=None,
            state_after="submitted",
        )
        send_notification(user["email"], invoice_id, None, "submitted")
        invoice = load_invoice(conn, invoice_id)
        return invoice_public(conn, invoice)


@app.get("/invoices/pending", response_model=list[InvoicePublic])
def pending_invoices(
    user: Annotated[sqlite3.Row, Depends(auth_user)],
    counterparty_name: str | None = None,
    min_amount_euros: Decimal | None = None,
    max_amount_euros: Decimal | None = None,
) -> list[InvoicePublic]:
    require_role(user, "approver")
    params: list[Any] = [user["id"], user["id"]]
    filters = [
        "state = 'submitted'",
        "submitter_id != ?",
        "NOT EXISTS (SELECT 1 FROM invoice_approvals WHERE invoice_id = invoices.id AND approver_id = ?)",
    ]
    if counterparty_name:
        filters.append("LOWER(counterparty_name) LIKE ?")
        params.append(f"%{counterparty_name.lower()}%")
    if min_amount_euros is not None:
        filters.append("amount_cents >= ?")
        params.append(amount_to_cents(min_amount_euros))
    if max_amount_euros is not None:
        filters.append("amount_cents <= ?")
        params.append(amount_to_cents(max_amount_euros))
    with connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM invoices WHERE {' AND '.join(filters)} ORDER BY due_date, id",
            params,
        ).fetchall()
        return [invoice_public(conn, row) for row in rows]


@app.get("/invoices/{invoice_id}", response_model=InvoicePublic)
def get_invoice(invoice_id: int, user: Annotated[sqlite3.Row, Depends(auth_user)]) -> InvoicePublic:
    with connect() as conn:
        invoice = load_invoice(conn, invoice_id)
        return invoice_public(conn, invoice)


@app.get("/invoices/{invoice_id}/counterparty-contact-email", response_model=ContactEmailResponse)
def get_counterparty_contact_email(
    invoice_id: int, user: Annotated[sqlite3.Row, Depends(auth_user)]
) -> ContactEmailResponse:
    with connect() as conn:
        invoice = load_invoice(conn, invoice_id)
        audit(
            conn,
            invoice_id=invoice_id,
            actor_user_id=user["id"],
            action="read_counterparty_contact_email",
            state_before=invoice["state"],
            state_after=invoice["state"],
        )
        return ContactEmailResponse(
            invoice_id=invoice_id,
            counterparty_contact_email=invoice["counterparty_contact_email"],
        )


@app.post("/invoices/{invoice_id}/erase-counterparty-contact-email", response_model=InvoicePublic)
def erase_counterparty_contact_email(
    invoice_id: int, user: Annotated[sqlite3.Row, Depends(auth_user)]
) -> InvoicePublic:
    with connect() as conn:
        invoice = load_invoice(conn, invoice_id)
        conn.execute(
            "UPDATE invoices SET counterparty_contact_email = NULL, updated_at = ? WHERE id = ?",
            (now_iso(), invoice_id),
        )
        audit(
            conn,
            invoice_id=invoice_id,
            actor_user_id=user["id"],
            action="erase_counterparty_contact_email",
            state_before=invoice["state"],
            state_after=invoice["state"],
        )
        return invoice_public(conn, load_invoice(conn, invoice_id))


@app.post("/invoices/{invoice_id}/approve", response_model=InvoicePublic)
def approve_invoice(invoice_id: int, user: Annotated[sqlite3.Row, Depends(auth_user)]) -> InvoicePublic:
    require_role(user, "approver")
    with connect() as conn:
        invoice = load_invoice(conn, invoice_id)
        if invoice["submitter_id"] == user["id"]:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Approver cannot approve own invoice")
        if invoice["state"] != "submitted":
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Invoice is not pending approval")
        try:
            conn.execute(
                "INSERT INTO invoice_approvals (invoice_id, approver_id, created_at) VALUES (?, ?, ?)",
                (invoice_id, user["id"], now_iso()),
            )
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Approver already approved this invoice") from None

        approvals_count = conn.execute(
            "SELECT COUNT(*) FROM invoice_approvals WHERE invoice_id = ?", (invoice_id,)
        ).fetchone()[0]
        before = invoice["state"]
        if approvals_count >= required_approvals(invoice["amount_cents"]):
            conn.execute("UPDATE invoices SET state = 'approved', updated_at = ? WHERE id = ?", (now_iso(), invoice_id))
            audit(
                conn,
                invoice_id=invoice_id,
                actor_user_id=user["id"],
                action="state_changed",
                state_before=before,
                state_after="approved",
            )
            submitter = conn.execute("SELECT email FROM users WHERE id = ?", (invoice["submitter_id"],)).fetchone()
            send_notification(submitter["email"], invoice_id, before, "approved")
        else:
            audit(
                conn,
                invoice_id=invoice_id,
                actor_user_id=user["id"],
                action="approval_recorded",
                state_before=before,
                state_after=before,
                details={"approvals_count": approvals_count},
            )
        return invoice_public(conn, load_invoice(conn, invoice_id))


@app.post("/invoices/{invoice_id}/reject", response_model=InvoicePublic)
def reject_invoice(
    invoice_id: int,
    user: Annotated[sqlite3.Row, Depends(auth_user)],
    payload: RejectRequest | None = None,
) -> InvoicePublic:
    require_role(user, "approver")
    with connect() as conn:
        invoice = load_invoice(conn, invoice_id)
        if invoice["submitter_id"] == user["id"]:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Approver cannot reject own invoice")
        if invoice["state"] != "submitted":
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Invoice is not pending approval")
        before = invoice["state"]
        conn.execute("UPDATE invoices SET state = 'rejected', updated_at = ? WHERE id = ?", (now_iso(), invoice_id))
        audit(
            conn,
            invoice_id=invoice_id,
            actor_user_id=user["id"],
            action="state_changed",
            state_before=before,
            state_after="rejected",
            details={"reason": payload.reason} if payload and payload.reason else None,
        )
        submitter = conn.execute("SELECT email FROM users WHERE id = ?", (invoice["submitter_id"],)).fetchone()
        send_notification(submitter["email"], invoice_id, before, "rejected")
        return invoice_public(conn, load_invoice(conn, invoice_id))


@app.get("/audit-logs", response_model=list[AuditLogPublic])
def audit_logs(user: Annotated[sqlite3.Row, Depends(auth_user)], invoice_id: int | None = None) -> list[AuditLogPublic]:
    params: list[Any] = []
    where = ""
    if invoice_id is not None:
        where = "WHERE invoice_id = ?"
        params.append(invoice_id)
    with connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM audit_logs {where} ORDER BY id",
            params,
        ).fetchall()
    return [
        AuditLogPublic(
            id=row["id"],
            invoice_id=row["invoice_id"],
            actor_user_id=row["actor_user_id"],
            action=row["action"],
            state_before=row["state_before"],
            state_after=row["state_after"],
            occurred_at=row["occurred_at"],
            details=json.loads(row["details"]),
        )
        for row in rows
    ]
