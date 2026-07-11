'use strict';

const { test } = require('node:test');
const assert = require('node:assert');
const request = require('supertest');

const { buildTestApp, login, SAMPLE_PDF } = require('./helpers');

const SUBMITTER = 'submitter@nordvik.example';
const APPROVER1 = 'approver1@nordvik.example';

function createInvoice(app, token, name, amount) {
  return request(app)
    .post('/invoices')
    .set('authorization', `Bearer ${token}`)
    .field('amount', String(amount))
    .field('counterpartyName', name)
    .field('counterpartyEmail', 'contact@x.example')
    .field('dueDate', '2026-12-31')
    .attach('pdf', SAMPLE_PDF, 'invoice.pdf');
}

// Requirement 8: approvers see pending list, filterable by counterparty name and amount.
test('pending list is filterable by counterparty name and amount', async () => {
  const { app } = buildTestApp();
  const sToken = await login(request, app, SUBMITTER);
  const aToken = await login(request, app, APPROVER1);

  await createInvoice(app, sToken, 'Alpha Metals', 1000);
  await createInvoice(app, sToken, 'Beta Plastics', 5000);
  await createInvoice(app, sToken, 'Alpha Coatings', 20000);

  const all = await request(app).get('/invoices/pending').set('authorization', `Bearer ${aToken}`);
  assert.equal(all.status, 200);
  assert.equal(all.body.length, 3);

  const byName = await request(app)
    .get('/invoices/pending?counterparty=Alpha')
    .set('authorization', `Bearer ${aToken}`);
  assert.equal(byName.body.length, 2);
  assert.ok(byName.body.every((i) => i.counterpartyName.includes('Alpha')));

  const byAmount = await request(app)
    .get('/invoices/pending?minAmount=4000&maxAmount=6000')
    .set('authorization', `Bearer ${aToken}`);
  assert.equal(byAmount.body.length, 1);
  assert.equal(byAmount.body[0].counterpartyName, 'Beta Plastics');
});

test('pending list excludes own invoices and already-approved ones', async () => {
  const { app, db } = buildTestApp();
  const sToken = await login(request, app, SUBMITTER);
  const aToken = await login(request, app, APPROVER1);

  // Large invoice needs two approvers; after approver1 approves once it should
  // disappear from approver1's pending list even though it is still 'submitted'.
  const inv = await createInvoice(app, sToken, 'Gamma Tools', 50000);
  await request(app).post(`/invoices/${inv.body.id}/approve`).set('authorization', `Bearer ${aToken}`);

  const pending = await request(app)
    .get('/invoices/pending')
    .set('authorization', `Bearer ${aToken}`);
  assert.equal(pending.body.length, 0);

  // An invoice submitted by the approver themselves never appears.
  const a1 = db.prepare('SELECT id FROM users WHERE email = ?').get(APPROVER1);
  db.prepare(
    `INSERT INTO invoices (submitter_id, amount_cents, counterparty_name, counterparty_email, due_date, pdf_path, state, created_at)
     VALUES (?, 100000, 'Own Co', 'o@x.example', '2026-01-01', '/tmp/x.pdf', 'submitted', ?)`
  ).run(a1.id, new Date().toISOString());
  const pending2 = await request(app)
    .get('/invoices/pending')
    .set('authorization', `Bearer ${aToken}`);
  assert.equal(pending2.body.length, 0);
});
