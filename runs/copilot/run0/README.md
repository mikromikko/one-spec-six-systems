# Nordvik invoice approval service

Internal web service for approving incoming supplier invoices. It is a dependency-free Node.js HTTP API with file-backed local storage.

## Requirements

- Node.js 20 or newer
- npm

## Run locally

```sh
npm install
npm start
```

The service listens on `http://localhost:3000` and stores local runtime data under `data/`.

Seed users:

| Email | Password | Role |
| --- | --- | --- |
| `submitter@nordvik.example` | `submitter-password` | submitter |
| `approver1@nordvik.example` | `approver-password` | approver |
| `approver2@nordvik.example` | `approver-password` | approver |
| `approver3@nordvik.example` | `approver-password` | approver |

## Run tests

```sh
npm test
```

## API quick start

Log in:

```sh
curl -s http://localhost:3000/auth/login \
  -H 'content-type: application/json' \
  -d '{"email":"submitter@nordvik.example","password":"submitter-password"}'
```

Create an invoice as a submitter. `pdfBase64` must decode to bytes starting with `%PDF`.

```sh
TOKEN=... # token from login
PDF=$(printf '%s\n' '%PDF-1.4 test invoice' | base64)
curl -s http://localhost:3000/invoices \
  -H "authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' \
  -d "{\"amountEuros\":\"12000.00\",\"counterpartyName\":\"ACME Supplies\",\"counterpartyContactEmail\":\"contact@acme.example\",\"dueDate\":\"2026-08-01\",\"pdfFileName\":\"invoice.pdf\",\"pdfBase64\":\"$PDF\"}"
```

Approver endpoints:

- `GET /invoices/pending?counterpartyName=acme&minAmount=100&maxAmount=15000`
- `POST /invoices/:id/approve`
- `POST /invoices/:id/reject`

Other endpoints:

- `GET /invoices/:id` returns invoice details and records an audit entry for reading the counterparty contact email.
- `POST /invoices/:id/erase-counterparty-contact` erases the counterparty contact email.
- `GET /audit?invoiceId=:id` reads append-only audit entries.
- `GET /health` returns service health.

Email notifications are stubbed as JSON lines in `data/emails.log`. Attachments are stored in `data/attachments/`.
