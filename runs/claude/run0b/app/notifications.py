"""Email notification stub.

No real SMTP is used. Every message is appended to the file configured as
EMAIL_LOG, which acts as the outbox for local development and tests.
"""
import os

from flask import current_app

from .auth import now_iso
from .db import get_db


def send_email(to, subject, body):
    line = (
        f"[{now_iso()}] TO: {to}\n"
        f"SUBJECT: {subject}\n"
        f"{body}\n"
        f"{'-' * 60}\n"
    )
    path = current_app.config["EMAIL_LOG"]
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(line)


def _submitter_email(invoice_row):
    user = get_db().execute(
        "SELECT email FROM users WHERE id = ?", (invoice_row["submitter_id"],)
    ).fetchone()
    return user["email"] if user else "unknown@localhost"


def notify_state_change(invoice_row, new_state, detail=""):
    """Notify the submitter that their invoice changed state."""
    to = _submitter_email(invoice_row)
    amount = f"{invoice_row['amount_cents'] / 100:.2f}"
    body = (
        f"Invoice #{invoice_row['id']} (counterparty: "
        f"{invoice_row['counterparty_name']}, amount: EUR {amount}) "
        f"is now '{new_state}'."
    )
    if detail:
        body += f"\nDetails: {detail}"
    send_email(to, f"Invoice #{invoice_row['id']} {new_state}", body)
