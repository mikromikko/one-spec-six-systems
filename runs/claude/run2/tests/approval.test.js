'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { startServer, seedUsers, createInvoice, auth } = require('./helpers');

function post(base, token, url, body) {
  return fetch(`${base}${url}`, {
    method: 'POST',
    headers: { ...auth(token), 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
  });
}

test('single approval is enough at or under 10,000 EUR (req 5)', async (t) => {
  const srv = await startServer();
  t.after(() => srv.close());
  const u = await seedUsers(srv.base);

  const { invoice } = await (await createInvoice(srv.base, u.submitter, { amount: 10000 })).json();
  assert.equal(invoice.requiredApprovals, 1);

  const res = await post(srv.base, u.approver1, `/api/invoices/${invoice.id}/approve`);
  const body = await res.json();
  assert.equal(body.invoice.state, 'approved');
});

test('over 10,000 EUR requires two distinct approvers (req 5)', async (t) => {
  const srv = await startServer();
  t.after(() => srv.close());
  const u = await seedUsers(srv.base);

  const { invoice } = await (await createInvoice(srv.base, u.submitter, { amount: 10000.01 })).json();
  assert.equal(invoice.requiredApprovals, 2);

  // First approval leaves it submitted.
  let body = await (await post(srv.base, u.approver1, `/api/invoices/${invoice.id}/approve`)).json();
  assert.equal(body.invoice.state, 'submitted');
  assert.equal(body.invoice.approvals.length, 1);

  // Same approver approving again does not count.
  const dup = await post(srv.base, u.approver1, `/api/invoices/${invoice.id}/approve`);
  assert.equal(dup.status, 409);

  // A second distinct approver flips it to approved.
  body = await (await post(srv.base, u.approver2, `/api/invoices/${invoice.id}/approve`)).json();
  assert.equal(body.invoice.state, 'approved');
  assert.equal(body.invoice.approvals.length, 2);
});

test('an approver cannot approve or reject their own invoice (req 4)', async (t) => {
  const srv = await startServer();
  t.after(() => srv.close());
  const u = await seedUsers(srv.base);

  // approver1 is also allowed to submit? No — submitting requires submitter role.
  // Simulate: give the invoice a submitter who is ALSO an approver by having the
  // approver submit is impossible, so instead we verify self-approval is blocked
  // by checking the submitter path. We create a user that is an approver and
  // confirm the rule via a submitter-owned invoice being approvable only by others.
  // Here we directly test the guard using approver1 as submitter is not possible,
  // so we assert the guard through the state machine: approver1 approves someone
  // else's invoice fine, and rejects it fine.
  const { invoice } = await (await createInvoice(srv.base, u.submitter, { amount: 500 })).json();

  // Confirm the self-approval guard by inserting an invoice owned by an approver.
  // We do this at the DB layer to represent a user whose role changed, exercising req 4.
  const db = srv.app.locals.db;
  const approverId = db.prepare('SELECT id FROM users WHERE email = ?').get('app1@x.test').id;
  db.prepare('UPDATE invoices SET submitter_id = ? WHERE id = ?').run(approverId, invoice.id);

  const selfApprove = await post(srv.base, u.approver1, `/api/invoices/${invoice.id}/approve`);
  assert.equal(selfApprove.status, 403);
  const selfReject = await post(srv.base, u.approver1, `/api/invoices/${invoice.id}/reject`);
  assert.equal(selfReject.status, 403);

  // A different approver can still act on it.
  const other = await post(srv.base, u.approver2, `/api/invoices/${invoice.id}/approve`);
  assert.equal(other.status, 200);
});

test('rejection moves invoice to rejected and only approvers can decide (reqs 3, 4)', async (t) => {
  const srv = await startServer();
  t.after(() => srv.close());
  const u = await seedUsers(srv.base);

  const { invoice } = await (await createInvoice(srv.base, u.submitter, { amount: 500 })).json();

  // A submitter cannot reject.
  const bySubmitter = await post(srv.base, u.submitter2, `/api/invoices/${invoice.id}/reject`);
  assert.equal(bySubmitter.status, 403);

  const rejected = await (await post(srv.base, u.approver1, `/api/invoices/${invoice.id}/reject`)).json();
  assert.equal(rejected.invoice.state, 'rejected');

  // No further transitions once terminal.
  const again = await post(srv.base, u.approver2, `/api/invoices/${invoice.id}/approve`);
  assert.equal(again.status, 409);
});

test('pending list is filterable by counterparty name and amount (req 8)', async (t) => {
  const srv = await startServer();
  t.after(() => srv.close());
  const u = await seedUsers(srv.base);

  await createInvoice(srv.base, u.submitter, { amount: 100, name: 'Acme Oy' });
  await createInvoice(srv.base, u.submitter, { amount: 9000, name: 'Beta Ab' });
  await createInvoice(srv.base, u.submitter, { amount: 20000, name: 'Acme Oy' });

  const all = await (await fetch(`${srv.base}/api/invoices`, { headers: auth(u.approver1) })).json();
  assert.equal(all.invoices.length, 3);

  const byName = await (await fetch(`${srv.base}/api/invoices?counterpartyName=Acme`, { headers: auth(u.approver1) })).json();
  assert.equal(byName.invoices.length, 2);

  const byAmount = await (await fetch(`${srv.base}/api/invoices?minAmount=5000&maxAmount=15000`, { headers: auth(u.approver1) })).json();
  assert.equal(byAmount.invoices.length, 1);
  assert.equal(byAmount.invoices[0].counterpartyName, 'Beta Ab');
});

test('pending list excludes the approver\'s own already-recorded approvals (req 8)', async (t) => {
  const srv = await startServer();
  t.after(() => srv.close());
  const u = await seedUsers(srv.base);

  const { invoice } = await (await createInvoice(srv.base, u.submitter, { amount: 20000 })).json();
  // approver1 partially approves the >10k invoice.
  await post(srv.base, u.approver1, `/api/invoices/${invoice.id}/approve`);

  // It should no longer appear in approver1's pending list, but still in approver2's.
  const forA1 = await (await fetch(`${srv.base}/api/invoices`, { headers: auth(u.approver1) })).json();
  assert.equal(forA1.invoices.find((i) => i.id === invoice.id), undefined);

  const forA2 = await (await fetch(`${srv.base}/api/invoices`, { headers: auth(u.approver2) })).json();
  assert.ok(forA2.invoices.find((i) => i.id === invoice.id));
});
