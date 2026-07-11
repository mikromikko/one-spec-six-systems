-- Schema for the invoice approval service.
-- The audit_log table is protected against updates and deletes by triggers,
-- enforcing the append-only requirement at the database level.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    email         TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL CHECK (role IN ('submitter', 'approver')),
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS invoices (
    id                        INTEGER PRIMARY KEY AUTOINCREMENT,
    amount_cents              INTEGER NOT NULL CHECK (amount_cents > 0),
    counterparty_name         TEXT NOT NULL,
    counterparty_email        TEXT,                       -- personal data; nullable so it can be erased
    counterparty_email_erased INTEGER NOT NULL DEFAULT 0, -- 1 once erased on request
    due_date                  TEXT NOT NULL,              -- ISO date (YYYY-MM-DD)
    pdf_path                  TEXT NOT NULL,
    submitter_id              INTEGER NOT NULL REFERENCES users(id),
    state                     TEXT NOT NULL DEFAULT 'submitted'
                                   CHECK (state IN ('submitted', 'approved', 'rejected')),
    created_at                TEXT NOT NULL,
    updated_at                TEXT NOT NULL
);

-- One row per distinct approver who has approved an invoice.
-- The UNIQUE constraint guarantees "two distinct approvers" cannot be one
-- approver counted twice.
CREATE TABLE IF NOT EXISTS approvals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id  INTEGER NOT NULL REFERENCES invoices(id),
    approver_id INTEGER NOT NULL REFERENCES users(id),
    created_at  TEXT NOT NULL,
    UNIQUE (invoice_id, approver_id)
);

-- Append-only audit log: who (actor_id), what changed (state_before/state_after
-- and action/detail), and when (created_at).
CREATE TABLE IF NOT EXISTS audit_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id   INTEGER REFERENCES invoices(id),
    actor_id     INTEGER REFERENCES users(id),
    action       TEXT NOT NULL,
    state_before TEXT,
    state_after  TEXT,
    detail       TEXT,
    created_at   TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS audit_log_no_update
BEFORE UPDATE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'audit_log is append-only: updates are not permitted');
END;

CREATE TRIGGER IF NOT EXISTS audit_log_no_delete
BEFORE DELETE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'audit_log is append-only: deletes are not permitted');
END;
