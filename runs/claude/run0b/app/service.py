"""Business logic for invoices, approvals, audit and personal-data handling.

All rules from the specification are enforced here so they hold regardless of
which interface (JSON API or HTML views) is used.
"""
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from .auth import now_iso
from .notifications import notify_state_change

# An amount strictly over EUR 10,000 requires two distinct approvers.
THRESHOLD_CENTS = 10_000 * 100

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# --- Errors ---------------------------------------------------------------

class ServiceError(Exception):
    status = 400


class NotFound(ServiceError):
    status = 404


class Forbidden(ServiceError):
    status = 403


class Conflict(ServiceError):
    status = 409


# --- Helpers --------------------------------------------------------------

def euros_to_cents(value):
    """Parse a euro amount (string or number) into integer cents."""
    try:
        d = Decimal(str(value).strip())
    except (InvalidOperation, AttributeError):
        raise ServiceError("invalid amount")
    if d <= 0:
        raise ServiceError("amount must be positive")
    if -d.as_tuple().exponent > 2:
        raise ServiceError("amount cannot have more than two decimal places")
    return int((d * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _get_invoice(db, invoice_id):
    return db.execute(
        "SELECT * FROM invoices WHERE id = ?", (invoice_id,)
    ).fetchone()


def _require_invoice(db, invoice_id):
    inv = _get_invoice(db, invoice_id)
    if inv is None:
        raise NotFound("invoice not found")
    return inv


def _audit(db, invoice_id, actor_id, action, state_before, state_after, detail):
    db.execute(
        "INSERT INTO audit_log "
        "(invoice_id, actor_id, action, state_before, state_after, detail, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (invoice_id, actor_id, action, state_before, state_after, detail, now_iso()),
    )


def _required_approvals(amount_cents):
    return 2 if amount_cents > THRESHOLD_CENTS else 1


# --- Invoice creation -----------------------------------------------------

def create_invoice(db, actor, amount_cents, counterparty_name,
                   counterparty_email, due_date, pdf_path):
    """Create a new invoice in the 'submitted' state."""
    if not isinstance(amount_cents, int) or amount_cents <= 0:
        raise ServiceError("amount must be a positive number")

    counterparty_name = (counterparty_name or "").strip()
    if not counterparty_name:
        raise ServiceError("counterparty name is required")

    counterparty_email = (counterparty_email or "").strip()
    if not _EMAIL_RE.match(counterparty_email):
        raise ServiceError("a valid counterparty contact email is required")

    due_date = (due_date or "").strip()
    try:
        datetime.strptime(due_date, "%Y-%m-%d")
    except ValueError:
        raise ServiceError("due date must be in YYYY-MM-DD format")

    if not pdf_path:
        raise ServiceError("a PDF attachment is required")

    ts = now_iso()
    cur = db.execute(
        "INSERT INTO invoices "
        "(amount_cents, counterparty_name, counterparty_email, due_date, "
        " pdf_path, submitter_id, state, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, 'submitted', ?, ?)",
        (amount_cents, counterparty_name, counterparty_email, due_date,
         pdf_path, actor["id"], ts, ts),
    )
    invoice_id = cur.lastrowid
    _audit(db, invoice_id, actor["id"], "create", None, "submitted",
           "invoice created")
    db.commit()

    notify_state_change(_require_invoice(db, invoice_id), "submitted",
                        "invoice received")
    return invoice_id


# --- State transitions ----------------------------------------------------

def _set_state(db, inv, actor_id, new_state, detail):
    db.execute(
        "UPDATE invoices SET state = ?, updated_at = ? WHERE id = ?",
        (new_state, now_iso(), inv["id"]),
    )
    _audit(db, inv["id"], actor_id, "state_change", inv["state"], new_state, detail)


def _guard_action(inv, actor):
    if actor["role"] != "approver":
        raise Forbidden("only approvers can approve or reject invoices")
    if inv["submitter_id"] == actor["id"]:
        raise Forbidden("you cannot approve or reject an invoice you submitted")
    if inv["state"] != "submitted":
        raise Conflict(f"invoice is already '{inv['state']}'")


def approve_invoice(db, actor, invoice_id):
    """Record an approval. The invoice becomes 'approved' once it has the
    required number of distinct approvers (two above EUR 10,000, else one)."""
    inv = _require_invoice(db, invoice_id)
    _guard_action(inv, actor)

    already = db.execute(
        "SELECT 1 FROM approvals WHERE invoice_id = ? AND approver_id = ?",
        (invoice_id, actor["id"]),
    ).fetchone()
    if already:
        raise Conflict("you have already approved this invoice")

    db.execute(
        "INSERT INTO approvals (invoice_id, approver_id, created_at) VALUES (?, ?, ?)",
        (invoice_id, actor["id"], now_iso()),
    )
    count = db.execute(
        "SELECT COUNT(*) AS c FROM approvals WHERE invoice_id = ?", (invoice_id,)
    ).fetchone()["c"]
    required = _required_approvals(inv["amount_cents"])

    if count >= required:
        _set_state(db, inv, actor["id"], "approved",
                   f"approved by {count} distinct approver(s)")
        db.commit()
        notify_state_change(_require_invoice(db, invoice_id), "approved")
        return "approved"

    # Not yet enough approvals: state stays 'submitted'. Record the partial
    # approval in the audit log (this is not a state change).
    _audit(db, invoice_id, actor["id"], "approval_recorded", "submitted",
           "submitted", f"approval {count}/{required} recorded")
    db.commit()
    return "pending"


def reject_invoice(db, actor, invoice_id, reason=None):
    inv = _require_invoice(db, invoice_id)
    _guard_action(inv, actor)
    _set_state(db, inv, actor["id"], "rejected", reason or "rejected")
    db.commit()
    notify_state_change(_require_invoice(db, invoice_id), "rejected", reason or "")
    return "rejected"


# --- Personal data (counterparty contact email) ---------------------------

def _can_see_invoice(inv, actor):
    return actor["role"] == "approver" or inv["submitter_id"] == actor["id"]


def read_counterparty_email(db, actor, invoice_id):
    """Return the counterparty contact email, recording the read access in the
    audit log (required because the email is personal data)."""
    inv = _require_invoice(db, invoice_id)
    if not _can_see_invoice(inv, actor):
        raise Forbidden("not allowed to view this invoice")

    if inv["counterparty_email_erased"] or not inv["counterparty_email"]:
        _audit(db, invoice_id, actor["id"], "read_counterparty_email", None, None,
               "read attempted; contact email has been erased")
        db.commit()
        return None

    _audit(db, invoice_id, actor["id"], "read_counterparty_email", None, None,
           "counterparty contact email accessed")
    db.commit()
    return inv["counterparty_email"]


def erase_counterparty_email(db, actor, invoice_id):
    """Erase the counterparty contact email on request (GDPR erasure)."""
    inv = _require_invoice(db, invoice_id)
    if not _can_see_invoice(inv, actor):
        raise Forbidden("not allowed to act on this invoice")

    db.execute(
        "UPDATE invoices "
        "SET counterparty_email = NULL, counterparty_email_erased = 1, updated_at = ? "
        "WHERE id = ?",
        (now_iso(), invoice_id),
    )
    _audit(db, invoice_id, actor["id"], "erase_counterparty_email", None, None,
           "counterparty contact email erased on request")
    db.commit()


# --- Queries --------------------------------------------------------------

def get_invoice_dict(db, invoice_id):
    inv = _require_invoice(db, invoice_id)
    approvers = [
        r["approver_id"]
        for r in db.execute(
            "SELECT approver_id FROM approvals WHERE invoice_id = ? ORDER BY created_at",
            (invoice_id,),
        ).fetchall()
    ]
    return {
        "id": inv["id"],
        "amount_cents": inv["amount_cents"],
        "amount_eur": f"{inv['amount_cents'] / 100:.2f}",
        "counterparty_name": inv["counterparty_name"],
        # The raw email is never included here; it is only available via the
        # audited read endpoint.
        "counterparty_email_present": bool(inv["counterparty_email"])
        and not inv["counterparty_email_erased"],
        "counterparty_email_erased": bool(inv["counterparty_email_erased"]),
        "due_date": inv["due_date"],
        "state": inv["state"],
        "submitter_id": inv["submitter_id"],
        "approvers": approvers,
        "approvals_count": len(approvers),
        "approvals_required": _required_approvals(inv["amount_cents"]),
        "created_at": inv["created_at"],
        "updated_at": inv["updated_at"],
    }


def list_invoices_for(db, actor):
    """Submitters see their own invoices; approvers see all."""
    if actor["role"] == "approver":
        rows = db.execute(
            "SELECT id FROM invoices ORDER BY created_at DESC"
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT id FROM invoices WHERE submitter_id = ? ORDER BY created_at DESC",
            (actor["id"],),
        ).fetchall()
    return [get_invoice_dict(db, r["id"]) for r in rows]


def list_pending_for_approver(db, actor, counterparty=None,
                              min_amount_cents=None, max_amount_cents=None):
    """Invoices awaiting this approver's action, filterable by counterparty
    name and amount range. Excludes their own invoices and any they already
    approved."""
    if actor["role"] != "approver":
        raise Forbidden("only approvers have a pending-approval list")

    query = (
        "SELECT id FROM invoices "
        "WHERE state = 'submitted' "
        "AND submitter_id != ? "
        "AND id NOT IN (SELECT invoice_id FROM approvals WHERE approver_id = ?)"
    )
    params = [actor["id"], actor["id"]]

    if counterparty:
        query += " AND lower(counterparty_name) LIKE ?"
        params.append(f"%{counterparty.lower()}%")
    if min_amount_cents is not None:
        query += " AND amount_cents >= ?"
        params.append(min_amount_cents)
    if max_amount_cents is not None:
        query += " AND amount_cents <= ?"
        params.append(max_amount_cents)

    query += " ORDER BY created_at DESC"
    rows = db.execute(query, params).fetchall()
    return [get_invoice_dict(db, r["id"]) for r in rows]


def get_audit_log(db, invoice_id, actor):
    inv = _require_invoice(db, invoice_id)
    if not _can_see_invoice(inv, actor):
        raise Forbidden("not allowed to view this invoice")
    rows = db.execute(
        "SELECT a.*, u.email AS actor_email "
        "FROM audit_log a LEFT JOIN users u ON u.id = a.actor_id "
        "WHERE a.invoice_id = ? ORDER BY a.id",
        (invoice_id,),
    ).fetchall()
    return [dict(r) for r in rows]
