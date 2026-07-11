import { initDb } from './db.js';
import { hashPassword } from './auth.js';

// Seed a set of demo users so the system is usable straight after setup.
const USERS = [
  { email: 'submitter1@nordvik.example', password: 'password123', role: 'submitter' },
  { email: 'submitter2@nordvik.example', password: 'password123', role: 'submitter' },
  { email: 'approver1@nordvik.example', password: 'password123', role: 'approver' },
  { email: 'approver2@nordvik.example', password: 'password123', role: 'approver' },
  { email: 'approver3@nordvik.example', password: 'password123', role: 'approver' },
];

export function seed(db) {
  const insert = db.prepare('INSERT OR IGNORE INTO users (email, password_hash, role) VALUES (?, ?, ?)');
  let created = 0;
  for (const u of USERS) {
    const info = insert.run(u.email, hashPassword(u.password), u.role);
    if (info.changes) created++;
  }
  return created;
}

// Allow running directly: `npm run seed`.
if (import.meta.url === `file://${process.argv[1]}`) {
  const db = initDb();
  const created = seed(db);
  console.log(`Seed complete. ${created} user(s) created.`);
  console.log('Demo accounts (all password: password123):');
  for (const u of USERS) console.log(`  ${u.role.padEnd(9)}  ${u.email}`);
}
