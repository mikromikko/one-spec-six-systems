'use strict';

// Seed a few demo users so the service is usable straight after `npm run seed`.
// Idempotent: existing users are left untouched.

const path = require('node:path');
const { openDb } = require('./db');
const { hashPassword } = require('./auth');

const dbPath = process.env.DB_PATH || path.join(__dirname, '..', 'data', 'app.db');
const db = openDb(dbPath);

const users = [
  { email: 'submitter@nordvik.example', password: 'submitter-pass', role: 'submitter' },
  { email: 'approver1@nordvik.example', password: 'approver-pass', role: 'approver' },
  { email: 'approver2@nordvik.example', password: 'approver-pass', role: 'approver' },
];

const now = new Date().toISOString();
const insert = db.prepare(
  'INSERT OR IGNORE INTO users (email, password_hash, role, created_at) VALUES (?, ?, ?, ?)',
);

for (const u of users) {
  const info = insert.run(u.email, hashPassword(u.password), u.role, now);
  const created = info.changes > 0;
  // eslint-disable-next-line no-console
  console.log(`${created ? 'created' : 'exists '}  ${u.role.padEnd(9)}  ${u.email}  (password: ${u.password})`);
}

// eslint-disable-next-line no-console
console.log(`\nSeed complete. Database: ${dbPath}`);
