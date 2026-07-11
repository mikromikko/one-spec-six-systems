import base64
import io
import json
import os
import sqlite3
import tempfile
import unittest

import app


class InvoiceServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        app.DB_PATH = os.path.join(self.tmp.name, "test.db")
        app.NOTIFICATION_LOG = os.path.join(self.tmp.name, "notifications.log")
        app.init_db()
        self.submitter = self.create_user("submitter@nordvik.example", "submitter")
        self.approver1 = self.create_user("approver1@nordvik.example", "approver")
        self.approver2 = self.create_user("approver2@nordvik.example", "approver")
        self.submitter_token = self.login("submitter@nordvik.example")
        self.approver1_token = self.login("approver1@nordvik.example")
        self.approver2_token = self.login("approver2@nordvik.example")

    def tearDown(self):
        self.tmp.cleanup()

    def request(self, method, path, body=None, token=None):
        payload = json.dumps(body or {}).encode("utf-8")
        status_headers = {}

        def start_response(status, headers):
            status_headers["status"] = int(status.split(" ", 1)[0])
            status_headers["headers"] = dict(headers)

        environ = {
            "REQUEST_METHOD": method,
            "PATH_INFO": path.split("?", 1)[0],
            "QUERY_STRING": path.split("?", 1)[1] if "?" in path else "",
            "CONTENT_LENGTH": str(len(payload)),
            "CONTENT_TYPE": "application/json",
            "wsgi.input": io.BytesIO(payload),
        }
        if token:
            environ["HTTP_AUTHORIZATION"] = f"Bearer {token}"
        response = b"".join(app.application(environ, start_response))
        content_type = status_headers["headers"].get("Content-Type", "")
        if content_type.startswith("application/json"):
            parsed = json.loads(response.decode("utf-8"))
        else:
            parsed = response
        return status_headers["status"], parsed

    def create_user(self, email, role):
        status, data = self.request("POST", "/users", {"email": email, "password": "secret123", "role": role})
        self.assertEqual(status, 201, data)
        return data

    def login(self, email):
        status, data = self.request("POST", "/auth/login", {"email": email, "password": "secret123"})
        self.assertEqual(status, 200, data)
        return data["token"]

    def invoice_payload(self, amount="9999.99", counterparty="Acme GmbH"):
        return {
            "amount_euros": amount,
            "counterparty_name": counterparty,
            "counterparty_contact_email": "accounts-payable@acme.example",
            "due_date": "2026-08-15",
            "pdf_filename": "invoice.pdf",
            "pdf_base64": base64.b64encode(b"%PDF-1.4\ninvoice\n%%EOF").decode("ascii"),
        }

    def create_invoice(self, amount="9999.99", counterparty="Acme GmbH"):
        status, data = self.request(
            "POST",
            "/invoices",
            self.invoice_payload(amount, counterparty),
            self.submitter_token,
        )
        self.assertEqual(status, 201, data)
        return data

    def audit_actions(self, invoice_id):
        status, data = self.request("GET", f"/audit-log?invoice_id={invoice_id}", token=self.approver1_token)
        self.assertEqual(status, 200, data)
        return [entry["action"] for entry in data["audit_log"]]

    def notifications(self):
        with open(app.NOTIFICATION_LOG, encoding="utf-8") as handle:
            return [json.loads(line) for line in handle]

    def test_submitter_creates_invoice_without_exposing_contact_email(self):
        invoice = self.create_invoice()

        self.assertEqual(invoice["state"], "submitted")
        self.assertEqual(invoice["approvals_required"], 1)
        self.assertNotIn("counterparty_contact_email", invoice)
        self.assertEqual(self.audit_actions(invoice["id"]), ["STATE_CHANGED"])
        self.assertEqual(self.notifications()[0]["state_after"], "submitted")

    def test_approver_pending_list_filters_by_counterparty_and_amount(self):
        invoice = self.create_invoice("100.00", "Baltic Steel")
        self.create_invoice("2500.00", "Other Supplier")

        status, data = self.request(
            "GET",
            "/approvals/pending?counterparty_name=baltic&min_amount_euros=50&max_amount_euros=150",
            token=self.approver1_token,
        )

        self.assertEqual(status, 200, data)
        self.assertEqual([item["id"] for item in data["invoices"]], [invoice["id"]])

    def test_invoice_at_threshold_is_approved_by_one_approver(self):
        invoice = self.create_invoice("10000.00")

        status, approved = self.request("POST", f"/invoices/{invoice['id']}/approve", token=self.approver1_token)

        self.assertEqual(status, 200, approved)
        self.assertEqual(approved["state"], "approved")
        self.assertEqual(approved["approvals_count"], 1)
        self.assertEqual(self.audit_actions(invoice["id"]), ["STATE_CHANGED", "STATE_CHANGED"])
        self.assertEqual(self.notifications()[-1]["state_after"], "approved")

    def test_invoice_over_threshold_requires_two_distinct_approvers(self):
        invoice = self.create_invoice("10000.01")

        status, first = self.request("POST", f"/invoices/{invoice['id']}/approve", token=self.approver1_token)
        self.assertEqual(status, 200, first)
        self.assertEqual(first["state"], "submitted")
        self.assertEqual(first["approvals_count"], 1)
        self.assertEqual(self.audit_actions(invoice["id"]), ["STATE_CHANGED", "APPROVAL_RECORDED"])

        status, pending = self.request("GET", "/approvals/pending", token=self.approver1_token)
        self.assertEqual(status, 200, pending)
        self.assertNotIn(invoice["id"], [item["id"] for item in pending["invoices"]])

        status, second = self.request("POST", f"/invoices/{invoice['id']}/approve", token=self.approver2_token)
        self.assertEqual(status, 200, second)
        self.assertEqual(second["state"], "approved")
        self.assertEqual(second["approvals_count"], 2)
        self.assertEqual(self.audit_actions(invoice["id"])[-1], "STATE_CHANGED")

    def test_approver_cannot_approve_or_reject_invoice_they_submitted(self):
        conn = sqlite3.connect(app.DB_PATH)
        try:
            with conn:
                conn.execute(
                    """
                    INSERT INTO invoices (
                        submitter_id, amount_cents, counterparty_name, counterparty_contact_email,
                        due_date, pdf_filename, pdf_data, state, created_at, updated_at
                    )
                    VALUES (?, 1000, 'Self Supplier', 'self@supplier.example',
                            '2026-08-15', 'self.pdf', ?, 'submitted', ?, ?)
                    """,
                    (self.approver1["id"], b"%PDF-1.4", app.utc_now(), app.utc_now()),
                )
                invoice_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        finally:
            conn.close()

        status, data = self.request("POST", f"/invoices/{invoice_id}/approve", token=self.approver1_token)
        self.assertEqual(status, 403, data)

        status, data = self.request("POST", f"/invoices/{invoice_id}/reject", token=self.approver1_token)
        self.assertEqual(status, 403, data)

    def test_reject_changes_state_and_notifies_submitter(self):
        invoice = self.create_invoice("500.00")

        status, rejected = self.request("POST", f"/invoices/{invoice['id']}/reject", token=self.approver1_token)

        self.assertEqual(status, 200, rejected)
        self.assertEqual(rejected["state"], "rejected")
        self.assertEqual(self.notifications()[-1]["state_after"], "rejected")

    def test_contact_email_read_is_audited_and_erasure_removes_value(self):
        invoice = self.create_invoice()

        status, data = self.request("GET", f"/invoices/{invoice['id']}/contact-email", token=self.approver1_token)
        self.assertEqual(status, 200, data)
        self.assertEqual(data["counterparty_contact_email"], "accounts-payable@acme.example")

        status, erased = self.request("DELETE", f"/invoices/{invoice['id']}/contact-email", token=self.submitter_token)
        self.assertEqual(status, 200, erased)
        self.assertTrue(erased["erased"])

        status, data = self.request("GET", f"/invoices/{invoice['id']}/contact-email", token=self.approver1_token)
        self.assertEqual(status, 200, data)
        self.assertIsNone(data["counterparty_contact_email"])
        self.assertEqual(
            self.audit_actions(invoice["id"]),
            ["STATE_CHANGED", "CONTACT_EMAIL_READ", "CONTACT_EMAIL_ERASED", "CONTACT_EMAIL_READ"],
        )

    def test_audit_log_is_append_only_at_database_level(self):
        invoice = self.create_invoice()
        conn = sqlite3.connect(app.DB_PATH)
        try:
            with self.assertRaises(sqlite3.DatabaseError):
                conn.execute("UPDATE audit_log SET action = 'TAMPERED' WHERE invoice_id = ?", (invoice["id"],))
            with self.assertRaises(sqlite3.DatabaseError):
                conn.execute("DELETE FROM audit_log WHERE invoice_id = ?", (invoice["id"],))
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
