# Invoice approval service

Internal invoice approval service for Nordvik Components Oy. It is a self-contained Python 3 application using SQLite and the standard library only.

## Run locally

1. Start the service:

   ```sh
   python3 app.py
   ```

2. Open `http://127.0.0.1:8000/health` or call the JSON API with `curl`.

The first startup creates `invoice_service.sqlite3`, `email_notifications.log`, and these demo users:

| Email | Password | Role |
| --- | --- | --- |
| `submitter@example.com` | `password123` | submitter |
| `approver1@example.com` | `password123` | approver |
| `approver2@example.com` | `password123` | approver |

To use different local paths:

```sh
INVOICE_SERVICE_DB=/tmp/invoices.sqlite3 INVOICE_SERVICE_EMAIL_LOG=/tmp/emails.jsonl PORT=8000 python3 app.py
```

## Run tests

```sh
python3 -m unittest -v
```

## API

All request and response bodies are JSON. Authenticated endpoints require `Authorization: Bearer <token>`.

### Sign in

```sh
curl -s http://127.0.0.1:8000/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"submitter@example.com","password":"password123"}'
```

### Create invoice

Submitters create invoices. `attachment_pdf_base64` must contain a PDF file encoded as base64.

```sh
PDF_BASE64="$(printf '%s\n' '%PDF-1.4 test invoice' | base64)"
curl -s http://127.0.0.1:8000/invoices \
  -H "Authorization: Bearer $SUBMITTER_TOKEN" \
  -H 'Content-Type: application/json' \
  -d "{
    \"amount_euros\":\"12000.00\",
    \"counterparty_name\":\"Nordic Steel Oy\",
    \"counterparty_contact_email\":\"ap@example-supplier.test\",
    \"due_date\":\"2026-08-15\",
    \"attachment_filename\":\"invoice.pdf\",
    \"attachment_pdf_base64\":\"$PDF_BASE64\"
  }"
```

### Pending approvals

Approvers can list invoices pending their own approval. The list excludes invoices they submitted and invoices they have already approved.

```sh
curl -s 'http://127.0.0.1:8000/invoices/pending?counterparty_name=steel&min_amount_euros=10000' \
  -H "Authorization: Bearer $APPROVER_TOKEN"
```

### Approve or reject

```sh
curl -s -X POST http://127.0.0.1:8000/invoices/1/approve \
  -H "Authorization: Bearer $APPROVER_TOKEN"

curl -s -X POST http://127.0.0.1:8000/invoices/1/reject \
  -H "Authorization: Bearer $APPROVER_TOKEN"
```

Invoices at or under EUR 10,000 become approved after one approval. Invoices over EUR 10,000 require approvals from two distinct approvers.

### Read invoice and erase personal data

Reading an invoice returns the counterparty contact email and records that personal-data read in the audit log. Erasing the contact email sets it to `null`.

```sh
curl -s http://127.0.0.1:8000/invoices/1 \
  -H "Authorization: Bearer $TOKEN"

curl -s -X POST http://127.0.0.1:8000/invoices/1/erase-contact \
  -H "Authorization: Bearer $TOKEN"
```

### Audit log

Audit log entries are append-only at the database level. The application exposes read-only access and has no endpoint for editing or deleting audit entries.

```sh
curl -s 'http://127.0.0.1:8000/audit-log?invoice_id=1' \
  -H "Authorization: Bearer $TOKEN"
```

Every invoice state change records who changed it, the state before and after, and when. Reads of `counterparty_contact_email` and erasure requests are also audited.

## Email notifications

Real SMTP is intentionally not used. Every invoice state change appends one JSON line to `email_notifications.log` for the invoice submitter.
