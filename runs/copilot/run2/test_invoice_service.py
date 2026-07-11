from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from invoice_service import AuthorizationError, ConflictError, InvoiceService


PDF = b"%PDF-1.4\n% test invoice\n"


class InvoiceServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.service = InvoiceService(root / "test.sqlite3", root / "emails.jsonl")
        self.service.initialize()
        self.submitter = self.service.login("submitter@example.com", "password123")["user"]
        self.approver1 = self.service.login("approver1@example.com", "password123")["user"]
        self.approver2 = self.service.login("approver2@example.com", "password123")["user"]
        self.email_log = root / "emails.jsonl"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def create_invoice(self, amount: str = "1000.00", name: str = "Paper Supplies Oy") -> dict:
        return self.service.create_invoice(
            self.submitter,
            amount,
            name,
            "accounts.payable@example-supplier.test",
            "2026-08-15",
            "invoice.pdf",
            PDF,
        )

    def test_authentication_and_role_enforcement(self) -> None:
        self.assertEqual(self.submitter["role"], "submitter")
        self.assertEqual(self.approver1["role"], "approver")
        with self.assertRaises(AuthorizationError):
            self.service.create_invoice(
                self.approver1,
                "12.00",
                "Supplier",
                "supplier@example.test",
                "2026-08-15",
                "invoice.pdf",
                PDF,
            )

    def test_invoice_at_or_under_10000_is_approved_by_one_approver(self) -> None:
        invoice = self.create_invoice("10000.00")
        result = self.service.approve_invoice(self.approver1, invoice["id"])
        self.assertEqual(result["state"], "approved")
        self.assertEqual(result["approval_count"], 1)
        self.assertEqual(result["required_approvals"], 1)
        audit = self.service.audit_log(self.submitter, invoice["id"])
        self.assertEqual([(row["state_before"], row["state_after"]) for row in audit if row["action"] == "approve_invoice"], [("submitted", "approved")])

    def test_invoice_over_10000_requires_two_distinct_approvers(self) -> None:
        invoice = self.create_invoice("10000.01")
        first = self.service.approve_invoice(self.approver1, invoice["id"])
        self.assertEqual(first["state"], "submitted")
        self.assertEqual(first["approval_count"], 1)
        with self.assertRaises(ConflictError):
            self.service.approve_invoice(self.approver1, invoice["id"])
        second = self.service.approve_invoice(self.approver2, invoice["id"])
        self.assertEqual(second["state"], "approved")
        self.assertEqual(second["approval_count"], 2)
        self.assertEqual(second["required_approvals"], 2)

    def test_approver_cannot_decide_invoice_they_submitted(self) -> None:
        with closing(sqlite3.connect(self.service.db_path)) as db:
            db.execute(
                """
                INSERT INTO invoices (
                    submitter_id, amount_cents, counterparty_name, counterparty_contact_email,
                    due_date, attachment_filename, attachment_pdf, state, created_at
                ) VALUES (?, 1200, 'Self Supplier', 'self@example.test', '2026-08-15', 'invoice.pdf', ?, 'submitted', '2026-07-11T00:00:00+00:00')
                """,
                (self.approver1["id"], PDF),
            )
            invoice_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
            db.commit()
        with self.assertRaises(AuthorizationError):
            self.service.approve_invoice(self.approver1, invoice_id)
        with self.assertRaises(AuthorizationError):
            self.service.reject_invoice(self.approver1, invoice_id)

    def test_rejection_records_state_change_and_sends_notification(self) -> None:
        invoice = self.create_invoice()
        result = self.service.reject_invoice(self.approver1, invoice["id"])
        self.assertEqual(result["state"], "rejected")
        audit = self.service.audit_log(self.submitter, invoice["id"])
        self.assertIn(("reject_invoice", "submitted", "rejected"), [(row["action"], row["state_before"], row["state_after"]) for row in audit])
        notifications = [json.loads(line) for line in self.email_log.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(notifications[-1]["to"], "submitter@example.com")
        self.assertEqual(notifications[-1]["state_after"], "rejected")

    def test_audit_log_is_append_only(self) -> None:
        invoice = self.create_invoice()
        with closing(sqlite3.connect(self.service.db_path)) as db:
            with self.assertRaises(sqlite3.DatabaseError):
                db.execute("UPDATE audit_log SET action = 'tamper' WHERE invoice_id = ?", (invoice["id"],))
            with self.assertRaises(sqlite3.DatabaseError):
                db.execute("DELETE FROM audit_log WHERE invoice_id = ?", (invoice["id"],))

    def test_contact_email_reads_are_audited_and_contact_can_be_erased(self) -> None:
        invoice = self.create_invoice()
        detail = self.service.get_invoice(self.approver1, invoice["id"])
        self.assertEqual(detail["counterparty_contact_email"], "accounts.payable@example-supplier.test")
        audit = self.service.audit_log(self.submitter, invoice["id"])
        self.assertIn("read_counterparty_contact_email", [row["action"] for row in audit])

        erased = self.service.erase_counterparty_contact_email(self.submitter, invoice["id"])
        self.assertIsNone(erased["counterparty_contact_email"])
        detail_after_erase = self.service.get_invoice(self.approver1, invoice["id"])
        self.assertIsNone(detail_after_erase["counterparty_contact_email"])
        audit_after_erase = self.service.audit_log(self.submitter, invoice["id"])
        actions = [row["action"] for row in audit_after_erase]
        self.assertEqual(actions.count("read_counterparty_contact_email"), 1)
        self.assertIn("erase_counterparty_contact_email", actions)

    def test_pending_list_filters_by_counterparty_name_and_amount(self) -> None:
        low = self.create_invoice("99.99", "Bolts Ltd")
        high = self.create_invoice("12000.00", "Nordic Steel Oy")
        self.create_invoice("5000.00", "Packaging GmbH")
        filtered = self.service.pending_invoices(self.approver1, counterparty_name="steel", min_amount_euros="10000")
        self.assertEqual([invoice["id"] for invoice in filtered], [high["id"]])
        self.service.approve_invoice(self.approver1, high["id"])
        remaining_for_approver1 = self.service.pending_invoices(self.approver1)
        self.assertNotIn(high["id"], [invoice["id"] for invoice in remaining_for_approver1])
        remaining_for_approver2 = self.service.pending_invoices(self.approver2, min_amount_euros="10000")
        self.assertIn(high["id"], [invoice["id"] for invoice in remaining_for_approver2])
        self.assertIn(low["id"], [invoice["id"] for invoice in self.service.pending_invoices(self.approver1, max_amount_euros="100")])


if __name__ == "__main__":
    unittest.main()
