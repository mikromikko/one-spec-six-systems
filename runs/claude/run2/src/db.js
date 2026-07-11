'use strict';

const fs = require('node:fs');
const path = require('node:path');
const { DatabaseSync } = require('node:sqlite');

/**
 * Open a SQLite database and ensure the schema exists.
 *
 * @param {string} path  File path, or ':memory:' for an ephemeral DB (used in tests).
 * @returns {DatabaseSync}
 */
function openDb(dbPath) {
  if (dbPath !== ':memory:') {
    fs.mkdirSync(path.dirname(dbPath), { recursive: true });
  }
  const db = new DatabaseSync(dbPath);
  db.exec('PRAGMA journal_mode = WAL;');
  db.exec('PRAGMA foreign_keys = ON;');
  initSchema(db);
  return db;
}

function initSchema(db) {
  db.exec(`
    CREATE TABLE IF NOT EXISTS users (
      id            INTEGER PRIMARY KEY AUTOINCREMENT,
      email         TEXT NOT NULL UNIQUE,
      password_hash TEXT NOT NULL,
      role          TEXT NOT NULL CHECK (role IN ('submitter', 'approver')),
      created_at    TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS invoices (
      id                     INTEGER PRIMARY KEY AUTOINCREMENT,
      amount_cents           INTEGER NOT NULL CHECK (amount_cents > 0),
      counterparty_name      TEXT NOT NULL,
      counterparty_email     TEXT,                       -- personal data; NULL once erased
      counterparty_email_erased INTEGER NOT NULL DEFAULT 0,
      due_date               TEXT NOT NULL,              -- ISO 8601 date (YYYY-MM-DD)
      pdf_path               TEXT NOT NULL,
      pdf_original_name      TEXT NOT NULL,
      submitter_id           INTEGER NOT NULL REFERENCES users(id),
      state                  TEXT NOT NULL DEFAULT 'submitted'
                               CHECK (state IN ('submitted', 'approved', 'rejected')),
      created_at             TEXT NOT NULL,
      updated_at             TEXT NOT NULL
    );

    -- One row per distinct approver decision to approve an invoice.
    -- The UNIQUE constraint enforces "two distinct approvers" (req 5): the same
    -- approver cannot count twice.
    CREATE TABLE IF NOT EXISTS approvals (
      id          INTEGER PRIMARY KEY AUTOINCREMENT,
      invoice_id  INTEGER NOT NULL REFERENCES invoices(id),
      approver_id INTEGER NOT NULL REFERENCES users(id),
      created_at  TEXT NOT NULL,
      UNIQUE (invoice_id, approver_id)
    );

    -- Append-only audit log (req 6 & 7). No UPDATE/DELETE is ever issued by the
    -- application, and the triggers below make that guarantee enforceable at the
    -- storage layer.
    CREATE TABLE IF NOT EXISTS audit_log (
      id           INTEGER PRIMARY KEY AUTOINCREMENT,
      invoice_id   INTEGER REFERENCES invoices(id),
      actor_id     INTEGER REFERENCES users(id),
      action       TEXT NOT NULL,
      state_before TEXT,
      state_after  TEXT,
      details      TEXT,
      created_at   TEXT NOT NULL
    );

    -- Enforce append-only-ness at the database level.
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

    CREATE INDEX IF NOT EXISTS idx_invoices_state ON invoices(state);
    CREATE INDEX IF NOT EXISTS idx_approvals_invoice ON approvals(invoice_id);
    CREATE INDEX IF NOT EXISTS idx_audit_invoice ON audit_log(invoice_id);
  `);
}

module.exports = { openDb };
