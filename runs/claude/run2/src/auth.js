'use strict';

const crypto = require('node:crypto');

// ---------------------------------------------------------------------------
// Password hashing (scrypt via node:crypto — no native dependency required).
// Stored format: scrypt$<saltHex>$<hashHex>
// ---------------------------------------------------------------------------

function hashPassword(password) {
  const salt = crypto.randomBytes(16);
  const derived = crypto.scryptSync(String(password), salt, 64);
  return `scrypt$${salt.toString('hex')}$${derived.toString('hex')}`;
}

function verifyPassword(password, stored) {
  if (typeof stored !== 'string') return false;
  const parts = stored.split('$');
  if (parts.length !== 3 || parts[0] !== 'scrypt') return false;
  const salt = Buffer.from(parts[1], 'hex');
  const expected = Buffer.from(parts[2], 'hex');
  const derived = crypto.scryptSync(String(password), salt, expected.length);
  return crypto.timingSafeEqual(expected, derived);
}

// ---------------------------------------------------------------------------
// Stateless auth tokens: base64url(payload).base64url(hmac-sha256(payload))
// Signed with a server secret. Keeps the service dependency-free while still
// providing tamper-evident sessions.
// ---------------------------------------------------------------------------

function b64url(buf) {
  return Buffer.from(buf).toString('base64url');
}

function signToken(payload, secret) {
  const body = b64url(JSON.stringify(payload));
  const sig = crypto.createHmac('sha256', secret).update(body).digest('base64url');
  return `${body}.${sig}`;
}

function verifyToken(token, secret) {
  if (typeof token !== 'string' || !token.includes('.')) return null;
  const [body, sig] = token.split('.');
  const expected = crypto.createHmac('sha256', secret).update(body).digest('base64url');
  const a = Buffer.from(sig || '', 'utf8');
  const b = Buffer.from(expected, 'utf8');
  if (a.length !== b.length || !crypto.timingSafeEqual(a, b)) return null;
  try {
    return JSON.parse(Buffer.from(body, 'base64url').toString('utf8'));
  } catch {
    return null;
  }
}

module.exports = { hashPassword, verifyPassword, signToken, verifyToken };
