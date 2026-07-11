'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { startServer, seedUsers, createInvoice, auth } = require('./helpers');

test('invoice creation requires submitter role and a valid PDF (req 2)', async (t) => {
  const srv = await startServer();
  t.after(() => srv.close());
  const u = await seedUsers(srv.base);

  // Approver cannot submit.
  const asApprover = await createInvoice(srv.base, u.approver1, { amount: 100 });
  assert.equal(asApprover.status, 403);

  // Submitter succeeds and starts in 'submitted'.
  const ok = await createInvoice(srv.base, u.submitter, { amount: 100 });
  assert.equal(ok.status, 201);
  const { invoice } = await ok.json();
  assert.equal(invoice.state, 'submitted');
  assert.equal(invoice.amountEuros, 100);
  assert.equal(invoice.requiredApprovals, 1);

  // Missing PDF is rejected.
  const form = new FormData();
  form.append('amountEuros', '50');
  form.append('counterpartyName', 'X');
  form.append('counterpartyEmail', 'x@y.test');
  form.append('dueDate', '2026-10-10');
  const noPdf = await fetch(`${srv.base}/api/invoices`, {
    method: 'POST', headers: auth(u.submitter), body: form,
  });
  assert.equal(noPdf.status, 400);
});

test('invoice creation validates fields', async (t) => {
  const srv = await startServer();
  t.after(() => srv.close());
  const u = await seedUsers(srv.base);

  const badAmount = await createInvoice(srv.base, u.submitter, { amount: -5 });
  assert.equal(badAmount.status, 400);

  const badEmail = await createInvoice(srv.base, u.submitter, { amount: 5, email: 'not-an-email' });
  assert.equal(badEmail.status, 400);

  const badDate = await createInvoice(srv.base, u.submitter, { amount: 5, due: '01/02/2026' });
  assert.equal(badDate.status, 400);
});

test('submitters see only their own invoices; PDF is downloadable', async (t) => {
  const srv = await startServer();
  t.after(() => srv.close());
  const u = await seedUsers(srv.base);

  const r = await createInvoice(srv.base, u.submitter, { amount: 200 });
  const { invoice } = await r.json();
  await createInvoice(srv.base, u.submitter2, { amount: 300 });

  const mine = await (await fetch(`${srv.base}/api/invoices`, { headers: auth(u.submitter) })).json();
  assert.equal(mine.invoices.length, 1);
  assert.equal(mine.invoices[0].id, invoice.id);

  const pdf = await fetch(`${srv.base}/api/invoices/${invoice.id}/pdf`, { headers: auth(u.submitter) });
  assert.equal(pdf.status, 200);
  assert.equal(pdf.headers.get('content-type'), 'application/pdf');
});
