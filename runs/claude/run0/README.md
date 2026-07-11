# Nordvik Invoice Approval Service

Internal web service for approving incoming supplier invoices.

- **Stack:** Python + FastAPI + SQLite (SQLAlchemy). Passwords hashed with
  PBKDF2 (standard library, no external crypto dependency).
- **Storage:** a local SQLite file (`invoices.db`). PDF attachments are saved
  under `uploads/`. Email notifications are written to `emails/outbox.log`.

## Requirements

- Python 3.11+ (developed on 3.14)
- [`uv`](https://docs.astral.sh/uv/) for dependency management
  (install: `curl -LsSf https://astral.sh/uv/install.sh | sh`).

If you prefer plain `pip`, see [Running without uv](#running-without-uv).

## Setup

```bash
uv sync --extra dev
```

This creates a virtual environment and installs all runtime and test
dependencies from `pyproject.toml` / `uv.lock`.

## Run the tests

```bash
uv run pytest
```

All tests are self-contained (each uses an isolated temporary database, upload
dir, and email outbox). Expect `31 passed`.

## Run the service

1. Create some users (email, password, role — `submitter` or `approver`):

   ```bash
   # A ready-made demo set (1 submitter, 2 approvers):
   uv run python -m app.seed demo

   # ...or create users individually:
   uv run python -m app.seed create-user alice@nordvik.example secret submitter
   uv run python -m app.seed create-user bob@nordvik.example   secret approver
   ```

   Demo credentials:
   | Email                         | Password        | Role      |
   |-------------------------------|-----------------|-----------|
   | submitter@nordvik.example     | submitter-pass  | submitter |
   | approver1@nordvik.example     | approver-pass   | approver  |
   | approver2@nordvik.example     | approver-pass   | approver  |

2. Start the server:

   ```bash
   uv run uvicorn app.main:app --reload --port 8000
   ```

3. Open the interactive API docs at <http://localhost:8000/docs>, or use `curl`
   as shown below.

The SQLite database, `uploads/`, and `emails/` directories are created
automatically on first use.

## API walkthrough (curl)

```bash
BASE=http://localhost:8000

# Log in -> returns a bearer token
TOKEN=$(curl -s $BASE/auth/login -H 'Content-Type: application/json' \
  -d '{"email":"submitter@nordvik.example","password":"submitter-pass"}' \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')

# Submit an invoice (multipart form + PDF attachment)
printf '%PDF-1.4 demo' > /tmp/invoice.pdf
curl -s $BASE/invoices -H "Authorization: Bearer $TOKEN" \
  -F amount_eur=15000.00 \
  -F counterparty_name="Acme Oy" \
  -F counterparty_contact_email="ap@acme.example" \
  -F due_date=2026-12-31 \
  -F pdf=@/tmp/invoice.pdf

# As an approver: list your pending queue (filterable)
APPROVER=$(curl -s $BASE/auth/login -H 'Content-Type: application/json' \
  -d '{"email":"approver1@nordvik.example","password":"approver-pass"}' \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')
curl -s "$BASE/invoices/pending?name=acme&min_amount_eur=1000" \
  -H "Authorization: Bearer $APPROVER"

# Approve / reject
curl -s -X POST $BASE/invoices/1/approve -H "Authorization: Bearer $APPROVER"

# Read the (personal-data) contact email — this access is written to the audit log
curl -s $BASE/invoices/1/contact-email -H "Authorization: Bearer $APPROVER"

# Erase the contact email on request (GDPR)
curl -s -X DELETE $BASE/invoices/1/contact-email -H "Authorization: Bearer $APPROVER"

# Inspect the append-only audit trail
curl -s $BASE/invoices/1/audit -H "Authorization: Bearer $APPROVER"
```

## Endpoints

| Method & path                          | Who        | Purpose |
|----------------------------------------|------------|---------|
| `POST /auth/login`                     | anyone     | Exchange email/password for a bearer token |
| `POST /invoices`                       | any user   | Submit an invoice (amount, counterparty, contact email, due date, PDF) |
| `GET  /invoices/{id}`                  | any user   | Invoice details (contact email omitted here on purpose) |
| `GET  /invoices/pending`               | approver   | Pending queue; filter by `name`, `min_amount_eur`, `max_amount_eur` |
| `POST /invoices/{id}/approve`          | approver   | Approve (see two-approver rule below) |
| `POST /invoices/{id}/reject`           | approver   | Reject |
| `GET  /invoices/{id}/contact-email`    | any user   | Read contact email — **logged** to audit |
| `DELETE /invoices/{id}/contact-email`  | any user   | Erase contact email — **logged** to audit |
| `GET  /invoices/{id}/audit`            | any user   | Read-only audit trail (no write/edit/delete verbs exist) |

## How the requirements are met

1. **Email/password auth, two roles.** `POST /auth/login` issues bearer tokens;
   each user has exactly one role (`submitter` / `approver`).
2. **Invoice creation** carries amount (EUR), counterparty name, contact email,
   due date, and a PDF attachment (`POST /invoices`).
3. **State machine** `submitted -> approved | rejected`; only approvers can
   approve/reject (enforced in `app/services.py`).
4. **No self-approval.** An approver cannot approve or reject an invoice they
   submitted (`PermissionDenied` -> HTTP 403).
5. **Amount threshold.** Amounts **over** 10,000 EUR require approvals from **two
   distinct** approvers; at or under 10,000 EUR one approval suffices. The same
   approver cannot count twice (unique `(invoice, approver)` constraint).
6. **Append-only audit log.** Every state change records actor, action,
   state-before, state-after, and timestamp. The audit log has a single write
   path and **no** update or delete API anywhere.
7. **Personal data.** The contact email is only returned via a dedicated
   endpoint that writes a `contact_email_read` audit entry on every access, and
   can be erased via `DELETE`, which writes `contact_email_erased`.
8. **Pending queue** for approvers, filterable by counterparty name and amount.
9. **Notifications.** Every state change appends an email to `emails/outbox.log`
   (SMTP stub — inspect the file to see what would be sent).
10. **Tests + README.** `uv run pytest` (31 tests across domain logic and HTTP).

## Design notes

- Money is stored as integer **cents** (`amount_cents`) to avoid floating-point
  errors; the API accepts/returns euros.
- All business rules live in `app/services.py`, independent of FastAPI, so they
  are unit-tested directly (`tests/test_services.py`) in addition to the HTTP
  tests (`tests/test_api.py`).
- Auth uses server-side session tokens (`sessions` table) sent as
  `Authorization: Bearer <token>`.

## Running without uv

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
pytest                       # run tests
python -m app.seed demo      # seed users
uvicorn app.main:app --port 8000
```

## Project layout

```
app/
  main.py       FastAPI app + routes
  services.py   business rules (state machine, approval policy, audit, GDPR)
  models.py     SQLAlchemy models
  schemas.py    Pydantic request/response models
  security.py   password hashing + token generation
  email_stub.py email notification stub (writes to emails/outbox.log)
  db.py         engine/session management
  seed.py       user-provisioning CLI
tests/
  test_services.py  domain-logic tests
  test_api.py       HTTP API tests
```
