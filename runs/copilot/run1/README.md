# Nordvik Invoice Approval Service

Internal web service for approving incoming supplier invoices. It uses FastAPI and SQLite, stores uploaded PDFs locally, and writes notification emails to a JSON-lines log file instead of SMTP.

## Run locally

1. Create and activate a virtual environment:

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```

2. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

3. Start the service:

   ```bash
   uvicorn app.main:app --reload
   ```

4. Open the API docs at <http://127.0.0.1:8000/docs>.

By default the service creates `data/invoices.sqlite3`, stores PDFs in `data/uploads/`, and appends email notifications to `data/notifications.log`. Override these with `INVOICE_DB_PATH`, `INVOICE_UPLOAD_DIR`, and `NOTIFICATION_LOG_PATH`.

## Run tests

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pytest
```

## Main API flow

1. Create users with `POST /users`, using role `submitter` or `approver`.
2. Sign in with `POST /auth/login` and use the returned bearer token in `Authorization: Bearer <token>`.
3. Submitters create invoices with `POST /invoices` as multipart form data: `amount_euros`, `counterparty_name`, `counterparty_contact_email`, `due_date`, and `pdf`.
4. Approvers list pending work with `GET /invoices/pending`, optionally filtering by `counterparty_name`, `min_amount_euros`, and `max_amount_euros`.
5. Approvers decide with `POST /invoices/{id}/approve` or `POST /invoices/{id}/reject`. Invoices over EUR 10,000 require approvals from two distinct approvers.
6. Read personal contact email with `GET /invoices/{id}/counterparty-contact-email`; each read is audit logged. Erase it with `POST /invoices/{id}/erase-counterparty-contact-email`.
7. Review immutable audit entries with `GET /audit-logs`.
