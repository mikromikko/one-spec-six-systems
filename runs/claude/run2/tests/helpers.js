'use strict';

const os = require('node:os');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { createApp } = require('../src/app');

// Spin up an isolated app instance (temp dir + in-memory DB) on an ephemeral
// port, returning a small client bound to fetch.
async function startServer() {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'invoice-test-'));
  const outboxPath = path.join(tmp, 'outbox.log');
  const app = createApp({
    dbPath: ':memory:',
    uploadDir: path.join(tmp, 'uploads'),
    outboxPath,
    jwtSecret: 'test-secret',
  });

  const server = await new Promise((resolve) => {
    const s = app.listen(0, () => resolve(s));
  });
  const base = `http://127.0.0.1:${server.address().port}`;

  async function close() {
    await new Promise((r) => server.close(r));
    fs.rmSync(tmp, { recursive: true, force: true });
  }

  function readOutbox() {
    if (!fs.existsSync(outboxPath)) return [];
    return fs
      .readFileSync(outboxPath, 'utf8')
      .split('\n')
      .filter(Boolean)
      .map((l) => JSON.parse(l));
  }

  return { base, close, readOutbox, app };
}

async function register(base, email, password, role) {
  const res = await fetch(`${base}/api/auth/register`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password, role }),
  });
  return res;
}

async function login(base, email, password) {
  const res = await fetch(`${base}/api/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password }),
  });
  const data = await res.json();
  return data.token;
}

// Create a submitter + one or two approvers and return their tokens.
async function seedUsers(base) {
  await register(base, 'sub@x.test', 'pw-submitter', 'submitter');
  await register(base, 'sub2@x.test', 'pw-submitter', 'submitter');
  await register(base, 'app1@x.test', 'pw-approver', 'approver');
  await register(base, 'app2@x.test', 'pw-approver', 'approver');
  return {
    submitter: await login(base, 'sub@x.test', 'pw-submitter'),
    submitter2: await login(base, 'sub2@x.test', 'pw-submitter'),
    approver1: await login(base, 'app1@x.test', 'pw-approver'),
    approver2: await login(base, 'app2@x.test', 'pw-approver'),
  };
}

// Submit an invoice via multipart form-data with a small fake PDF.
async function createInvoice(base, token, { amount, name = 'Acme Oy', email = 'billing@acme.test', due = '2026-12-01' }) {
  const form = new FormData();
  form.append('amountEuros', String(amount));
  form.append('counterpartyName', name);
  form.append('counterpartyEmail', email);
  form.append('dueDate', due);
  const pdf = Buffer.concat([Buffer.from('%PDF-1.4\n'), crypto.randomBytes(64)]);
  form.append('pdf', new Blob([pdf], { type: 'application/pdf' }), 'invoice.pdf');
  const res = await fetch(`${base}/api/invoices`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}` },
    body: form,
  });
  return res;
}

function auth(token) {
  return { Authorization: `Bearer ${token}` };
}

module.exports = { startServer, register, login, seedUsers, createInvoice, auth };
