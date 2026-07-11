# Invoice Approval Service — Nordvik Components Oy

An internal web service for approving incoming supplier invoices.

Submitters register invoices (amount, counterparty, contact email, due date, PDF).
Approvers approve or reject them. Invoices over €10,000 require two distinct
approvers; €10,000 and under require one. Every state change is written to an
append-only audit log, the counterparty contact email is treated as erasable
personal data whose reads are audited, and every state change triggers an email
notification (written to a file, no real SMTP).

## Tech stack

- **Node.js** (>= 22) with **Express** — HTTP API + minimal web UI
- **SQLite** via the built-in `node:sqlite` module — no native build, no server
- **scrypt** (`node:crypto`) for password hashing
- **multer** for PDF uploads
- Tests use the built-in **`node:test`** runner + **supertest**

No external database or services are needed — everything runs from this directory.

## Requirements

- Node.js **22 or newer** (uses the built-in `node:sqlite`). Check with `node --version`.

## Setup

```bash
npm install
```

## Run the system

```bash
npm start
```

This starts the service on <http://localhost:3000> and, on first run, seeds a set
of demo users (see below). Open the URL in a browser to use the web UI, or call
the JSON API directly.

Environment variables (all optional):

| Variable         | Default          | Purpose                              |
| ---------------- | ---------------- | ------------------------------------ |
| `PORT`           | `3000`           | HTTP port                            |
| `DB_PATH`        | `./data/app.db`  | SQLite database file                 |
| `UPLOAD_DIR`     | `./uploads`      | Where invoice PDFs are stored        |
| `EMAIL_DIR`      | `./emails`       | Where notification emails are written|
| `SESSION_SECRET` | `dev-secret-...` | Session cookie secret (set in prod)  |

### Demo accounts

Seeded automatically on first run. Password for all: `password123`.

| Role      | Email                        |
| --------- | ---------------------------- |
| submitter | `submitter1@nordvik.example` |
| submitter | `submitter2@nordvik.example` |
| approver  | `approver1@nordvik.example`  |
| approver  | `approver2@nordvik.example`  |
| approver  | `approver3@nordvik.example`  |

To (re)seed manually: `npm run seed`.

## Run the tests

```bash
npm test
```

The suite runs against an in-memory database and a temporary working directory,
so it needs no setup and leaves no artifacts. Each test is named after the
specification requirement it covers.

## How the requirements are met

1. **Auth & roles** — email/password login (`/api/auth/login`), scrypt-hashed
   passwords, one role per user (`submitter` or `approver`), enforced by
   `requireRole`.
2. **Invoice creation** — `POST /api/invoices` (submitter only) takes amount,
   counterparty name, contact email, due date, and a required PDF attachment.
3. **States** — `submitted → approved | rejected`; only approvers can decide
   (`/approve`, `/reject`).
4. **No self-approval** — approving/rejecting an invoice you submitted returns 403.
5. **Two-approver rule** — amounts over €10,000 need two *distinct* approvers
   (tracked in the `approvals` table with a `UNIQUE(invoice_id, approver_id)`
   constraint); €10,000 and under need one.
6. **Append-only audit log** — every state change is recorded (actor, action,
   state before/after, timestamp). The app exposes no edit/delete endpoint, and
   SQLite `BEFORE UPDATE`/`BEFORE DELETE` triggers reject any such attempt.
7. **Personal data** — the counterparty email can be erased
   (`DELETE /api/invoices/:id/counterparty-email`), and every read
   (`GET /api/invoices/:id/counterparty-email`) is written to the audit log.
8. **Pending list** — `GET /api/invoices/pending` (approver only) shows invoices
   awaiting *their* approval, filterable by `counterparty`, `min_amount`, and
   `max_amount`.
9. **Email notifications** — every state change writes a message to `EMAIL_DIR`
   (one file per email plus a combined `outbox.log`); no SMTP required.
10. **Tests & README** — automated tests (`npm test`) and this document.

## API reference (brief)

| Method & path                                   | Role       | Purpose                          |
| ----------------------------------------------- | ---------- | -------------------------------- |
| `POST /api/auth/register`                       | public     | Create a user                    |
| `POST /api/auth/login` / `logout`               | public     | Session login/logout             |
| `GET  /api/auth/me`                             | any        | Current user                     |
| `POST /api/invoices`                            | submitter  | Create invoice (multipart, `pdf`)|
| `GET  /api/invoices`                            | any        | List (own for submitters, all for approvers) |
| `GET  /api/invoices/pending`                    | approver   | Pending list, filterable         |
| `GET  /api/invoices/:id`                        | owner/appr | Invoice detail + approvals        |
| `POST /api/invoices/:id/approve` / `reject`     | approver   | Decide                           |
| `GET  /api/invoices/:id/counterparty-email`     | owner/appr | Read email (audited)             |
| `DELETE /api/invoices/:id/counterparty-email`   | owner/appr | Erase email (audited)            |
| `GET  /api/invoices/:id/audit`                  | owner/appr | Audit log for the invoice        |

## Project layout

```
server.js            Entry point: init DB, seed, start HTTP server
src/db.js            SQLite schema (incl. append-only audit triggers)
src/auth.js          Password hashing + auth/role middleware
src/audit.js         Append-only audit log helper
src/email.js         Email notification stub (writes files)
src/app.js           Express app + all routes / business rules
src/seed.js          Demo user seeding
public/index.html    Minimal web UI
test/invoices.test.js  Requirement-by-requirement test suite
```
