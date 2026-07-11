import { DatabaseSync } from 'node:sqlite';
import { mkdirSync } from 'node:fs';
import { dirname } from 'node:path';

const SCHEMA = `
CREATE TABLE IF NOT EXISTS users (
  id            INTEGER PRIMARY KEY,
  email         TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL,
  role          TEXT NOT NULL CHECK(role IN ('submitter','approver'))
);

CREATE TABLE IF NOT EXISTS invoices (
  id                        INTEGER PRIMARY KEY,
  submitter_id              INTEGER NOT NULL REFERENCES users(id),
  amount_cents              INTEGER NOT NULL CHECK(amount_cents > 0),
  counterparty_name         TEXT NOT NULL,
  counterparty_email        TEXT,
  counterparty_email_erased INTEGER NOT NULL DEFAULT 0,
  due_date                  TEXT NOT NULL,
  pdf_path                  TEXT NOT NULL,
  state                     TEXT NOT NULL DEFAULT 'submitted'
                              CHECK(state IN ('submitted','approved','rejected')),
  created_at                TEXT NOT NULL
);

-- One decision per approver per invoice; UNIQUE prevents an approver
-- from counting twice toward the two-approver rule.
CREATE TABLE IF NOT EXISTS approvals (
  id          INTEGER PRIMARY KEY,
  invoice_id  INTEGER NOT NULL REFERENCES invoices(id),
  approver_id INTEGER NOT NULL REFERENCES users(id),
  decision    TEXT NOT NULL CHECK(decision IN ('approve','reject')),
  created_at  TEXT NOT NULL,
  UNIQUE(invoice_id, approver_id)
);

CREATE TABLE IF NOT EXISTS audit_log (
  id           INTEGER PRIMARY KEY,
  invoice_id   INTEGER REFERENCES invoices(id),
  actor_id     INTEGER REFERENCES users(id),
  action       TEXT NOT NULL,
  state_before TEXT,
  state_after  TEXT,
  detail       TEXT,
  created_at   TEXT NOT NULL
);

-- The audit log is append-only. These triggers make it impossible to
-- edit or delete an entry even by direct SQL, backing requirement 6.
CREATE TRIGGER IF NOT EXISTS audit_log_no_update
  BEFORE UPDATE ON audit_log
  BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;

CREATE TRIGGER IF NOT EXISTS audit_log_no_delete
  BEFORE DELETE ON audit_log
  BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
`;

export function initDb(path = process.env.DB_PATH || './data/app.db') {
  if (path !== ':memory:') {
    mkdirSync(dirname(path), { recursive: true });
  }
  const db = new DatabaseSync(path);
  db.exec('PRAGMA foreign_keys = ON;');
  db.exec(SCHEMA);
  return db;
}
