import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { afterEach, beforeEach, describe, it } from 'node:test';
import { createServer } from '../src/service.js';

const pdfBase64 = Buffer.from('%PDF-1.4\n% test invoice\n').toString('base64');

let server;
let baseUrl;
let dataDir;

async function request(method, pathname, token, body) {
  const response = await fetch(`${baseUrl}${pathname}`, {
    method,
    headers: {
      ...(token ? { authorization: `Bearer ${token}` } : {}),
      ...(body ? { 'content-type': 'application/json' } : {})
    },
    body: body ? JSON.stringify(body) : undefined
  });
  const payload = await response.json();
  return { status: response.status, payload };
}

async function login(email, password) {
  const response = await request('POST', '/auth/login', null, { email, password });
  assert.equal(response.status, 200);
  return response.payload.token;
}

async function createInvoice(token, overrides = {}) {
  const response = await request('POST', '/invoices', token, {
    amountEuros: '100.00',
    counterpartyName: 'ACME Supplies',
    counterpartyContactEmail: 'contact@acme.example',
    dueDate: '2026-08-01',
    pdfFileName: 'invoice.pdf',
    pdfBase64,
    ...overrides
  });
  assert.equal(response.status, 201);
  return response.payload.invoice;
}

beforeEach(async () => {
  dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'invoice-service-'));
  server = createServer({ dataDir });
  await new Promise((resolve) => server.listen(0, resolve));
  baseUrl = `http://127.0.0.1:${server.address().port}`;
});

afterEach(async () => {
  await new Promise((resolve) => server.close(resolve));
  fs.rmSync(dataDir, { recursive: true, force: true });
});

describe('invoice approval service', () => {
  it('authenticates seeded users and rejects invalid passwords', async () => {
    const ok = await request('POST', '/auth/login', null, {
      email: 'submitter@nordvik.example',
      password: 'submitter-password'
    });
    assert.equal(ok.status, 200);
    assert.equal(ok.payload.user.role, 'submitter');

    const bad = await request('POST', '/auth/login', null, {
      email: 'submitter@nordvik.example',
      password: 'wrong'
    });
    assert.equal(bad.status, 401);
  });

  it('lets submitters create invoices with PDF attachments and audit/email side effects', async () => {
    const submitter = await login('submitter@nordvik.example', 'submitter-password');
    const invoice = await createInvoice(submitter);

    assert.equal(invoice.state, 'submitted');
    assert.equal(invoice.counterpartyContactEmail, 'contact@acme.example');
    assert.ok(fs.existsSync(path.join(dataDir, 'attachments', `${invoice.id}.pdf`)));

    const audit = await request('GET', `/audit?invoiceId=${invoice.id}`, submitter);
    assert.equal(audit.status, 200);
    assert.equal(audit.payload.auditLog[0].action, 'submit_invoice');
    assert.equal(audit.payload.auditLog[0].stateBefore, null);
    assert.equal(audit.payload.auditLog[0].stateAfter, 'submitted');

    const emails = fs.readFileSync(path.join(dataDir, 'emails.log'), 'utf8').trim().split('\n');
    assert.equal(JSON.parse(emails[0]).to, 'submitter@nordvik.example');
  });

  it('prevents submitters from approving and prevents self-approval by approvers', async () => {
    const submitter = await login('submitter@nordvik.example', 'submitter-password');
    const invoice = await createInvoice(submitter);

    const submitterApproval = await request('POST', `/invoices/${invoice.id}/approve`, submitter);
    assert.equal(submitterApproval.status, 403);

    await new Promise((resolve) => server.close(resolve));
    const db = JSON.parse(fs.readFileSync(path.join(dataDir, 'db.json'), 'utf8'));
    const approverUser = db.users.find((user) => user.email === 'approver1@nordvik.example');
    const seeded = db.invoices[0];
    seeded.submitterId = approverUser.id;
    fs.writeFileSync(path.join(dataDir, 'db.json'), JSON.stringify(db, null, 2));
    server = createServer({ dataDir });
    await new Promise((resolve) => server.listen(0, resolve));
    baseUrl = `http://127.0.0.1:${server.address().port}`;

    const approver = await login('approver1@nordvik.example', 'approver-password');
    const selfApproval = await request('POST', `/invoices/${invoice.id}/approve`, approver);
    assert.equal(selfApproval.status, 403);
  });

  it('requires one approval at or under 10000 euros', async () => {
    const submitter = await login('submitter@nordvik.example', 'submitter-password');
    const approver = await login('approver1@nordvik.example', 'approver-password');
    const invoice = await createInvoice(submitter, { amountEuros: '10000.00' });

    const approval = await request('POST', `/invoices/${invoice.id}/approve`, approver);
    assert.equal(approval.status, 200);
    assert.equal(approval.payload.invoice.state, 'approved');
    assert.equal(approval.payload.invoice.approvals.length, 1);
  });

  it('requires two distinct approvals over 10000 euros', async () => {
    const submitter = await login('submitter@nordvik.example', 'submitter-password');
    const approver1 = await login('approver1@nordvik.example', 'approver-password');
    const approver2 = await login('approver2@nordvik.example', 'approver-password');
    const invoice = await createInvoice(submitter, { amountEuros: '10000.01' });

    const first = await request('POST', `/invoices/${invoice.id}/approve`, approver1);
    assert.equal(first.status, 200);
    assert.equal(first.payload.invoice.state, 'submitted');
    assert.equal(first.payload.invoice.approvals.length, 1);

    const duplicate = await request('POST', `/invoices/${invoice.id}/approve`, approver1);
    assert.equal(duplicate.status, 409);

    const second = await request('POST', `/invoices/${invoice.id}/approve`, approver2);
    assert.equal(second.status, 200);
    assert.equal(second.payload.invoice.state, 'approved');
    assert.equal(second.payload.invoice.approvals.length, 2);
  });

  it('lets approvers reject submitted invoices and notifies submitters', async () => {
    const submitter = await login('submitter@nordvik.example', 'submitter-password');
    const approver = await login('approver1@nordvik.example', 'approver-password');
    const invoice = await createInvoice(submitter);

    const rejection = await request('POST', `/invoices/${invoice.id}/reject`, approver);
    assert.equal(rejection.status, 200);
    assert.equal(rejection.payload.invoice.state, 'rejected');

    const emails = fs.readFileSync(path.join(dataDir, 'emails.log'), 'utf8').trim().split('\n').map(JSON.parse);
    assert.equal(emails.at(-1).subject, `Invoice ${invoice.id} is now rejected`);
  });

  it('lists pending approvals with counterparty and amount filters', async () => {
    const submitter = await login('submitter@nordvik.example', 'submitter-password');
    const approver1 = await login('approver1@nordvik.example', 'approver-password');
    const approver2 = await login('approver2@nordvik.example', 'approver-password');
    const acme = await createInvoice(submitter, { amountEuros: '12000.00', counterpartyName: 'ACME Supplies' });
    await createInvoice(submitter, { amountEuros: '50.00', counterpartyName: 'Globex' });

    const filtered = await request('GET', '/invoices/pending?counterpartyName=acme&minAmount=10000&maxAmount=13000', approver1);
    assert.equal(filtered.status, 200);
    assert.deepEqual(filtered.payload.invoices.map((invoice) => invoice.id), [acme.id]);
    assert.equal(filtered.payload.invoices[0].counterpartyContactEmail, undefined);

    await request('POST', `/invoices/${acme.id}/approve`, approver1);
    const afterApproval = await request('GET', '/invoices/pending', approver1);
    assert.equal(afterApproval.payload.invoices.some((invoice) => invoice.id === acme.id), false);

    const otherApprover = await request('GET', '/invoices/pending', approver2);
    assert.equal(otherApprover.payload.invoices.some((invoice) => invoice.id === acme.id), true);
  });

  it('records every read access to counterparty contact email and can erase it', async () => {
    const submitter = await login('submitter@nordvik.example', 'submitter-password');
    const approver = await login('approver1@nordvik.example', 'approver-password');
    const invoice = await createInvoice(submitter);

    const read = await request('GET', `/invoices/${invoice.id}`, approver);
    assert.equal(read.status, 200);
    assert.equal(read.payload.invoice.counterpartyContactEmail, 'contact@acme.example');

    const erase = await request('POST', `/invoices/${invoice.id}/erase-counterparty-contact`, submitter);
    assert.equal(erase.status, 200);
    assert.equal(erase.payload.invoice.counterpartyContactEmail, null);

    const readAfterErase = await request('GET', `/invoices/${invoice.id}`, approver);
    assert.equal(readAfterErase.payload.invoice.counterpartyContactEmail, null);

    const audit = await request('GET', `/audit?invoiceId=${invoice.id}`, submitter);
    const actions = audit.payload.auditLog.map((entry) => entry.action);
    assert.equal(actions.filter((action) => action === 'read_counterparty_contact_email').length, 2);
    assert.ok(actions.includes('erase_counterparty_contact_email'));
  });
});
