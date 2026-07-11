"""JSON REST API for the invoice approval service."""
import os
import uuid

from flask import Blueprint, current_app, g, jsonify, request, send_file

from . import service
from .auth import login_required
from .db import get_db

bp = Blueprint("api", __name__, url_prefix="/api")


def _parse_amount_filter(name):
    raw = request.args.get(name)
    if raw is None or raw == "":
        return None
    return service.euros_to_cents(raw)


def _save_pdf(file_storage):
    if file_storage is None or file_storage.filename == "":
        raise service.ServiceError("a PDF attachment is required")
    head = file_storage.read(5)
    file_storage.seek(0)
    if not head.startswith(b"%PDF"):
        raise service.ServiceError("attachment must be a PDF file")
    filename = f"{uuid.uuid4().hex}.pdf"
    path = os.path.join(current_app.config["UPLOAD_DIR"], filename)
    file_storage.save(path)
    return path


@bp.route("/invoices", methods=["POST"])
@login_required
def create_invoice():
    form = request.form
    amount_cents = service.euros_to_cents(form.get("amount"))
    pdf_path = _save_pdf(request.files.get("pdf") or request.files.get("file"))
    invoice_id = service.create_invoice(
        get_db(),
        g.user,
        amount_cents,
        form.get("counterparty_name"),
        form.get("counterparty_email"),
        form.get("due_date"),
        pdf_path,
    )
    return jsonify(service.get_invoice_dict(get_db(), invoice_id)), 201


@bp.route("/invoices", methods=["GET"])
@login_required
def list_invoices():
    return jsonify(service.list_invoices_for(get_db(), g.user))


@bp.route("/invoices/<int:invoice_id>", methods=["GET"])
@login_required
def get_invoice(invoice_id):
    return jsonify(service.get_invoice_dict(get_db(), invoice_id))


@bp.route("/invoices/<int:invoice_id>/pdf", methods=["GET"])
@login_required
def get_invoice_pdf(invoice_id):
    inv = service._require_invoice(get_db(), invoice_id)
    if not service._can_see_invoice(inv, g.user):
        raise service.Forbidden("not allowed to view this invoice")
    return send_file(inv["pdf_path"], mimetype="application/pdf")


@bp.route("/invoices/<int:invoice_id>/approve", methods=["POST"])
@login_required
def approve_invoice(invoice_id):
    result = service.approve_invoice(get_db(), g.user, invoice_id)
    return jsonify({"result": result,
                    "invoice": service.get_invoice_dict(get_db(), invoice_id)})


@bp.route("/invoices/<int:invoice_id>/reject", methods=["POST"])
@login_required
def reject_invoice(invoice_id):
    data = request.get_json(silent=True) or request.form
    reason = data.get("reason") if data else None
    result = service.reject_invoice(get_db(), g.user, invoice_id, reason)
    return jsonify({"result": result,
                    "invoice": service.get_invoice_dict(get_db(), invoice_id)})


@bp.route("/invoices/<int:invoice_id>/counterparty-email", methods=["GET"])
@login_required
def read_counterparty_email(invoice_id):
    email = service.read_counterparty_email(get_db(), g.user, invoice_id)
    return jsonify({"counterparty_email": email})


@bp.route("/invoices/<int:invoice_id>/counterparty-email", methods=["DELETE"])
@login_required
def erase_counterparty_email(invoice_id):
    service.erase_counterparty_email(get_db(), g.user, invoice_id)
    return jsonify({"ok": True, "erased": True})


@bp.route("/invoices/<int:invoice_id>/audit", methods=["GET"])
@login_required
def invoice_audit(invoice_id):
    return jsonify(service.get_audit_log(get_db(), invoice_id, g.user))


@bp.route("/pending", methods=["GET"])
@login_required
def pending():
    return jsonify(
        service.list_pending_for_approver(
            get_db(),
            g.user,
            counterparty=request.args.get("counterparty"),
            min_amount_cents=_parse_amount_filter("min_amount"),
            max_amount_cents=_parse_amount_filter("max_amount"),
        )
    )
