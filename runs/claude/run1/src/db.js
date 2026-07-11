'use strict';

const path = require('path');
const fs = require('fs');
const { DatabaseSync } = require('node:sqlite');

const DB_PATH = process.env.DB_PATH || path.join(__dirname, '..', 'data', 'invoices.db');

function ensureDir(filePath) {
  const dir = path.dirname(filePath);
  if (!fs.existsSync(dir)) {
    fs.mkdirSync(dir, { recursive: true });
  }
}

function createDb(dbPath = DB_PATH) {
  if (dbPath !== ':memory:') {
    ensureDir(dbPath);
  }
  const db = new DatabaseSync(dbPath);
  db.exec('PRAGMA journal_mode = WAL');
  db.exec('PRAGMA foreign_keys = ON');
  migrate(db);
  return db;
}

// node:sqlite has no built-in transaction() helper, so provide a small one.
// Returns a function that runs `fn` inside BEGIN/COMMIT, rolling back on error.
function transaction(db, fn) {
  return (...args) => {
    db.exec('BEGIN');
    try {
      const result = fn(...args);
      db.exec('COMMIT');
      return result;
    } catch (err) {
      db.exec('ROLLBACK');
      throw err;
    }
  };
}

function migrate(db) {
  db.exec(`
    CREATE TABLE IF NOT EXISTS users (
      id            INTEGER PRIMARY KEY AUTOINCREMENT,
      email         TEXT NOT NULL UNIQUE,
      password_hash TEXT NOT NULL,
      role          TEXT NOT NULL CHECK (role IN ('submitter', 'approver')),
      created_at    TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS sessions (
      token      TEXT PRIMARY KEY,
      user_id    INTEGER NOT NULL REFERENCES users(id),
      created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS invoices (
      id                  INTEGER PRIMARY KEY AUTOINCREMENT,
      submitter_id        INTEGER NOT NULL REFERENCES users(id),
      amount_cents        INTEGER NOT NULL CHECK (amount_cents > 0),
      counterparty_name   TEXT NOT NULL,
      counterparty_email  TEXT,
      email_erased        INTEGER NOT NULL DEFAULT 0,
      due_date            TEXT NOT NULL,
      pdf_path            TEXT NOT NULL,
      state               TEXT NOT NULL CHECK (state IN ('submitted', 'approved', 'rejected')),
      created_at          TEXT NOT NULL
    );

    -- Records each distinct approver decision that counts toward approval.
    CREATE TABLE IF NOT EXISTS approvals (
      id          INTEGER PRIMARY KEY AUTOINCREMENT,
      invoice_id  INTEGER NOT NULL REFERENCES invoices(id),
      approver_id INTEGER NOT NULL REFERENCES users(id),
      created_at  TEXT NOT NULL,
      UNIQUE (invoice_id, approver_id)
    );

    -- Append-only audit log. The application exposes no UPDATE or DELETE for this table.
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
  `);

  // Enforce append-only nature of the audit log at the storage layer:
  // any attempt to UPDATE or DELETE a row raises an error.
  db.exec(`
    CREATE TRIGGER IF NOT EXISTS audit_log_no_update
    BEFORE UPDATE ON audit_log
    BEGIN
      SELECT RAISE(ABORT, 'audit_log is append-only');
    END;

    CREATE TRIGGER IF NOT EXISTS audit_log_no_delete
    BEFORE DELETE ON audit_log
    BEGIN
      SELECT RAISE(ABORT, 'audit_log is append-only');
    END;
  `);
}

module.exports = { createDb, transaction, DB_PATH };
