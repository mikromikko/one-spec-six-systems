from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import json
import secrets
import sqlite3
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Iterator


DEFAULT_USERS = (
    ("submitter@example.com", "password123", "submitter"),
    ("approver1@example.com", "password123", "approver"),
    ("approver2@example.com", "password123", "approver"),
)


class ServiceError(Exception):
    status_code = 400

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class AuthenticationError(ServiceError):
    status_code = 401


class AuthorizationError(ServiceError):
    status_code = 403


class NotFoundError(ServiceError):
    status_code = 404


class ConflictError(ServiceError):
    status_code = 409


def utc_now() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat()


def password_hash(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000)
    return f"pbkdf2_sha256${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, salt_hex, digest_hex = encoded.split("$", 2)
    except ValueError:
        return False
    if algorithm != "pbkdf2_sha256":
        return False
    expected = password_hash(password, bytes.fromhex(salt_hex)).split("$", 2)[2]
    return hmac.compare_digest(expected, digest_hex)


def amount_to_cents(amount: Any) -> int:
    try:
        decimal_amount = Decimal(str(amount))
    except (InvalidOperation, ValueError):
        raise ServiceError("amount_euros must be a valid euro amount") from None
    cents = (decimal_amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    if cents <= 0:
        raise ServiceError("amount_euros must be greater than zero")
    return int(cents)


def cents_to_euros(cents: int) -> str:
    return f"{Decimal(cents) / Decimal(100):.2f}"


def required_approvals(amount_cents: int) -> int:
    return 2 if amount_cents > 1_000_000 else 1


class InvoiceService:
    def __init__(self, db_path: str | Path = "invoice_service.sqlite3", notification_log: str | Path = "email_notifications.log"):
        self.db_path = str(db_path)
        self.notification_log = Path(notification_log)

    def initialize(self, seed_defaults: bool = True) -> None:
        with self._connect() as db:
            db.executescript(
                """
                PRAGMA foreign_keys = ON;

                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL CHECK (role IN ('submitter', 'approver')),
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS sessions (
                    token TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id),
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS invoices (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    submitter_id INTEGER NOT NULL REFERENCES users(id),
                    amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
                    counterparty_name TEXT NOT NULL,
                    counterparty_contact_email TEXT,
                    due_date TEXT NOT NULL,
                    attachment_filename TEXT NOT NULL,
                    attachment_pdf BLOB NOT NULL,
                    state TEXT NOT NULL CHECK (state IN ('submitted', 'approved', 'rejected')),
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS invoice_approvals (
                    invoice_id INTEGER NOT NULL REFERENCES invoices(id),
                    approver_id INTEGER NOT NULL REFERENCES users(id),
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (invoice_id, approver_id)
                );

                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    invoice_id INTEGER REFERENCES invoices(id),
                    actor_user_id INTEGER NOT NULL REFERENCES users(id),
                    action TEXT NOT NULL,
                    state_before TEXT,
                    state_after TEXT,
                    occurred_at TEXT NOT NULL,
                    details TEXT NOT NULL DEFAULT '{}'
                );

                CREATE TRIGGER IF NOT EXISTS audit_log_no_update
                BEFORE UPDATE ON audit_log
                BEGIN
                    SELECT RAISE(ABORT, 'audit_log is append-only');
                END;

                CREATE TRIGGER IF NOT EXISTS audit_log_no_delete
                BEFORE DELETE ON audit_log
                BEGIN
                    SELECT RAISE(ABORT, 'audit_log is append-only');
                END;
                """
            )
        if seed_defaults:
            for email, password, role in DEFAULT_USERS:
                self.create_user(email, password, role, ignore_existing=True)

    def create_user(self, email: str, password: str, role: str, ignore_existing: bool = False) -> int:
        if role not in {"submitter", "approver"}:
            raise ServiceError("role must be submitter or approver")
        with self._connect() as db:
            try:
                cursor = db.execute(
                    "INSERT INTO users (email, password_hash, role, created_at) VALUES (?, ?, ?, ?)",
                    (email.lower(), password_hash(password), role, utc_now()),
                )
            except sqlite3.IntegrityError:
                if ignore_existing:
                    row = db.execute("SELECT id FROM users WHERE email = ?", (email.lower(),)).fetchone()
                    return int(row["id"])
                raise ConflictError("user already exists") from None
            return int(cursor.lastrowid)

    def login(self, email: str, password: str) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute("SELECT * FROM users WHERE email = ?", (email.lower(),)).fetchone()
            if row is None or not verify_password(password, row["password_hash"]):
                raise AuthenticationError("invalid email or password")
            token = secrets.token_urlsafe(32)
            db.execute(
                "INSERT INTO sessions (token, user_id, created_at) VALUES (?, ?, ?)",
                (token, row["id"], utc_now()),
            )
            return {"token": token, "user": self._user_dict(row)}

    def authenticate(self, token: str | None) -> dict[str, Any]:
        if not token:
            raise AuthenticationError("missing bearer token")
        with self._connect() as db:
            row = db.execute(
                """
                SELECT users.* FROM sessions
                JOIN users ON users.id = sessions.user_id
                WHERE sessions.token = ?
                """,
                (token,),
            ).fetchone()
            if row is None:
                raise AuthenticationError("invalid bearer token")
            return self._user_dict(row)

    def create_invoice(
        self,
        actor: dict[str, Any],
        amount_euros: Any,
        counterparty_name: str,
        counterparty_contact_email: str,
        due_date: str,
        attachment_filename: str,
        attachment_pdf: bytes,
    ) -> dict[str, Any]:
        self._require_role(actor, "submitter")
        amount_cents = amount_to_cents(amount_euros)
        self._validate_invoice_input(counterparty_name, counterparty_contact_email, due_date, attachment_filename, attachment_pdf)
        now = utc_now()
        with self._connect() as db:
            cursor = db.execute(
                """
                INSERT INTO invoices (
                    submitter_id, amount_cents, counterparty_name, counterparty_contact_email,
                    due_date, attachment_filename, attachment_pdf, state, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'submitted', ?)
                """,
                (
                    actor["id"],
                    amount_cents,
                    counterparty_name.strip(),
                    counterparty_contact_email.strip().lower(),
                    due_date,
                    attachment_filename,
                    attachment_pdf,
                    now,
                ),
            )
            invoice_id = int(cursor.lastrowid)
            self._audit(db, invoice_id, actor["id"], "create_invoice", None, "submitted", {"message": "invoice submitted"})
            invoice = self._invoice_by_id(db, invoice_id)
            submitter = self._user_by_id(db, actor["id"])
        self._send_notification(submitter["email"], invoice_id, None, "submitted")
        return self._invoice_summary(invoice, approval_count=0)

    def get_invoice(self, actor: dict[str, Any], invoice_id: int) -> dict[str, Any]:
        with self._connect() as db:
            invoice = self._invoice_by_id(db, invoice_id)
            approval_count = self._approval_count(db, invoice_id)
            if invoice["counterparty_contact_email"] is not None:
                self._audit(
                    db,
                    invoice_id,
                    actor["id"],
                    "read_counterparty_contact_email",
                    invoice["state"],
                    invoice["state"],
                    {"field": "counterparty_contact_email"},
                )
            result = self._invoice_summary(invoice, approval_count=approval_count, include_contact=True)
            result["attachment_pdf_base64"] = base64.b64encode(invoice["attachment_pdf"]).decode("ascii")
            return result

    def pending_invoices(
        self,
        actor: dict[str, Any],
        counterparty_name: str | None = None,
        min_amount_euros: Any | None = None,
        max_amount_euros: Any | None = None,
    ) -> list[dict[str, Any]]:
        self._require_role(actor, "approver")
        clauses = [
            "state = 'submitted'",
            "submitter_id != ?",
            "NOT EXISTS (SELECT 1 FROM invoice_approvals ia WHERE ia.invoice_id = invoices.id AND ia.approver_id = ?)",
        ]
        params: list[Any] = [actor["id"], actor["id"]]
        if counterparty_name:
            clauses.append("LOWER(counterparty_name) LIKE ?")
            params.append(f"%{counterparty_name.lower()}%")
        if min_amount_euros is not None:
            clauses.append("amount_cents >= ?")
            params.append(amount_to_cents(min_amount_euros))
        if max_amount_euros is not None:
            clauses.append("amount_cents <= ?")
            params.append(amount_to_cents(max_amount_euros))
        with self._connect() as db:
            rows = db.execute(
                f"""
                SELECT * FROM invoices
                WHERE {' AND '.join(clauses)}
                ORDER BY due_date ASC, id ASC
                """,
                params,
            ).fetchall()
            return [self._invoice_summary(row, approval_count=self._approval_count(db, row["id"])) for row in rows]

    def approve_invoice(self, actor: dict[str, Any], invoice_id: int) -> dict[str, Any]:
        self._require_role(actor, "approver")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            invoice = self._invoice_by_id(db, invoice_id)
            self._ensure_can_decide(actor, invoice)
            try:
                db.execute(
                    "INSERT INTO invoice_approvals (invoice_id, approver_id, created_at) VALUES (?, ?, ?)",
                    (invoice_id, actor["id"], utc_now()),
                )
            except sqlite3.IntegrityError:
                raise ConflictError("approver has already approved this invoice") from None
            approval_count = self._approval_count(db, invoice_id)
            before = invoice["state"]
            after = before
            if approval_count >= required_approvals(invoice["amount_cents"]):
                after = "approved"
                db.execute("UPDATE invoices SET state = 'approved' WHERE id = ?", (invoice_id,))
                self._audit(db, invoice_id, actor["id"], "approve_invoice", before, after, {"approval_count": approval_count})
                submitter = self._user_by_id(db, invoice["submitter_id"])
            else:
                submitter = None
            updated = self._invoice_by_id(db, invoice_id)
        if after != before and submitter is not None:
            self._send_notification(submitter["email"], invoice_id, before, after)
        return self._invoice_summary(updated, approval_count=approval_count)

    def reject_invoice(self, actor: dict[str, Any], invoice_id: int) -> dict[str, Any]:
        self._require_role(actor, "approver")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            invoice = self._invoice_by_id(db, invoice_id)
            self._ensure_can_decide(actor, invoice)
            before = invoice["state"]
            after = "rejected"
            db.execute("UPDATE invoices SET state = 'rejected' WHERE id = ?", (invoice_id,))
            self._audit(db, invoice_id, actor["id"], "reject_invoice", before, after, {})
            submitter = self._user_by_id(db, invoice["submitter_id"])
            updated = self._invoice_by_id(db, invoice_id)
        self._send_notification(submitter["email"], invoice_id, before, after)
        return self._invoice_summary(updated, approval_count=self.approval_count(invoice_id))

    def erase_counterparty_contact_email(self, actor: dict[str, Any], invoice_id: int) -> dict[str, Any]:
        with self._connect() as db:
            invoice = self._invoice_by_id(db, invoice_id)
            db.execute("UPDATE invoices SET counterparty_contact_email = NULL WHERE id = ?", (invoice_id,))
            self._audit(
                db,
                invoice_id,
                actor["id"],
                "erase_counterparty_contact_email",
                invoice["state"],
                invoice["state"],
                {"field": "counterparty_contact_email"},
            )
            updated = self._invoice_by_id(db, invoice_id)
            return self._invoice_summary(updated, approval_count=self._approval_count(db, invoice_id), include_contact=True)

    def audit_log(self, actor: dict[str, Any], invoice_id: int | None = None) -> list[dict[str, Any]]:
        self.authenticate_actor(actor)
        params: list[Any] = []
        where = ""
        if invoice_id is not None:
            where = "WHERE invoice_id = ?"
            params.append(invoice_id)
        with self._connect() as db:
            rows = db.execute(
                f"""
                SELECT audit_log.*, users.email AS actor_email
                FROM audit_log
                JOIN users ON users.id = audit_log.actor_user_id
                {where}
                ORDER BY audit_log.id ASC
                """,
                params,
            ).fetchall()
            return [self._audit_dict(row) for row in rows]

    def approval_count(self, invoice_id: int) -> int:
        with self._connect() as db:
            self._invoice_by_id(db, invoice_id)
            return self._approval_count(db, invoice_id)

    def authenticate_actor(self, actor: dict[str, Any]) -> None:
        if "id" not in actor or "role" not in actor:
            raise AuthenticationError("invalid actor")

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.db_path, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        try:
            yield db
            if db.in_transaction:
                db.commit()
        except Exception:
            if db.in_transaction:
                db.rollback()
            raise
        finally:
            db.close()

    def _require_role(self, actor: dict[str, Any], role: str) -> None:
        self.authenticate_actor(actor)
        if actor["role"] != role:
            raise AuthorizationError(f"{role} role required")

    def _ensure_can_decide(self, actor: dict[str, Any], invoice: sqlite3.Row) -> None:
        if invoice["state"] != "submitted":
            raise ConflictError("invoice is not pending approval")
        if invoice["submitter_id"] == actor["id"]:
            raise AuthorizationError("approvers cannot approve or reject invoices they submitted")

    def _validate_invoice_input(
        self,
        counterparty_name: str,
        counterparty_contact_email: str,
        due_date: str,
        attachment_filename: str,
        attachment_pdf: bytes,
    ) -> None:
        if not counterparty_name or not counterparty_name.strip():
            raise ServiceError("counterparty_name is required")
        if "@" not in counterparty_contact_email or not counterparty_contact_email.strip():
            raise ServiceError("counterparty_contact_email must be a valid email")
        try:
            dt.date.fromisoformat(due_date)
        except ValueError:
            raise ServiceError("due_date must use YYYY-MM-DD format") from None
        if not attachment_filename.lower().endswith(".pdf"):
            raise ServiceError("attachment_filename must end with .pdf")
        if not attachment_pdf.startswith(b"%PDF"):
            raise ServiceError("attachment_pdf must be a PDF document")

    def _invoice_by_id(self, db: sqlite3.Connection, invoice_id: int) -> sqlite3.Row:
        row = db.execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
        if row is None:
            raise NotFoundError("invoice not found")
        return row

    def _user_by_id(self, db: sqlite3.Connection, user_id: int) -> sqlite3.Row:
        row = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if row is None:
            raise NotFoundError("user not found")
        return row

    def _approval_count(self, db: sqlite3.Connection, invoice_id: int) -> int:
        row = db.execute("SELECT COUNT(*) AS count FROM invoice_approvals WHERE invoice_id = ?", (invoice_id,)).fetchone()
        return int(row["count"])

    def _audit(
        self,
        db: sqlite3.Connection,
        invoice_id: int | None,
        actor_user_id: int,
        action: str,
        state_before: str | None,
        state_after: str | None,
        details: dict[str, Any],
    ) -> None:
        db.execute(
            """
            INSERT INTO audit_log (invoice_id, actor_user_id, action, state_before, state_after, occurred_at, details)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (invoice_id, actor_user_id, action, state_before, state_after, utc_now(), json.dumps(details, sort_keys=True)),
        )

    def _send_notification(self, submitter_email: str, invoice_id: int, state_before: str | None, state_after: str) -> None:
        self.notification_log.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "to": submitter_email,
            "invoice_id": invoice_id,
            "subject": f"Invoice {invoice_id} state changed to {state_after}",
            "state_before": state_before,
            "state_after": state_after,
            "sent_at": utc_now(),
        }
        with self.notification_log.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True) + "\n")

    def _invoice_summary(self, row: sqlite3.Row, approval_count: int, include_contact: bool = False) -> dict[str, Any]:
        result = {
            "id": row["id"],
            "submitter_id": row["submitter_id"],
            "amount_euros": cents_to_euros(row["amount_cents"]),
            "counterparty_name": row["counterparty_name"],
            "due_date": row["due_date"],
            "attachment_filename": row["attachment_filename"],
            "state": row["state"],
            "approval_count": approval_count,
            "required_approvals": required_approvals(row["amount_cents"]),
            "created_at": row["created_at"],
        }
        if include_contact:
            result["counterparty_contact_email"] = row["counterparty_contact_email"]
        return result

    def _user_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        return {"id": row["id"], "email": row["email"], "role": row["role"]}

    def _audit_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "invoice_id": row["invoice_id"],
            "actor_user_id": row["actor_user_id"],
            "actor_email": row["actor_email"],
            "action": row["action"],
            "state_before": row["state_before"],
            "state_after": row["state_after"],
            "occurred_at": row["occurred_at"],
            "details": json.loads(row["details"]),
        }
