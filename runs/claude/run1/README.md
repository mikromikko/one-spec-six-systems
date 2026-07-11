# Invoice Approval Service — Nordvik Components Oy

An internal web service (JSON/REST API) for approving incoming supplier invoices.

## Tech stack

- **Node.js** (>= 22.5) with **Express** — HTTP API
- **`node:sqlite`** — the SQLite database built into Node, so there is **no native
  build step and no external database server** to install
- **multer** — PDF upload handling
- Passwords hashed with **scrypt** (Node's built-in `crypto`)

Everything runs locally from this directory. State lives in `./data/`
(SQLite database, uploaded PDFs, and the email log).

## Requirements

- Node.js **22.5 or newer** (uses the built-in `node:sqlite` module).
  Check with `node --version`.

## Setup

```bash
npm install
npm run seed      # creates four demo users (see below)
npm start         # starts the service on http://localhost:3000
```

Environment variables (all optional):

| Variable     | Default              | Purpose                          |
|--------------|----------------------|----------------------------------|
| `PORT`       | `3000`               | HTTP port                        |
| `DB_PATH`    | `./data/invoices.db` | SQLite database file             |
| `UPLOAD_DIR` | `./data/uploads`     | Where PDF attachments are stored |
| `EMAIL_LOG`  | `./data/emails.log`  | Where notification emails are written |

### Demo users (created by `npm run seed`)

| Role      | Email                          | Password      |
|-----------|--------------------------------|---------------|
| submitter | `submitter@nordvik.example`    | `password123` |
| submitter | `submitter2@nordvik.example`   | `password123` |
| approver  | `approver1@nordvik.example`    | `password123` |
| approver  | `approver2@nordvik.example`    | `password123` |

## Running the tests

```bash
npm test
```

This runs the automated test suite (Node's built-in test runner + supertest).
The tests use isolated temporary databases and do not touch your `./data/`
directory. They cover each requirement: authentication and roles, invoice
creation with a PDF, the approval state machine, the two-approver rule above
€10,000, the self-approval prohibition, the append-only audit log, personal-data
read logging and erasure, the filterable pending list, and email notifications.

## How it maps to the requirements

1. **Auth & roles** — `POST /login` returns a bearer token. Each user has exactly
   one role (`submitter` or `approver`), enforced by a DB `CHECK` constraint.
2. **Invoice creation** — `POST /invoices` (submitter only) takes `amount`,
   `counterpartyName`, `counterpartyEmail`, `dueDate` and a `pdf` file. Amounts are
   stored in integer cents; only PDFs are accepted.
3. **State machine** — invoices are `submitted → approved | rejected`. Only
   approvers can call approve/reject.
4. **No self-approval** — an approver cannot approve or reject an invoice whose
   submitter is themselves (returns `403`).
5. **Two-approver threshold** — amounts **over €10,000** require two *distinct*
   approvers before becoming `approved`; at or under €10,000, one approval suffices.
   A given approver can only approve an invoice once (unique constraint).
6. **Append-only audit log** — every state change records who, the action, the
   state before and after, and when. The `audit_log` table has `BEFORE UPDATE`/
   `BEFORE DELETE` triggers that abort any modification, and the application
   exposes no edit/delete endpoint. Read it via `GET /invoices/:id/audit`.
7. **Personal data (counterparty email)** — reading it via
   `GET /invoices/:id/counterparty-email` writes a `read_counterparty_email` audit
   entry on every access. `DELETE /invoices/:id/counterparty-email` erases it
   (irreversibly nulled and flagged), which is also audited.
8. **Pending list** — `GET /invoices/pending` (approver only) lists invoices
   awaiting *that* approver's decision (excludes their own and ones they already
   approved), filterable with `?counterparty=`, `?minAmount=`, `?maxAmount=`.
9. **Email notifications** — on every state change the submitter is notified. This
   is a stub that appends the message to `EMAIL_LOG` (no real SMTP).
10. **Tests & README** — this file plus `npm test`.

## API reference

All endpoints except `POST /login` and `GET /health` require an
`Authorization: Bearer <token>` header.

| Method & path                              | Role       | Purpose                                    |
|--------------------------------------------|------------|--------------------------------------------|
| `POST /login`                              | any        | Log in, returns `{ token, role, email }`   |
| `POST /logout`                             | any        | Invalidate the current token               |
| `GET /me`                                  | any        | Current user                               |
| `POST /invoices`                           | submitter  | Create invoice (multipart, `pdf` field)    |
| `GET /invoices`                            | any        | List invoices (submitters see their own)   |
| `GET /invoices/:id`                        | any        | Invoice details                            |
| `GET /invoices/pending`                    | approver   | Invoices pending this approver (filterable)|
| `POST /invoices/:id/approve`               | approver   | Approve                                    |
| `POST /invoices/:id/reject`                | approver   | Reject (optional `{ "reason": "..." }`)    |
| `GET /invoices/:id/counterparty-email`     | any        | Read counterparty email (audited)          |
| `DELETE /invoices/:id/counterparty-email`  | any        | Erase counterparty email                   |
| `GET /invoices/:id/audit`                  | any        | Audit log for the invoice                  |
| `GET /health`                              | —          | Liveness check                             |

### Example flow with curl

```bash
# Log in as a submitter
TOKEN=$(curl -s -X POST localhost:3000/login \
  -H 'content-type: application/json' \
  -d '{"email":"submitter@nordvik.example","password":"password123"}' \
  | node -pe 'JSON.parse(require("fs").readFileSync(0)).token')

# Create an invoice with a PDF attachment
curl -s -X POST localhost:3000/invoices \
  -H "authorization: Bearer $TOKEN" \
  -F amount=12000 \
  -F counterpartyName="Acme Supplies Oy" \
  -F counterpartyEmail=contact@acme.example \
  -F dueDate=2026-12-31 \
  -F pdf=@/path/to/invoice.pdf

# Approve it as two different approvers (needed because 12000 > 10000)
A1=$(curl -s -X POST localhost:3000/login -H 'content-type: application/json' \
  -d '{"email":"approver1@nordvik.example","password":"password123"}' \
  | node -pe 'JSON.parse(require("fs").readFileSync(0)).token')
curl -s -X POST localhost:3000/invoices/1/approve -H "authorization: Bearer $A1"
# ... then approver2 approves to finalize.
```

## Notes & design decisions

- **Money** is stored as integer cents to avoid floating-point rounding. The
  €10,000 threshold is strict: `> €10,000` needs two approvers.
- **A partial approval** (first of two) is itself recorded in the audit log as
  `approve_partial` but does not change state, so no notification email is sent —
  email fires only on an actual state transition, per requirement 9.
- **Sessions** are opaque bearer tokens stored in the `sessions` table.
- Running against Node's `node:sqlite` prints a one-line experimental-feature
  warning on startup; this is expected and harmless.
