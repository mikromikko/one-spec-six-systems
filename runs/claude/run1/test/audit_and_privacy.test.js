'use strict';

const { test } = require('node:test');
const assert = require('node:assert');
const request = require('supertest');

const { buildTestApp, login, SAMPLE_PDF } = require('./helpers');

const SUBMITTER = 'submitter@nordvik.example';
const APPROVER1 = 'approver1@nordvik.example';

function createInvoice(app, token, amount = 5000) {
  return request(app)
    .post('/invoices')
    .set('authorization', `Bearer ${token}`)
    .field('amount', String(amount))
    .field('counterpartyName', 'Acme Supplies Oy')
    .field('counterpartyEmail', 'contact@acme.example')
    .field('dueDate', '2026-12-31')
    .attach('pdf', SAMPLE_PDF, 'invoice.pdf');
}

// Requirement 6: every state change recorded (who/before/after/when), append-only.
test('state changes are recorded in the audit log', async () => {
  const { app } = buildTestApp();
  const sToken = await login(request, app, SUBMITTER);
  const a1 = await login(request, app, APPROVER1);
  const inv = await createInvoice(app, sToken, 500);
  await request(app).post(`/invoices/${inv.body.id}/approve`).set('authorization', `Bearer ${a1}`);

  const audit = await request(app)
    .get(`/invoices/${inv.body.id}/audit`)
    .set('authorization', `Bearer ${sToken}`);
  assert.equal(audit.status, 200);

  const submit = audit.body.find((e) => e.action === 'submit');
  assert.ok(submit);
  assert.equal(submit.state_before, null);
  assert.equal(submit.state_after, 'submitted');
  assert.ok(submit.actor_id);
  assert.ok(submit.created_at);

  const approve = audit.body.find((e) => e.action === 'approve');
  assert.ok(approve);
  assert.equal(approve.state_before, 'submitted');
  assert.equal(approve.state_after, 'approved');
});

test('audit log is append-only at the storage layer (no update/delete)', async () => {
  const { app, db } = buildTestApp();
  const sToken = await login(request, app, SUBMITTER);
  await createInvoice(app, sToken, 500);

  assert.throws(
    () => db.prepare('UPDATE audit_log SET action = ? WHERE id = 1').run('tampered'),
    /append-only/
  );
  assert.throws(() => db.prepare('DELETE FROM audit_log WHERE id = 1').run(), /append-only/);
});

// Requirement 7: counterparty email is personal data; reads are audited; erasable.
test('reading the counterparty email is recorded in the audit log', async () => {
  const { app } = buildTestApp();
  const sToken = await login(request, app, SUBMITTER);
  const inv = await createInvoice(app, sToken);

  const read = await request(app)
    .get(`/invoices/${inv.body.id}/counterparty-email`)
    .set('authorization', `Bearer ${sToken}`);
  assert.equal(read.status, 200);
  assert.equal(read.body.counterpartyEmail, 'contact@acme.example');

  const audit = await request(app)
    .get(`/invoices/${inv.body.id}/audit`)
    .set('authorization', `Bearer ${sToken}`);
  const reads = audit.body.filter((e) => e.action === 'read_counterparty_email');
  assert.equal(reads.length, 1);
});

test('counterparty email can be erased and is then unreadable', async () => {
  const { app } = buildTestApp();
  const sToken = await login(request, app, SUBMITTER);
  const inv = await createInvoice(app, sToken);

  const del = await request(app)
    .delete(`/invoices/${inv.body.id}/counterparty-email`)
    .set('authorization', `Bearer ${sToken}`);
  assert.equal(del.status, 200);

  const read = await request(app)
    .get(`/invoices/${inv.body.id}/counterparty-email`)
    .set('authorization', `Bearer ${sToken}`);
  assert.equal(read.status, 410);

  const detail = await request(app)
    .get(`/invoices/${inv.body.id}`)
    .set('authorization', `Bearer ${sToken}`);
  assert.equal(detail.body.counterpartyEmailErased, true);

  // Erasure itself is audited.
  const audit = await request(app)
    .get(`/invoices/${inv.body.id}/audit`)
    .set('authorization', `Bearer ${sToken}`);
  assert.ok(audit.body.find((e) => e.action === 'erase_counterparty_email'));
});
