# Invoice Approval Service

An internal web service for **Nordvik Components Oy** to approve incoming
supplier invoices.

Built with Python (Flask) and SQLite. It exposes both a **JSON REST API** and a
small **server-rendered HTML interface**. No external services are required —
email notifications are written to a local file.

## Requirements

- Python 3.9+ (developed and tested on 3.14)
- No other system dependencies; Flask and pytest are installed into a
  virtual environment as shown below.

## Setup

Run every command from this directory.

```bash
# 1. Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Create demo users (database is created automatically on first run)
export FLASK_APP=wsgi              # Windows (cmd): set FLASK_APP=wsgi
flask seed-demo
```

`flask seed-demo` creates four users, all with password `password123`:

| Email                      | Role      |
|----------------------------|-----------|
| submitter@nordvik.test     | submitter |
| submitter2@nordvik.test    | submitter |
| approver@nordvik.test      | approver  |
| approver2@nordvik.test     | approver  |

You can also create your own user:

```bash
flask create-user alice@nordvik.test approver
```

## Run the service

```bash
export FLASK_APP=wsgi
flask run
```

Then open <http://127.0.0.1:5000/> and sign in with one of the demo users.

- **Submitters** can create invoices and view their own.
- **Approvers** see a filterable list of invoices pending their approval, and
  can approve or reject.

Data is stored under `data/`:
- `data/app.db` — SQLite database
- `data/uploads/` — uploaded PDF attachments
- `data/emails.log` — the notification "outbox" (stub for email)

## Run the tests

```bash
source .venv/bin/activate
pip install -r requirements.txt   # if not already installed
pytest
```

The tests use isolated temporary databases and do not touch `data/`.

## How the requirements are met

1. **Email/password auth, two roles.** Users have exactly one role
   (`submitter` or `approver`), enforced by a DB `CHECK` constraint. Passwords
   are hashed (Werkzeug). Sessions via signed cookies.
2. **Invoice creation.** Amount (EUR), counterparty name, counterparty contact
   email, due date and a PDF attachment. The upload is validated to be a PDF.
3. **State machine.** `submitted → approved | rejected`. Only approvers can
   approve or reject (`role_required` / service-layer checks).
4. **No self-approval.** An approver can never approve or reject an invoice
   they submitted (`submitter_id != actor` check). Approvers may also submit
   invoices, so this rule is actively enforced.
5. **Dual approval over EUR 10,000.** Amounts **over** 10,000 require two
   **distinct** approvers; the `approvals` table has a
   `UNIQUE (invoice_id, approver_id)` constraint so one approver cannot count
   twice. At or under 10,000, one approval is enough.
6. **Append-only audit log.** Every state change records who, the state before
   and after, and when. There is no API to edit or delete entries, and SQLite
   triggers reject any `UPDATE`/`DELETE` on the `audit_log` table.
7. **Personal data handling.** The counterparty contact email is only returned
   via a dedicated endpoint, and **every read is recorded** in the audit log.
   It can be **erased on request** (`DELETE .../counterparty-email` or the
   "Erase contact email" button); the erasure is itself audited.
8. **Pending list with filters.** Approvers get a list of invoices pending
   their approval, filterable by counterparty name and by amount range.
9. **Notifications.** On every state change the submitter is notified. This is a
   stub that appends the message to `data/emails.log`.
10. **Tests and README.** `pytest` suite in `tests/` plus this README.

## JSON API reference

All endpoints are under `/api`. Authenticate first; the session cookie is
reused for subsequent requests.

| Method & path | Description |
|---|---|
| `POST /api/auth/login` | Log in (`{"email","password"}`) |
| `POST /api/auth/logout` | Log out |
| `GET  /api/auth/me` | Current user |
| `POST /api/invoices` | Create invoice (multipart form: `amount`, `counterparty_name`, `counterparty_email`, `due_date`, `pdf`) |
| `GET  /api/invoices` | List invoices (own for submitters, all for approvers) |
| `GET  /api/invoices/<id>` | Invoice detail (never includes the raw email) |
| `GET  /api/invoices/<id>/pdf` | Download the PDF attachment |
| `POST /api/invoices/<id>/approve` | Approve |
| `POST /api/invoices/<id>/reject` | Reject (`{"reason": "..."}` optional) |
| `GET  /api/invoices/<id>/counterparty-email` | Read the contact email (audited) |
| `DELETE /api/invoices/<id>/counterparty-email` | Erase the contact email |
| `GET  /api/invoices/<id>/audit` | Invoice audit log |
| `GET  /api/pending?counterparty=&min_amount=&max_amount=` | Pending list for approver |

### Example (curl)

```bash
# log in and keep the session cookie
curl -c cookies.txt -X POST http://127.0.0.1:5000/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"submitter@nordvik.test","password":"password123"}'

# create an invoice
curl -b cookies.txt -X POST http://127.0.0.1:5000/api/invoices \
  -F amount=15000.00 -F counterparty_name="Test Oy" \
  -F counterparty_email=contact@test.example -F due_date=2026-12-01 \
  -F pdf=@/path/to/invoice.pdf
```

## Project layout

```
app/
  __init__.py       app factory, error handlers
  schema.sql        tables + append-only triggers
  db.py             SQLite connection handling
  auth.py           login/logout, session, access decorators
  service.py        all business rules (single source of truth)
  api.py            JSON REST API
  views.py          HTML interface
  notifications.py  email stub (writes to data/emails.log)
  commands.py       CLI: init-db, create-user, seed-demo
  templates/        HTML templates
tests/              pytest suite
wsgi.py             entrypoint (flask run)
```
