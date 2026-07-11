'use strict';

// Seeds a set of demo users so the system can be exercised immediately.
const { createDb } = require('./db');
const { hashPassword } = require('./auth');

const USERS = [
  { email: 'submitter@nordvik.example', password: 'password123', role: 'submitter' },
  { email: 'submitter2@nordvik.example', password: 'password123', role: 'submitter' },
  { email: 'approver1@nordvik.example', password: 'password123', role: 'approver' },
  { email: 'approver2@nordvik.example', password: 'password123', role: 'approver' },
];

function seed(db = createDb()) {
  const insert = db.prepare(
    'INSERT OR IGNORE INTO users (email, password_hash, role, created_at) VALUES (?, ?, ?, ?)'
  );
  const now = new Date().toISOString();
  for (const u of USERS) {
    insert.run(u.email.toLowerCase(), hashPassword(u.password), u.role, now);
  }
  return USERS;
}

if (require.main === module) {
  seed();
  console.log('Seeded demo users:');
  for (const u of USERS) {
    console.log(`  ${u.role.padEnd(10)} ${u.email}  (password: ${u.password})`);
  }
}

module.exports = { seed, USERS };
