# Nordvik Components — Invoice Approval Service

An internal web service for approving incoming supplier invoices.

Submitters register invoices with a PDF attachment; approvers approve or reject
them. Invoices over €10,000 require two distinct approvers. Every state change
and every read of the counterparty's personal contact email is written to an
append-only audit log, and the submitter is notified by email on each change.

## Tech stack

- **Node.js** (≥ 22.5, tested on Node 26) with **Express** for the HTTP API.
- **SQLite** via Node's built-in `node:sqlite` module — no native build step.
- **Multer** for the PDF upload; everything else uses the Node standard library
  (`node:crypto` for password hashing and signed auth tokens).
- A tiny static HTML page (`public/index.html`) provides a click-through UI.

The only third-party runtime dependencies are `express` and `multer`.

## Prerequisites

- Node.js **22.5 or newer** (uses the stable `node:sqlite` module). Check with
  `node --version`.

## Run the system locally

```bash
# 1. Install dependencies
npm install

# 2. Create three demo users (idempotent)
npm run seed

# 3. Start the service
npm start
```

The service listens on <http://localhost:3000>. Open that URL in a browser for
the demo UI, or use the JSON API directly (see below).

Seeded users (email / password / role):

| Email                       | Password        | Role      |
| --------------------------- | --------------- | --------- |
| `submitter@nordvik.example` | `submitter-pass`| submitter |
| `approver1@nordvik.example` | `approver-pass` | approver  |
| `approver2@nordvik.example` | `approver-pass` | approver  |

Runtime data (SQLite database, uploaded PDFs, the email outbox) is written under
`./data/`. Email notifications are appended as JSON lines to `./data/outbox.log`
(the required email stub — no real SMTP).

Configuration via environment variables (all optional): `PORT`, `DB_PATH`,
`UPLOAD_DIR`, `OUTBOX_PATH`, `JWT_SECRET`.

## Run the tests

```bash
npm test
```

This runs the automated test suite with Node's built-in test runner. Each test
starts an isolated server on an ephemeral port backed by an in-memory database,
so the tests need no seeding and leave no artifacts. The suite covers every
requirement (sign-in and roles, submission, the approval state machine, the
€10,000 dual-approval rule, the self-approval prohibition, the append-only audit
log, personal-data read logging and erasure, filtering, and email notifications).

## API overview

All endpoints are under `/api`. Authenticate by sending
`Authorization: Bearer <token>` where `<token>` comes from `POST /api/auth/login`.

| Method & path                          | Role       | Purpose                                                  |
| -------------------------------------- | ---------- | -------------------------------------------------------- |
| `POST /api/auth/register`              | —          | Create a user (`email`, `password`, `role`).             |
| `POST /api/auth/login`                 | —          | Sign in; returns a bearer token.                         |
| `GET  /api/me`                         | any        | Current user.                                            |
| `POST /api/invoices`                   | submitter  | Create an invoice (multipart: `amountEuros`, `counterpartyName`, `counterpartyEmail`, `dueDate`, `pdf`). |
| `GET  /api/invoices`                   | any        | Approvers: invoices pending their approval, filterable by `counterpartyName`, `minAmount`, `maxAmount`. Submitters: their own invoices. |
| `GET  /api/invoices/:id`               | any        | Invoice details (never includes the contact email).      |
| `GET  /api/invoices/:id/pdf`           | any        | Download the PDF attachment.                             |
| `POST /api/invoices/:id/approve`       | approver   | Record an approval (may finalize the invoice).           |
| `POST /api/invoices/:id/reject`        | approver   | Reject the invoice.                                      |
| `GET  /api/invoices/:id/contact-email` | any        | Read the counterparty email — **recorded in the audit log**. |
| `DELETE /api/invoices/:id/contact-email` | any      | Erase the counterparty email (right to erasure).         |
| `GET  /api/invoices/:id/audit`         | any        | Audit trail for one invoice.                             |
| `GET  /api/audit`                      | approver   | Recent audit entries across all invoices.                |

## How each requirement is met

1. **Sign-in & roles** — email/password login (`src/auth.js`, scrypt hashing);
   each user row has exactly one `role` (`submitter` or `approver`), enforced by
   a `CHECK` constraint.
2. **Invoice creation** — `POST /api/invoices` accepts amount, counterparty
   name, contact email, due date and a required PDF (validated as
   `application/pdf`); submitter-only.
3. **State machine** — invoices are `submitted` → `approved` / `rejected`; only
   approvers can call approve/reject; terminal states reject further changes.
4. **No self-approval** — approve/reject return `403` when the actor is the
   invoice's submitter.
5. **Dual approval over €10,000** — the threshold is €10,000 (`> 10000` requires
   two *distinct* approvers; a `UNIQUE(invoice_id, approver_id)` constraint
   prevents one approver counting twice). At or under €10,000, one approval
   finalizes it.
6. **Append-only audit log** — every state change inserts an `audit_log` row
   (actor, action, state before/after, timestamp). No endpoint edits or deletes
   audit rows, and `BEFORE UPDATE`/`BEFORE DELETE` triggers make the table
   append-only at the storage layer.
7. **Personal data** — the contact email is never returned by ordinary invoice
   endpoints. It is readable only via `GET .../contact-email`, which writes a
   `contact_email_read` audit entry on **every** read. `DELETE .../contact-email`
   erases it (and audits the erasure).
8. **Pending list & filters** — approvers' `GET /api/invoices` returns invoices
   awaiting their approval, filterable by counterparty name (substring) and by
   amount range.
9. **Email notifications** — every state change calls the mailer stub
   (`src/email.js`), which appends the message to `data/outbox.log` and logs it.
10. **Tests & README** — `npm test` runs the suite in `tests/`; this file
    documents the exact steps.

## Project layout

```
src/
  app.js      Express app factory: routes and business rules
  server.js   Process entry point (npm start)
  db.js       SQLite schema, indexes, append-only triggers
  auth.js     scrypt password hashing + signed bearer tokens
  audit.js    Append-only audit-log writer
  email.js    Email notification stub (writes to outbox file)
  seed.js     Creates the demo users (npm run seed)
public/
  index.html  Minimal demo UI
tests/        Automated test suite (node --test)
```
