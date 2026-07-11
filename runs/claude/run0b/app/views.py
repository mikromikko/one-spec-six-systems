"""Server-rendered HTML interface, a thin layer over the service functions."""
import os
import uuid

from flask import (Blueprint, current_app, flash, g, redirect, render_template,
                   request, session, url_for)

from . import service
from .auth import authenticate
from .db import get_db

bp = Blueprint("views", __name__)


def _require_login():
    if g.user is None:
        return redirect(url_for("views.login"))
    return None


@bp.route("/")
def index():
    if g.user is None:
        return redirect(url_for("views.login"))
    if g.user["role"] == "approver":
        return redirect(url_for("views.pending"))
    return redirect(url_for("views.my_invoices"))


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        user = authenticate(request.form.get("email"), request.form.get("password"))
        if user is None:
            flash("Invalid email or password.")
        else:
            session.clear()
            session["user_id"] = user["id"]
            return redirect(url_for("views.index"))
    return render_template("login.html")


@bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("views.login"))


@bp.route("/invoices")
def my_invoices():
    guard = _require_login()
    if guard:
        return guard
    invoices = service.list_invoices_for(get_db(), g.user)
    return render_template("invoices.html", invoices=invoices)


@bp.route("/invoices/new", methods=["GET", "POST"])
def new_invoice():
    guard = _require_login()
    if guard:
        return guard
    if request.method == "POST":
        try:
            amount_cents = service.euros_to_cents(request.form.get("amount"))
            file = request.files.get("pdf")
            if file is None or file.filename == "":
                raise service.ServiceError("a PDF attachment is required")
            head = file.read(5)
            file.seek(0)
            if not head.startswith(b"%PDF"):
                raise service.ServiceError("attachment must be a PDF file")
            filename = f"{uuid.uuid4().hex}.pdf"
            path = os.path.join(current_app.config["UPLOAD_DIR"], filename)
            file.save(path)
            invoice_id = service.create_invoice(
                get_db(), g.user, amount_cents,
                request.form.get("counterparty_name"),
                request.form.get("counterparty_email"),
                request.form.get("due_date"), path,
            )
            return redirect(url_for("views.invoice_detail", invoice_id=invoice_id))
        except service.ServiceError as err:
            flash(str(err))
    return render_template("new_invoice.html")


@bp.route("/invoices/<int:invoice_id>")
def invoice_detail(invoice_id):
    guard = _require_login()
    if guard:
        return guard
    try:
        invoice = service.get_invoice_dict(get_db(), invoice_id)
        audit = service.get_audit_log(get_db(), invoice_id, g.user)
    except service.ServiceError as err:
        flash(str(err))
        return redirect(url_for("views.index"))
    revealed = request.args.get("show_email") == "1"
    email = None
    if revealed:
        email = service.read_counterparty_email(get_db(), g.user, invoice_id)
        audit = service.get_audit_log(get_db(), invoice_id, g.user)
    can_act = (
        g.user["role"] == "approver"
        and invoice["state"] == "submitted"
        and invoice["submitter_id"] != g.user["id"]
        and g.user["id"] not in invoice["approvers"]
    )
    return render_template(
        "invoice_detail.html", invoice=invoice, audit=audit,
        revealed=revealed, email=email, can_act=can_act,
    )


@bp.route("/invoices/<int:invoice_id>/approve", methods=["POST"])
def approve(invoice_id):
    guard = _require_login()
    if guard:
        return guard
    try:
        service.approve_invoice(get_db(), g.user, invoice_id)
    except service.ServiceError as err:
        flash(str(err))
    return redirect(url_for("views.invoice_detail", invoice_id=invoice_id))


@bp.route("/invoices/<int:invoice_id>/reject", methods=["POST"])
def reject(invoice_id):
    guard = _require_login()
    if guard:
        return guard
    try:
        service.reject_invoice(get_db(), g.user, invoice_id,
                               request.form.get("reason"))
    except service.ServiceError as err:
        flash(str(err))
    return redirect(url_for("views.invoice_detail", invoice_id=invoice_id))


@bp.route("/invoices/<int:invoice_id>/erase-email", methods=["POST"])
def erase_email(invoice_id):
    guard = _require_login()
    if guard:
        return guard
    try:
        service.erase_counterparty_email(get_db(), g.user, invoice_id)
        flash("Counterparty contact email erased.")
    except service.ServiceError as err:
        flash(str(err))
    return redirect(url_for("views.invoice_detail", invoice_id=invoice_id))


@bp.route("/pending")
def pending():
    guard = _require_login()
    if guard:
        return guard
    if g.user["role"] != "approver":
        return redirect(url_for("views.my_invoices"))
    counterparty = request.args.get("counterparty") or None
    min_amount = request.args.get("min_amount") or None
    max_amount = request.args.get("max_amount") or None
    invoices = service.list_pending_for_approver(
        get_db(), g.user, counterparty=counterparty,
        min_amount_cents=service.euros_to_cents(min_amount) if min_amount else None,
        max_amount_cents=service.euros_to_cents(max_amount) if max_amount else None,
    )
    return render_template("pending.html", invoices=invoices,
                           counterparty=counterparty or "",
                           min_amount=min_amount or "", max_amount=max_amount or "")
