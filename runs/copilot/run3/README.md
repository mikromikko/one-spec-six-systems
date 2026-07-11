# Invoice Approval Service

Internal local web service for Nordvik Components Oy supplier invoice approvals.

## Requirements

- Python 3.11 or newer
- No third-party Python packages are required

## Run locally

1. Start the service:

   ```sh
   python3 app.py
   ```

2. The API listens on `http://127.0.0.1:8000` by default.

Optional environment variables:

- `HOST` and `PORT` set the bind address.
- `INVOICE_DB` sets the SQLite database path. Default: `invoices.db`.
- `NOTIFICATION_LOG` sets the email notification stub log path. Default: `notifications.log`.

## Run tests

```sh
python3 -m unittest -v
```

## Minimal API flow

Create users:

```sh
curl -s -X POST http://127.0.0.1:8000/users \
  -H 'Content-Type: application/json' \
  -d '{"email":"submitter@nordvik.example","password":"secret123","role":"submitter"}'

curl -s -X POST http://127.0.0.1:8000/users \
  -H 'Content-Type: application/json' \
  -d '{"email":"approver1@nordvik.example","password":"secret123","role":"approver"}'

curl -s -X POST http://127.0.0.1:8000/users \
  -H 'Content-Type: application/json' \
  -d '{"email":"approver2@nordvik.example","password":"secret123","role":"approver"}'
```

Sign in and save tokens:

```sh
SUBMITTER_TOKEN=$(curl -s -X POST http://127.0.0.1:8000/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"submitter@nordvik.example","password":"secret123"}' | python3 -c 'import json,sys; print(json.load(sys.stdin)["token"])')

APPROVER1_TOKEN=$(curl -s -X POST http://127.0.0.1:8000/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"approver1@nordvik.example","password":"secret123"}' | python3 -c 'import json,sys; print(json.load(sys.stdin)["token"])')
```

Create an invoice. The PDF is provided as base64 in `pdf_base64`.

```sh
PDF_BASE64=$(printf '%s\n' '%PDF-1.4 invoice %%EOF' | base64)

curl -s -X POST http://127.0.0.1:8000/invoices \
  -H "Authorization: Bearer $SUBMITTER_TOKEN" \
  -H 'Content-Type: application/json' \
  -d "{\"amount_euros\":\"12000.00\",\"counterparty_name\":\"Acme GmbH\",\"counterparty_contact_email\":\"ap@acme.example\",\"due_date\":\"2026-08-15\",\"pdf_filename\":\"invoice.pdf\",\"pdf_base64\":\"$PDF_BASE64\"}"
```

List invoices pending an approver, with optional filters:

```sh
curl -s "http://127.0.0.1:8000/approvals/pending?counterparty_name=acme&min_amount_euros=10000" \
  -H "Authorization: Bearer $APPROVER1_TOKEN"
```

Approve or reject:

```sh
curl -s -X POST http://127.0.0.1:8000/invoices/1/approve \
  -H "Authorization: Bearer $APPROVER1_TOKEN"

curl -s -X POST http://127.0.0.1:8000/invoices/1/reject \
  -H "Authorization: Bearer $APPROVER1_TOKEN"
```

Invoices over 10,000 euros require approvals from two distinct approvers. Invoices at or under 10,000 euros require one approval.

Read or erase counterparty contact email:

```sh
curl -s http://127.0.0.1:8000/invoices/1/contact-email \
  -H "Authorization: Bearer $APPROVER1_TOKEN"

curl -s -X DELETE http://127.0.0.1:8000/invoices/1/contact-email \
  -H "Authorization: Bearer $SUBMITTER_TOKEN"
```

Every contact email read is written to the append-only audit log. Audit log rows can be listed but the API provides no update or delete route:

```sh
curl -s http://127.0.0.1:8000/audit-log?invoice_id=1 \
  -H "Authorization: Bearer $APPROVER1_TOKEN"
```

State-change email notifications are stubbed as JSON lines in `notifications.log`.
