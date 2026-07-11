'use strict';

const { test } = require('node:test');
const assert = require('node:assert');
const request = require('supertest');

const { buildTestApp, readEmails, login, SAMPLE_PDF } = require('./helpers');

const SUBMITTER = 'submitter@nordvik.example';
const SUBMITTER2 = 'submitter2@nordvik.example';
const APPROVER1 = 'approver1@nordvik.example';
const APPROVER2 = 'approver2@nordvik.example';

function createInvoice(app, token, overrides = {}) {
  const req = request(app)
    .post('/invoices')
    .set('authorization', `Bearer ${token}`)
    .field('amount', overrides.amount !== undefined ? String(overrides.amount) : '5000')
    .field('counterpartyName', overrides.counterpartyName || 'Acme Supplies Oy')
    .field('counterpartyEmail', overrides.counterpartyEmail || 'contact@acme.example')
    .field('dueDate', overrides.dueDate || '2026-12-31');
  return req.attach('pdf', SAMPLE_PDF, 'invoice.pdf');
}

// Requirement 1: sign in with email/password; roles enforced.
test('login works and unknown credentials are rejected', async () => {
  const { app } = buildTestApp();
  const ok = await request(app).post('/login').send({ email: SUBMITTER, password: 'password123' });
  assert.equal(ok.status, 200);
  assert.equal(ok.body.role, 'submitter');

  const bad = await request(app).post('/login').send({ email: SUBMITTER, password: 'wrong' });
  assert.equal(bad.status, 401);
});

test('unauthenticated requests are rejected', async () => {
  const { app } = buildTestApp();
  const res = await request(app).get('/invoices');
  assert.equal(res.status, 401);
});

// Requirement 2: submitter creates invoice with all fields + PDF.
test('submitter can create an invoice with a PDF attachment', async () => {
  const { app } = buildTestApp();
  const token = await login(request, app, SUBMITTER);
  const res = await createInvoice(app, token, { amount: 1500.5 });
  assert.equal(res.status, 201);
  assert.equal(res.body.state, 'submitted');
  assert.equal(res.body.amountEuros, 1500.5);
  assert.equal(res.body.counterpartyName, 'Acme Supplies Oy');
});

test('invoice creation requires a PDF attachment', async () => {
  const { app } = buildTestApp();
  const token = await login(request, app, SUBMITTER);
  const res = await request(app)
    .post('/invoices')
    .set('authorization', `Bearer ${token}`)
    .field('amount', '100')
    .field('counterpartyName', 'X')
    .field('counterpartyEmail', 'x@y.example')
    .field('dueDate', '2026-01-01');
  assert.equal(res.status, 400);
});

test('non-PDF attachments are rejected', async () => {
  const { app } = buildTestApp();
  const token = await login(request, app, SUBMITTER);
  const res = await request(app)
    .post('/invoices')
    .set('authorization', `Bearer ${token}`)
    .field('amount', '100')
    .field('counterpartyName', 'X')
    .field('counterpartyEmail', 'x@y.example')
    .field('dueDate', '2026-01-01')
    .attach('pdf', Buffer.from('not a pdf'), 'evil.txt');
  assert.equal(res.status, 400);
});

test('invalid invoice fields are rejected', async () => {
  const { app } = buildTestApp();
  const token = await login(request, app, SUBMITTER);
  const bad = await createInvoice(app, token, { amount: -5 });
  assert.equal(bad.status, 400);
  const bad2 = await createInvoice(app, token, { counterpartyEmail: 'not-an-email' });
  assert.equal(bad2.status, 400);
  const bad3 = await createInvoice(app, token, { dueDate: 'nope' });
  assert.equal(bad3.status, 400);
});

// Requirement 3: only approvers can approve/reject.
test('submitters cannot approve invoices', async () => {
  const { app } = buildTestApp();
  const sToken = await login(request, app, SUBMITTER);
  const inv = await createInvoice(app, sToken);
  const res = await request(app)
    .post(`/invoices/${inv.body.id}/approve`)
    .set('authorization', `Bearer ${sToken}`);
  assert.equal(res.status, 403);
});

// Requirement 5: <= 10000 needs one approval.
test('invoice at or under 10000 EUR is approved by a single approver', async () => {
  const { app, emailLog } = buildTestApp();
  const sToken = await login(request, app, SUBMITTER);
  const aToken = await login(request, app, APPROVER1);
  const inv = await createInvoice(app, sToken, { amount: 10000 });

  const res = await request(app)
    .post(`/invoices/${inv.body.id}/approve`)
    .set('authorization', `Bearer ${aToken}`);
  assert.equal(res.status, 200);
  assert.equal(res.body.state, 'approved');

  // Requirement 9: email notification sent on state change.
  const emails = readEmails(emailLog);
  assert.equal(emails.length, 1);
  assert.match(emails[0].subject, /approved/);
  assert.equal(emails[0].to, SUBMITTER);
});

// Requirement 5: > 10000 needs two distinct approvers.
test('invoice over 10000 EUR needs two distinct approvers', async () => {
  const { app } = buildTestApp();
  const sToken = await login(request, app, SUBMITTER);
  const a1 = await login(request, app, APPROVER1);
  const a2 = await login(request, app, APPROVER2);
  const inv = await createInvoice(app, sToken, { amount: 10000.01 });

  const first = await request(app)
    .post(`/invoices/${inv.body.id}/approve`)
    .set('authorization', `Bearer ${a1}`);
  assert.equal(first.status, 200);
  assert.equal(first.body.state, 'submitted', 'still submitted after one approval');

  // Same approver cannot approve twice.
  const dup = await request(app)
    .post(`/invoices/${inv.body.id}/approve`)
    .set('authorization', `Bearer ${a1}`);
  assert.equal(dup.status, 409);

  const second = await request(app)
    .post(`/invoices/${inv.body.id}/approve`)
    .set('authorization', `Bearer ${a2}`);
  assert.equal(second.status, 200);
  assert.equal(second.body.state, 'approved');
});

// Requirement 4: an approver can never approve/reject their own invoice.
test('approver cannot approve or reject an invoice they submitted', async () => {
  const { app, db } = buildTestApp();
  // Make approver1 also able to submit? Roles are exactly one; instead create an
  // invoice submitted by an approver directly via a submitter-less path is not allowed.
  // So we test the guard by having an approver submit is impossible; verify with a
  // crafted invoice whose submitter is the approver.
  const a1User = db.prepare('SELECT id FROM users WHERE email = ?').get(APPROVER1);
  const info = db
    .prepare(
      `INSERT INTO invoices (submitter_id, amount_cents, counterparty_name, counterparty_email, due_date, pdf_path, state, created_at)
       VALUES (?, 500000, 'Self Co', 'self@x.example', '2026-01-01', '/tmp/x.pdf', 'submitted', ?)`
    )
    .run(a1User.id, new Date().toISOString());

  const a1 = await login(request, app, APPROVER1);
  const approve = await request(app)
    .post(`/invoices/${info.lastInsertRowid}/approve`)
    .set('authorization', `Bearer ${a1}`);
  assert.equal(approve.status, 403);

  const reject = await request(app)
    .post(`/invoices/${info.lastInsertRowid}/reject`)
    .set('authorization', `Bearer ${a1}`);
  assert.equal(reject.status, 403);
});

test('an approver can reject an invoice, notifying the submitter', async () => {
  const { app, emailLog } = buildTestApp();
  const sToken = await login(request, app, SUBMITTER);
  const a1 = await login(request, app, APPROVER1);
  const inv = await createInvoice(app, sToken, { amount: 200 });

  const res = await request(app)
    .post(`/invoices/${inv.body.id}/reject`)
    .set('authorization', `Bearer ${a1}`)
    .send({ reason: 'duplicate' });
  assert.equal(res.status, 200);
  assert.equal(res.body.state, 'rejected');

  const emails = readEmails(emailLog);
  assert.equal(emails.length, 1);
  assert.match(emails[0].subject, /rejected/);
});

test('a decided invoice cannot be approved or rejected again', async () => {
  const { app } = buildTestApp();
  const sToken = await login(request, app, SUBMITTER);
  const a1 = await login(request, app, APPROVER1);
  const a2 = await login(request, app, APPROVER2);
  const inv = await createInvoice(app, sToken, { amount: 100 });

  await request(app).post(`/invoices/${inv.body.id}/approve`).set('authorization', `Bearer ${a1}`);
  const again = await request(app)
    .post(`/invoices/${inv.body.id}/reject`)
    .set('authorization', `Bearer ${a2}`);
  assert.equal(again.status, 409);
});
