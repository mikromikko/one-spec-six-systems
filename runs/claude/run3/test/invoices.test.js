import { test, before, beforeEach, after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import request from 'supertest';

import { initDb } from '../src/db.js';
import { createApp } from '../src/app.js';
import { hashPassword } from '../src/auth.js';

let db, app, workDir, pdfPath;

// Create a user directly and return the row.
function makeUser(email, role, password = 'password123') {
  const info = db
    .prepare('INSERT INTO users (email, password_hash, role) VALUES (?, ?, ?)')
    .run(email, hashPassword(password), role);
  return { id: Number(info.lastInsertRowid), email, role, password };
}

// Return a logged-in supertest agent for the given user.
async function agentFor(user) {
  const a = request.agent(app);
  await a.post('/api/auth/login').send({ email: user.email, password: user.password }).expect(200);
  return a;
}

// Submit an invoice through the API and return the created invoice body.
async function submitInvoice(agent, { amount, name = 'Acme Oy', email = 'contact@acme.example', due = '2026-12-31' }) {
  const res = await agent
    .post('/api/invoices')
    .field('amount', String(amount))
    .field('counterparty_name', name)
    .field('counterparty_email', email)
    .field('due_date', due)
    .attach('pdf', pdfPath)
    .expect(201);
  return res.body;
}

before(() => {
  workDir = mkdtempSync(join(tmpdir(), 'invoice-test-'));
  pdfPath = join(workDir, 'sample.pdf');
  writeFileSync(pdfPath, '%PDF-1.4\n%test\n');
});

after(() => rmSync(workDir, { recursive: true, force: true }));

beforeEach(() => {
  db = initDb(':memory:');
  app = createApp(db, { uploadDir: join(workDir, 'uploads'), emailDir: join(workDir, 'emails') });
});

test('req 1: users authenticate; bad credentials rejected', async () => {
  const u = makeUser('s1@x.example', 'submitter');
  await request(app).post('/api/auth/login').send({ email: u.email, password: 'wrong' }).expect(401);
  const a = await agentFor(u);
  const me = await a.get('/api/auth/me').expect(200);
  assert.equal(me.body.role, 'submitter');
});

test('req 2 + 3: submitter creates an invoice in submitted state', async () => {
  const s = makeUser('s@x.example', 'submitter');
  const a = await agentFor(s);
  const inv = await submitInvoice(a, { amount: 500 });
  assert.equal(inv.state, 'submitted');
  assert.equal(inv.amount_eur, 500);
  assert.equal(inv.counterparty_name, 'Acme Oy');
});

test('req 2: a PDF attachment is required', async () => {
  const s = makeUser('s@x.example', 'submitter');
  const a = await agentFor(s);
  const res = await a
    .post('/api/invoices')
    .field('amount', '100')
    .field('counterparty_name', 'Acme')
    .field('counterparty_email', 'c@acme.example')
    .field('due_date', '2026-12-31')
    .expect(400);
  assert.match(res.body.error, /PDF/);
});

test('req 2: non-PDF attachment is rejected', async () => {
  const s = makeUser('s@x.example', 'submitter');
  const a = await agentFor(s);
  const txt = join(workDir, 'note.txt');
  writeFileSync(txt, 'not a pdf');
  const res = await a
    .post('/api/invoices')
    .field('amount', '100')
    .field('counterparty_name', 'Acme')
    .field('counterparty_email', 'c@acme.example')
    .field('due_date', '2026-12-31')
    .attach('pdf', txt)
    .expect(400);
  assert.match(res.body.error, /PDF/);
});

test('req 3: submitters cannot approve invoices', async () => {
  const s = makeUser('s@x.example', 'submitter');
  const s2 = makeUser('s2@x.example', 'submitter');
  const a = await agentFor(s);
  const inv = await submitInvoice(a, { amount: 100 });
  const a2 = await agentFor(s2);
  await a2.post(`/api/invoices/${inv.id}/approve`).expect(403);
});

test('req 4: an approver cannot approve/reject their own invoice', async () => {
  // An approver may also submit invoices; they must not act on them.
  const approver = makeUser('ap@x.example', 'approver');
  const a = request.agent(app);
  // approver role user cannot use the submitter-only create endpoint,
  // so insert the invoice directly with them as submitter.
  await a.post('/api/auth/login').send({ email: approver.email, password: approver.password }).expect(200);
  const info = db
    .prepare(
      `INSERT INTO invoices (submitter_id, amount_cents, counterparty_name, counterparty_email, due_date, pdf_path, state, created_at)
       VALUES (?, 5000, 'Self Oy', 'c@self.example', '2026-12-31', '/tmp/x.pdf', 'submitted', ?)`
    )
    .run(approver.id, new Date().toISOString());
  const id = Number(info.lastInsertRowid);
  await a.post(`/api/invoices/${id}/approve`).expect(403);
  await a.post(`/api/invoices/${id}/reject`).expect(403);
});

test('req 5: <= 10,000 EUR is approved after one approval', async () => {
  const s = makeUser('s@x.example', 'submitter');
  const approver = makeUser('ap@x.example', 'approver');
  const inv = await submitInvoice(await agentFor(s), { amount: 10000 });
  const a = await agentFor(approver);
  const res = await a.post(`/api/invoices/${inv.id}/approve`).expect(200);
  assert.equal(res.body.state, 'approved');
});

test('req 5: > 10,000 EUR needs two distinct approvers', async () => {
  const s = makeUser('s@x.example', 'submitter');
  const ap1 = makeUser('ap1@x.example', 'approver');
  const ap2 = makeUser('ap2@x.example', 'approver');
  const inv = await submitInvoice(await agentFor(s), { amount: 10000.01 });
  assert.equal(inv.approvals_required, 2);

  const a1 = await agentFor(ap1);
  const first = await a1.post(`/api/invoices/${inv.id}/approve`).expect(200);
  assert.equal(first.body.state, 'submitted', 'still pending after one approval');

  // Same approver cannot approve twice.
  await a1.post(`/api/invoices/${inv.id}/approve`).expect(409);

  const a2 = await agentFor(ap2);
  const second = await a2.post(`/api/invoices/${inv.id}/approve`).expect(200);
  assert.equal(second.body.state, 'approved');
});

test('req 5: a single reject rejects the invoice regardless of amount', async () => {
  const s = makeUser('s@x.example', 'submitter');
  const ap1 = makeUser('ap1@x.example', 'approver');
  const inv = await submitInvoice(await agentFor(s), { amount: 50000 });
  const a1 = await agentFor(ap1);
  const res = await a1.post(`/api/invoices/${inv.id}/reject`).expect(200);
  assert.equal(res.body.state, 'rejected');
});

test('req 6: every state change is recorded in the append-only audit log', async () => {
  const s = makeUser('s@x.example', 'submitter');
  const ap1 = makeUser('ap1@x.example', 'approver');
  const ap2 = makeUser('ap2@x.example', 'approver');
  const inv = await submitInvoice(await agentFor(s), { amount: 20000 });
  await (await agentFor(ap1)).post(`/api/invoices/${inv.id}/approve`).expect(200);
  await (await agentFor(ap2)).post(`/api/invoices/${inv.id}/approve`).expect(200);

  const audit = await (await agentFor(s)).get(`/api/invoices/${inv.id}/audit`).expect(200);
  const actions = audit.body.map((r) => r.action);
  assert.ok(actions.includes('create'));
  assert.ok(actions.includes('approve'));
  const approved = audit.body.find((r) => r.action === 'approve');
  assert.equal(approved.state_before, 'submitted');
  assert.equal(approved.state_after, 'approved');

  // Append-only: direct UPDATE/DELETE must fail.
  assert.throws(() => db.prepare('UPDATE audit_log SET action = ? WHERE id = ?').run('x', audit.body[0].id));
  assert.throws(() => db.prepare('DELETE FROM audit_log WHERE id = ?').run(audit.body[0].id));
});

test('req 7: reading the counterparty email is audited; erasure works', async () => {
  const s = makeUser('s@x.example', 'submitter');
  const inv = await submitInvoice(await agentFor(s), { amount: 100, email: 'person@vendor.example' });
  const a = await agentFor(s);

  const read = await a.get(`/api/invoices/${inv.id}/counterparty-email`).expect(200);
  assert.equal(read.body.counterparty_email, 'person@vendor.example');

  let audit = await a.get(`/api/invoices/${inv.id}/audit`).expect(200);
  assert.ok(audit.body.some((r) => r.action === 'read_counterparty_email'));

  // Erase, then confirm it is gone and the erasure is audited.
  await a.delete(`/api/invoices/${inv.id}/counterparty-email`).expect(200);
  const read2 = await a.get(`/api/invoices/${inv.id}/counterparty-email`).expect(200);
  assert.equal(read2.body.counterparty_email, null);
  assert.equal(read2.body.erased, true);

  audit = await a.get(`/api/invoices/${inv.id}/audit`).expect(200);
  assert.ok(audit.body.some((r) => r.action === 'erase_counterparty_email'));
});

test('req 8: pending list is filterable and excludes own/decided invoices', async () => {
  const s = makeUser('s@x.example', 'submitter');
  const ap1 = makeUser('ap1@x.example', 'approver');
  const ap2 = makeUser('ap2@x.example', 'approver');
  const sa = await agentFor(s);
  await submitInvoice(sa, { amount: 100, name: 'Alpha Oy' });
  await submitInvoice(sa, { amount: 5000, name: 'Beta Oy' });
  await submitInvoice(sa, { amount: 9000, name: 'Alpha Traders' });

  const a1 = await agentFor(ap1);
  let pending = await a1.get('/api/invoices/pending').expect(200);
  assert.equal(pending.body.length, 3);

  // Filter by counterparty name.
  pending = await a1.get('/api/invoices/pending?counterparty=Alpha').expect(200);
  assert.equal(pending.body.length, 2);

  // Filter by amount range.
  pending = await a1.get('/api/invoices/pending?min_amount=1000&max_amount=8000').expect(200);
  assert.equal(pending.body.length, 1);
  assert.equal(pending.body[0].counterparty_name, 'Beta Oy');

  // After ap1 approves a big invoice partially, it drops off ap1's list but stays on ap2's.
  const big = await submitInvoice(sa, { amount: 30000, name: 'Gamma Oy' });
  await a1.post(`/api/invoices/${big.id}/approve`).expect(200);
  const p1 = await a1.get('/api/invoices/pending?counterparty=Gamma').expect(200);
  assert.equal(p1.body.length, 0);
  const a2 = await agentFor(ap2);
  const p2 = await a2.get('/api/invoices/pending?counterparty=Gamma').expect(200);
  assert.equal(p2.body.length, 1);
});

test('req 9: a state change writes an email notification to the stub', async () => {
  const s = makeUser('s@x.example', 'submitter');
  const ap1 = makeUser('ap1@x.example', 'approver');
  const inv = await submitInvoice(await agentFor(s), { amount: 100 });
  await (await agentFor(ap1)).post(`/api/invoices/${inv.id}/approve`).expect(200);

  const { readFileSync } = await import('node:fs');
  const outbox = readFileSync(join(workDir, 'emails', 'outbox.log'), 'utf8');
  assert.match(outbox, new RegExp(`Invoice #${inv.id} approved`));
  assert.match(outbox, /s@x\.example/);
});

test('terminal invoices cannot be re-decided', async () => {
  const s = makeUser('s@x.example', 'submitter');
  const ap1 = makeUser('ap1@x.example', 'approver');
  const ap2 = makeUser('ap2@x.example', 'approver');
  const inv = await submitInvoice(await agentFor(s), { amount: 100 });
  await (await agentFor(ap1)).post(`/api/invoices/${inv.id}/approve`).expect(200);
  await (await agentFor(ap2)).post(`/api/invoices/${inv.id}/reject`).expect(409);
});
