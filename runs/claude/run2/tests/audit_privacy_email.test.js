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

test('every state change is recorded in an append-only audit log (req 6)', async (t) => {
  const srv = await startServer();
  t.after(() => srv.close());
  const u = await seedUsers(srv.base);

  const { invoice } = await (await createInvoice(srv.base, u.submitter, { amount: 500 })).json();
  await post(srv.base, u.approver1, `/api/invoices/${invoice.id}/approve`);

  const { entries } = await (await fetch(`${srv.base}/api/invoices/${invoice.id}/audit`, { headers: auth(u.approver1) })).json();
  const actions = entries.map((e) => e.action);
  assert.ok(actions.includes('invoice_submitted'));
  assert.ok(actions.includes('invoice_approved'));

  const approvedEntry = entries.find((e) => e.action === 'invoice_approved');
  assert.equal(approvedEntry.state_before, 'submitted');
  assert.equal(approvedEntry.state_after, 'approved');
  assert.ok(approvedEntry.actor_id);
  assert.ok(approvedEntry.created_at);
});

test('audit log is append-only: DB rejects updates and deletes (req 6)', async (t) => {
  const srv = await startServer();
  t.after(() => srv.close());
  const u = await seedUsers(srv.base);
  await (await createInvoice(srv.base, u.submitter, { amount: 500 })).json();

  const db = srv.app.locals.db;
  assert.throws(() => db.exec('UPDATE audit_log SET action = ? WHERE id = 1'.replace('?', "'x'")), /append-only/);
  assert.throws(() => db.exec('DELETE FROM audit_log WHERE id = 1'), /append-only/);
});

test('contact email: every read is audited and erasure works (req 7)', async (t) => {
  const srv = await startServer();
  t.after(() => srv.close());
  const u = await seedUsers(srv.base);

  const { invoice } = await (await createInvoice(srv.base, u.submitter, { amount: 500, email: 'gdpr@acme.test' })).json();

  // The email is NOT exposed via the ordinary invoice endpoint.
  const detail = await (await fetch(`${srv.base}/api/invoices/${invoice.id}`, { headers: auth(u.approver1) })).json();
  assert.equal(detail.invoice.counterpartyEmail, undefined);
  assert.equal(detail.invoice.counterpartyEmailPresent, true);

  // Reading via the dedicated endpoint returns the value and records an audit entry.
  const read = await (await fetch(`${srv.base}/api/invoices/${invoice.id}/contact-email`, { headers: auth(u.approver1) })).json();
  assert.equal(read.counterpartyEmail, 'gdpr@acme.test');

  let audit = await (await fetch(`${srv.base}/api/invoices/${invoice.id}/audit`, { headers: auth(u.approver1) })).json();
  const reads = audit.entries.filter((e) => e.action === 'contact_email_read');
  assert.equal(reads.length, 1);
  assert.ok(reads[0].actor_id, 'read access records the actor');

  // A second read is also recorded.
  await fetch(`${srv.base}/api/invoices/${invoice.id}/contact-email`, { headers: auth(u.approver2) });
  audit = await (await fetch(`${srv.base}/api/invoices/${invoice.id}/audit`, { headers: auth(u.approver1) })).json();
  assert.equal(audit.entries.filter((e) => e.action === 'contact_email_read').length, 2);

  // Erasure removes the value and is itself audited.
  const erase = await fetch(`${srv.base}/api/invoices/${invoice.id}/contact-email`, { method: 'DELETE', headers: auth(u.approver1) });
  assert.equal((await erase.json()).erased, true);

  const afterErase = await fetch(`${srv.base}/api/invoices/${invoice.id}/contact-email`, { headers: auth(u.approver1) });
  assert.equal(afterErase.status, 410);

  audit = await (await fetch(`${srv.base}/api/invoices/${invoice.id}/audit`, { headers: auth(u.approver1) })).json();
  assert.ok(audit.entries.some((e) => e.action === 'contact_email_erased'));
});

test('a notification email is written on every state change (req 9)', async (t) => {
  const srv = await startServer();
  t.after(() => srv.close());
  const u = await seedUsers(srv.base);

  const { invoice } = await (await createInvoice(srv.base, u.submitter, { amount: 12000 })).json();
  await post(srv.base, u.approver1, `/api/invoices/${invoice.id}/approve`); // partial, no state change
  await post(srv.base, u.approver2, `/api/invoices/${invoice.id}/approve`); // -> approved

  const outbox = srv.readOutbox();
  const subjects = outbox.map((m) => m.subject);
  assert.ok(subjects.some((s) => s.includes('submitted')));
  assert.ok(subjects.some((s) => s.includes('approved')));
  // All notifications go to the submitter.
  assert.ok(outbox.every((m) => m.to === 'sub@x.test'));
});
