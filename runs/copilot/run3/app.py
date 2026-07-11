#!/usr/bin/env python3
"""Local invoice approval service for Nordvik Components Oy."""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import sys
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs
from wsgiref.simple_server import make_server

DB_PATH = os.environ.get("INVOICE_DB", "invoices.db")
NOTIFICATION_LOG = os.environ.get("NOTIFICATION_LOG", "notifications.log")
APPROVAL_THRESHOLD_CENTS = 1_000_000
ROLES = {"submitter", "approver"}


class HttpError(Exception):
    def __init__(self, status: int, message: str):
        self.status = status
        self.message = message
        super().__init__(message)


def utc_now() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def json_response(
    start_response: Callable,
    status: int,
    payload: Any,
    headers: list[tuple[str, str]] | None = None,
) -> list[bytes]:
    body = json.dumps(payload, sort_keys=True).encode("utf-8")
    response_headers = [
        ("Content-Type", "application/json"),
        ("Content-Length", str(len(body))),
    ]
    if headers:
        response_headers.extend(headers)
    start_response(f"{status} {status_text(status)}", response_headers)
    return [body]


def bytes_response(
    start_response: Callable,
    status: int,
    body: bytes,
    content_type: str,
    headers: list[tuple[str, str]] | None = None,
) -> list[bytes]:
    response_headers = [
        ("Content-Type", content_type),
        ("Content-Length", str(len(body))),
    ]
    if headers:
        response_headers.extend(headers)
    start_response(f"{status} {status_text(status)}", response_headers)
    return [body]


def status_text(status: int) -> str:
    return {
        200: "OK",
        201: "Created",
        204: "No Content",
        400: "Bad Request",
        401: "Unauthorized",
        403: "Forbidden",
        404: "Not Found",
        409: "Conflict",
        405: "Method Not Allowed",
    }.get(status, "Error")


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = get_db()
    try:
        with conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    salt TEXT NOT NULL,
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
                    contact_email_erased_at TEXT,
                    due_date TEXT NOT NULL,
                    pdf_filename TEXT NOT NULL,
                    pdf_data BLOB NOT NULL,
                    state TEXT NOT NULL CHECK (state IN ('submitted', 'approved', 'rejected')),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS approvals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    invoice_id INTEGER NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
                    approver_id INTEGER NOT NULL REFERENCES users(id),
                    created_at TEXT NOT NULL,
                    UNIQUE(invoice_id, approver_id)
                );

                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    invoice_id INTEGER REFERENCES invoices(id),
                    actor_user_id INTEGER REFERENCES users(id),
                    action TEXT NOT NULL,
                    state_before TEXT,
                    state_after TEXT,
                    details TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
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
    finally:
        conn.close()


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    if salt is None:
        salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), 200_000)
    return digest.hex(), salt


def verify_password(password: str, stored_hash: str, salt: str) -> bool:
    digest, _ = hash_password(password, salt)
    return hmac.compare_digest(digest, stored_hash)


def parse_json(environ: dict[str, Any]) -> dict[str, Any]:
    try:
        length = int(environ.get("CONTENT_LENGTH") or "0")
    except ValueError:
        length = 0
    raw = environ["wsgi.input"].read(length) if length else b"{}"
    try:
        value = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise HttpError(400, "Invalid JSON body") from exc
    if not isinstance(value, dict):
        raise HttpError(400, "JSON body must be an object")
    return value


def require_str(data: dict[str, Any], name: str) -> str:
    value = data.get(name)
    if not isinstance(value, str) or not value.strip():
        raise HttpError(400, f"{name} is required")
    return value.strip()


def parse_amount_cents(value: Any) -> int:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise HttpError(400, "amount_euros must be a positive decimal") from exc
    if amount <= 0:
        raise HttpError(400, "amount_euros must be positive")
    if amount != amount.quantize(Decimal("0.01")):
        raise HttpError(400, "amount_euros must have at most two decimal places")
    cents = int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return cents


def format_amount(cents: int) -> str:
    return f"{Decimal(cents) / Decimal(100):.2f}"


def validate_due_date(value: str) -> str:
    try:
        dt.date.fromisoformat(value)
    except ValueError as exc:
        raise HttpError(400, "due_date must be YYYY-MM-DD") from exc
    return value


def validate_email(value: str, field: str = "email") -> str:
    email = value.strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise HttpError(400, f"{field} must be a valid email address")
    return email


def approval_requirement(amount_cents: int) -> int:
    return 2 if amount_cents > APPROVAL_THRESHOLD_CENTS else 1


def row_to_invoice(row: sqlite3.Row, approvals_count: int | None = None) -> dict[str, Any]:
    count = approvals_count if approvals_count is not None else 0
    return {
        "id": row["id"],
        "submitter_id": row["submitter_id"],
        "amount_euros": format_amount(row["amount_cents"]),
        "counterparty_name": row["counterparty_name"],
        "due_date": row["due_date"],
        "pdf_filename": row["pdf_filename"],
        "state": row["state"],
        "approvals_required": approval_requirement(row["amount_cents"]),
        "approvals_count": count,
        "contact_email_erased": row["counterparty_contact_email"] is None,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def record_audit(
    conn: sqlite3.Connection,
    invoice_id: int | None,
    actor_user_id: int | None,
    action: str,
    state_before: str | None,
    state_after: str | None,
    details: dict[str, Any] | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO audit_log
            (invoice_id, actor_user_id, action, state_before, state_after, details, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            invoice_id,
            actor_user_id,
            action,
            state_before,
            state_after,
            json.dumps(details or {}, sort_keys=True),
            utc_now(),
        ),
    )


def send_notification(submitter_email: str, invoice_id: int, state_before: str | None, state_after: str) -> None:
    Path(NOTIFICATION_LOG).parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "to": submitter_email,
        "invoice_id": invoice_id,
        "state_before": state_before,
        "state_after": state_after,
        "sent_at": utc_now(),
    }
    with open(NOTIFICATION_LOG, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True) + "\n")


def authenticated_user(conn: sqlite3.Connection, environ: dict[str, Any]) -> sqlite3.Row:
    header = environ.get("HTTP_AUTHORIZATION", "")
    if not header.startswith("Bearer "):
        raise HttpError(401, "Missing bearer token")
    token = header.removeprefix("Bearer ").strip()
    row = conn.execute(
        """
        SELECT users.*
        FROM sessions
        JOIN users ON users.id = sessions.user_id
        WHERE sessions.token = ?
        """,
        (token,),
    ).fetchone()
    if row is None:
        raise HttpError(401, "Invalid bearer token")
    return row


def require_role(user: sqlite3.Row, role: str) -> None:
    if user["role"] != role:
        raise HttpError(403, f"Only {role}s can perform this action")


def get_invoice(conn: sqlite3.Connection, invoice_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
    if row is None:
        raise HttpError(404, "Invoice not found")
    return row


def approvals_count(conn: sqlite3.Connection, invoice_id: int) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM approvals WHERE invoice_id = ?", (invoice_id,)).fetchone()[0])


def create_user(conn: sqlite3.Connection, data: dict[str, Any]) -> dict[str, Any]:
    email = validate_email(require_str(data, "email"))
    password = require_str(data, "password")
    role = require_str(data, "role")
    if role not in ROLES:
        raise HttpError(400, "role must be submitter or approver")
    password_hash, salt = hash_password(password)
    try:
        cur = conn.execute(
            """
            INSERT INTO users (email, password_hash, salt, role, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (email, password_hash, salt, role, utc_now()),
        )
    except sqlite3.IntegrityError as exc:
        raise HttpError(409, "User already exists") from exc
    return {"id": cur.lastrowid, "email": email, "role": role}


def login(conn: sqlite3.Connection, data: dict[str, Any]) -> dict[str, Any]:
    email = validate_email(require_str(data, "email"))
    password = require_str(data, "password")
    user = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    if user is None or not verify_password(password, user["password_hash"], user["salt"]):
        raise HttpError(401, "Invalid email or password")
    token = secrets.token_urlsafe(32)
    conn.execute(
        "INSERT INTO sessions (token, user_id, created_at) VALUES (?, ?, ?)",
        (token, user["id"], utc_now()),
    )
    return {"token": token, "user": {"id": user["id"], "email": user["email"], "role": user["role"]}}


def create_invoice(conn: sqlite3.Connection, user: sqlite3.Row, data: dict[str, Any]) -> dict[str, Any]:
    require_role(user, "submitter")
    amount_cents = parse_amount_cents(data.get("amount_euros"))
    counterparty_name = require_str(data, "counterparty_name")
    contact_email = validate_email(require_str(data, "counterparty_contact_email"), "counterparty_contact_email")
    due_date = validate_due_date(require_str(data, "due_date"))
    pdf_filename = require_str(data, "pdf_filename")
    if not pdf_filename.lower().endswith(".pdf"):
        raise HttpError(400, "pdf_filename must end with .pdf")
    encoded = require_str(data, "pdf_base64")
    try:
        pdf_data = base64.b64decode(encoded, validate=True)
    except ValueError as exc:
        raise HttpError(400, "pdf_base64 must be valid base64") from exc
    if not pdf_data.startswith(b"%PDF"):
        raise HttpError(400, "PDF attachment must start with %PDF")
    now = utc_now()
    cur = conn.execute(
        """
        INSERT INTO invoices (
            submitter_id, amount_cents, counterparty_name, counterparty_contact_email,
            due_date, pdf_filename, pdf_data, state, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, 'submitted', ?, ?)
        """,
        (
            user["id"],
            amount_cents,
            counterparty_name,
            contact_email,
            due_date,
            pdf_filename,
            pdf_data,
            now,
            now,
        ),
    )
    invoice_id = int(cur.lastrowid)
    record_audit(conn, invoice_id, user["id"], "STATE_CHANGED", None, "submitted", {"reason": "invoice_created"})
    send_notification(user["email"], invoice_id, None, "submitted")
    invoice = get_invoice(conn, invoice_id)
    return row_to_invoice(invoice, 0)


def list_pending_for_approval(conn: sqlite3.Connection, user: sqlite3.Row, query: dict[str, list[str]]) -> list[dict[str, Any]]:
    require_role(user, "approver")
    where = [
        "invoices.state = 'submitted'",
        "invoices.submitter_id != ?",
        "NOT EXISTS (SELECT 1 FROM approvals a WHERE a.invoice_id = invoices.id AND a.approver_id = ?)",
    ]
    params: list[Any] = [user["id"], user["id"]]
    if query.get("counterparty_name", [""])[0].strip():
        where.append("LOWER(invoices.counterparty_name) LIKE ?")
        params.append(f"%{query['counterparty_name'][0].strip().lower()}%")
    if query.get("min_amount_euros", [""])[0].strip():
        where.append("invoices.amount_cents >= ?")
        params.append(parse_amount_cents(query["min_amount_euros"][0]))
    if query.get("max_amount_euros", [""])[0].strip():
        where.append("invoices.amount_cents <= ?")
        params.append(parse_amount_cents(query["max_amount_euros"][0]))
    if query.get("amount_euros", [""])[0].strip():
        where.append("invoices.amount_cents = ?")
        params.append(parse_amount_cents(query["amount_euros"][0]))
    rows = conn.execute(
        f"""
        SELECT invoices.*, COUNT(approvals.id) AS approval_count
        FROM invoices
        LEFT JOIN approvals ON approvals.invoice_id = invoices.id
        WHERE {' AND '.join(where)}
        GROUP BY invoices.id
        ORDER BY invoices.due_date ASC, invoices.id ASC
        """,
        params,
    ).fetchall()
    return [row_to_invoice(row, row["approval_count"]) for row in rows]


def change_invoice_state(
    conn: sqlite3.Connection,
    user: sqlite3.Row,
    invoice_id: int,
    action: str,
) -> dict[str, Any]:
    require_role(user, "approver")
    invoice = get_invoice(conn, invoice_id)
    if invoice["submitter_id"] == user["id"]:
        raise HttpError(403, "Approvers cannot approve or reject invoices they submitted")
    if invoice["state"] != "submitted":
        raise HttpError(409, "Invoice is not pending approval")

    state_before = invoice["state"]
    state_after = state_before
    if action == "approve":
        try:
            conn.execute(
                "INSERT INTO approvals (invoice_id, approver_id, created_at) VALUES (?, ?, ?)",
                (invoice_id, user["id"], utc_now()),
            )
        except sqlite3.IntegrityError as exc:
            raise HttpError(409, "Approver has already approved this invoice") from exc
        current_count = approvals_count(conn, invoice_id)
        if current_count >= approval_requirement(invoice["amount_cents"]):
            state_after = "approved"
    elif action == "reject":
        state_after = "rejected"
    else:
        raise HttpError(404, "Not found")

    if state_after != state_before:
        now = utc_now()
        conn.execute(
            "UPDATE invoices SET state = ?, updated_at = ? WHERE id = ?",
            (state_after, now, invoice_id),
        )
        record_audit(conn, invoice_id, user["id"], "STATE_CHANGED", state_before, state_after, {"action": action})
        submitter = conn.execute("SELECT email FROM users WHERE id = ?", (invoice["submitter_id"],)).fetchone()
        send_notification(submitter["email"], invoice_id, state_before, state_after)
    else:
        record_audit(
            conn,
            invoice_id,
            user["id"],
            "APPROVAL_RECORDED",
            state_before,
            state_after,
            {"approvals_count": approvals_count(conn, invoice_id)},
        )

    updated = get_invoice(conn, invoice_id)
    return row_to_invoice(updated, approvals_count(conn, invoice_id))


def list_invoices(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT invoices.*, COUNT(approvals.id) AS approval_count
        FROM invoices
        LEFT JOIN approvals ON approvals.invoice_id = invoices.id
        GROUP BY invoices.id
        ORDER BY invoices.id ASC
        """
    ).fetchall()
    return [row_to_invoice(row, row["approval_count"]) for row in rows]


def read_contact_email(conn: sqlite3.Connection, user: sqlite3.Row, invoice_id: int) -> dict[str, Any]:
    invoice = get_invoice(conn, invoice_id)
    record_audit(
        conn,
        invoice_id,
        user["id"],
        "CONTACT_EMAIL_READ",
        invoice["state"],
        invoice["state"],
        {"erased": invoice["counterparty_contact_email"] is None},
    )
    return {"invoice_id": invoice_id, "counterparty_contact_email": invoice["counterparty_contact_email"]}


def erase_contact_email(conn: sqlite3.Connection, user: sqlite3.Row, invoice_id: int) -> dict[str, Any]:
    invoice = get_invoice(conn, invoice_id)
    if invoice["counterparty_contact_email"] is None:
        return {"invoice_id": invoice_id, "counterparty_contact_email": None, "erased": True}
    conn.execute(
        """
        UPDATE invoices
        SET counterparty_contact_email = NULL, contact_email_erased_at = ?, updated_at = ?
        WHERE id = ?
        """,
        (utc_now(), utc_now(), invoice_id),
    )
    record_audit(
        conn,
        invoice_id,
        user["id"],
        "CONTACT_EMAIL_ERASED",
        invoice["state"],
        invoice["state"],
        {},
    )
    return {"invoice_id": invoice_id, "counterparty_contact_email": None, "erased": True}


def list_audit_log(conn: sqlite3.Connection, invoice_id: int | None = None) -> list[dict[str, Any]]:
    params: tuple[Any, ...] = ()
    where = ""
    if invoice_id is not None:
        where = "WHERE invoice_id = ?"
        params = (invoice_id,)
    rows = conn.execute(
        f"""
        SELECT audit_log.*, users.email AS actor_email
        FROM audit_log
        LEFT JOIN users ON users.id = audit_log.actor_user_id
        {where}
        ORDER BY audit_log.id ASC
        """,
        params,
    ).fetchall()
    return [
        {
            "id": row["id"],
            "invoice_id": row["invoice_id"],
            "actor_user_id": row["actor_user_id"],
            "actor_email": row["actor_email"],
            "action": row["action"],
            "state_before": row["state_before"],
            "state_after": row["state_after"],
            "details": json.loads(row["details"]),
            "created_at": row["created_at"],
        }
        for row in rows
    ]


def application(environ: dict[str, Any], start_response: Callable) -> list[bytes]:
    init_db()
    method = environ["REQUEST_METHOD"].upper()
    path = environ.get("PATH_INFO", "/")
    query = parse_qs(environ.get("QUERY_STRING", ""))

    conn = get_db()
    try:
        with conn:
            if method == "GET" and path == "/health":
                return json_response(start_response, 200, {"ok": True})

            if method == "POST" and path == "/users":
                return json_response(start_response, 201, create_user(conn, parse_json(environ)))

            if method == "POST" and path == "/auth/login":
                return json_response(start_response, 200, login(conn, parse_json(environ)))

            user = authenticated_user(conn, environ)

            if method == "POST" and path == "/invoices":
                return json_response(start_response, 201, create_invoice(conn, user, parse_json(environ)))

            if method == "GET" and path == "/invoices":
                return json_response(start_response, 200, {"invoices": list_invoices(conn)})

            if method == "GET" and path == "/approvals/pending":
                return json_response(start_response, 200, {"invoices": list_pending_for_approval(conn, user, query)})

            if method == "GET" and path == "/audit-log":
                invoice_id = int(query["invoice_id"][0]) if query.get("invoice_id") else None
                return json_response(start_response, 200, {"audit_log": list_audit_log(conn, invoice_id)})

            match = re.fullmatch(r"/invoices/(\d+)", path)
            if match and method == "GET":
                invoice_id = int(match.group(1))
                invoice = get_invoice(conn, invoice_id)
                return json_response(start_response, 200, row_to_invoice(invoice, approvals_count(conn, invoice_id)))

            match = re.fullmatch(r"/invoices/(\d+)/pdf", path)
            if match and method == "GET":
                invoice = get_invoice(conn, int(match.group(1)))
                return bytes_response(
                    start_response,
                    200,
                    invoice["pdf_data"],
                    "application/pdf",
                    [("Content-Disposition", f'attachment; filename="{invoice["pdf_filename"]}"')],
                )

            match = re.fullmatch(r"/invoices/(\d+)/(approve|reject)", path)
            if match and method == "POST":
                return json_response(
                    start_response,
                    200,
                    change_invoice_state(conn, user, int(match.group(1)), match.group(2)),
                )

            match = re.fullmatch(r"/invoices/(\d+)/contact-email", path)
            if match and method == "GET":
                return json_response(start_response, 200, read_contact_email(conn, user, int(match.group(1))))
            if match and method == "DELETE":
                return json_response(start_response, 200, erase_contact_email(conn, user, int(match.group(1))))

            raise HttpError(404, "Not found")
    except HttpError as exc:
        return json_response(start_response, exc.status, {"error": exc.message})
    except ValueError:
        return json_response(start_response, 400, {"error": "Invalid numeric value"})
    finally:
        conn.close()


def main() -> int:
    init_db()
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    with make_server(host, port, application) as server:
        print(f"Invoice approval service running on http://{host}:{port}", flush=True)
        server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
