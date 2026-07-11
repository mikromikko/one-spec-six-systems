'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { startServer, register, login, auth } = require('./helpers');

test('auth: sign in with email/password and enforce a single role (req 1)', async (t) => {
  const srv = await startServer();
  t.after(() => srv.close());

  const r = await register(srv.base, 'a@x.test', 'secret', 'approver');
  assert.equal(r.status, 201);

  // Wrong password is rejected.
  const bad = await fetch(`${srv.base}/api/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email: 'a@x.test', password: 'wrong' }),
  });
  assert.equal(bad.status, 401);

  // Correct password returns a token and the user's single role.
  const token = await login(srv.base, 'a@x.test', 'secret');
  assert.ok(token);
  const me = await fetch(`${srv.base}/api/me`, { headers: auth(token) });
  const body = await me.json();
  assert.equal(body.user.role, 'approver');
  assert.equal(body.user.email, 'a@x.test');
});

test('auth: invalid role is rejected and duplicate email conflicts', async (t) => {
  const srv = await startServer();
  t.after(() => srv.close());

  const badRole = await register(srv.base, 'b@x.test', 'secret', 'admin');
  assert.equal(badRole.status, 400);

  await register(srv.base, 'c@x.test', 'secret', 'submitter');
  const dup = await register(srv.base, 'c@x.test', 'secret', 'submitter');
  assert.equal(dup.status, 409);
});

test('auth: protected routes require a valid token', async (t) => {
  const srv = await startServer();
  t.after(() => srv.close());

  const noToken = await fetch(`${srv.base}/api/invoices`);
  assert.equal(noToken.status, 401);

  const badToken = await fetch(`${srv.base}/api/invoices`, { headers: auth('garbage.sig') });
  assert.equal(badToken.status, 401);
});
