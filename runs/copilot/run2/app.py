from __future__ import annotations

import base64
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from invoice_service import AuthenticationError, InvoiceService, ServiceError


DB_PATH = os.environ.get("INVOICE_SERVICE_DB", "invoice_service.sqlite3")
NOTIFICATION_LOG = os.environ.get("INVOICE_SERVICE_EMAIL_LOG", "email_notifications.log")


class Handler(BaseHTTPRequestHandler):
    service = InvoiceService(DB_PATH, NOTIFICATION_LOG)

    def do_GET(self) -> None:
        self._handle("GET")

    def do_POST(self) -> None:
        self._handle("POST")

    def log_message(self, format: str, *args: object) -> None:
        return

    def _handle(self, method: str) -> None:
        try:
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"
            query = parse_qs(parsed.query)
            if method == "GET" and path == "/health":
                self._json(200, {"status": "ok"})
                return
            if method == "POST" and path == "/auth/login":
                body = self._body()
                self._json(200, self.service.login(body.get("email", ""), body.get("password", "")))
                return

            actor = self._actor()
            if method == "POST" and path == "/invoices":
                body = self._body()
                attachment_pdf = base64.b64decode(body.get("attachment_pdf_base64", ""), validate=True)
                invoice = self.service.create_invoice(
                    actor,
                    body.get("amount_euros"),
                    body.get("counterparty_name", ""),
                    body.get("counterparty_contact_email", ""),
                    body.get("due_date", ""),
                    body.get("attachment_filename", ""),
                    attachment_pdf,
                )
                self._json(201, invoice)
                return
            if method == "GET" and path == "/invoices/pending":
                self._json(
                    200,
                    self.service.pending_invoices(
                        actor,
                        counterparty_name=self._single(query, "counterparty_name"),
                        min_amount_euros=self._single(query, "min_amount_euros"),
                        max_amount_euros=self._single(query, "max_amount_euros"),
                    ),
                )
                return
            if method == "GET" and path == "/audit-log":
                invoice_id = self._single(query, "invoice_id")
                self._json(200, self.service.audit_log(actor, int(invoice_id) if invoice_id else None))
                return

            parts = path.strip("/").split("/")
            if len(parts) >= 2 and parts[0] == "invoices":
                invoice_id = int(parts[1])
                if method == "GET" and len(parts) == 2:
                    self._json(200, self.service.get_invoice(actor, invoice_id))
                    return
                if method == "POST" and len(parts) == 3 and parts[2] == "approve":
                    self._json(200, self.service.approve_invoice(actor, invoice_id))
                    return
                if method == "POST" and len(parts) == 3 and parts[2] == "reject":
                    self._json(200, self.service.reject_invoice(actor, invoice_id))
                    return
                if method == "POST" and len(parts) == 3 and parts[2] == "erase-contact":
                    self._json(200, self.service.erase_counterparty_contact_email(actor, invoice_id))
                    return
            self._json(404, {"error": "not found"})
        except AuthenticationError as error:
            self._json(error.status_code, {"error": error.message})
        except ServiceError as error:
            self._json(error.status_code, {"error": error.message})
        except (ValueError, TypeError, base64.binascii.Error):
            self._json(400, {"error": "invalid request"})

    def _actor(self) -> dict:
        header = self.headers.get("Authorization", "")
        prefix = "Bearer "
        if not header.startswith(prefix):
            raise AuthenticationError("missing bearer token")
        return self.service.authenticate(header[len(prefix) :])

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length == 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def _json(self, status: int, payload: object) -> None:
        encoded = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _single(self, query: dict[str, list[str]], key: str) -> str | None:
        values = query.get(key)
        return values[0] if values else None


def main() -> None:
    Handler.service.initialize(seed_defaults=True)
    port = int(os.environ.get("PORT", "8000"))
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Invoice approval service running on http://127.0.0.1:{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
