'use strict';

const crypto = require('crypto');

// Password hashing using scrypt (built into Node, no external dependency).
// Stored format: scrypt$<saltHex>$<hashHex>
function hashPassword(password) {
  const salt = crypto.randomBytes(16);
  const hash = crypto.scryptSync(password, salt, 64);
  return `scrypt$${salt.toString('hex')}$${hash.toString('hex')}`;
}

function verifyPassword(password, stored) {
  const parts = String(stored).split('$');
  if (parts.length !== 3 || parts[0] !== 'scrypt') return false;
  const salt = Buffer.from(parts[1], 'hex');
  const expected = Buffer.from(parts[2], 'hex');
  const actual = crypto.scryptSync(password, salt, expected.length);
  return expected.length === actual.length && crypto.timingSafeEqual(expected, actual);
}

function newToken() {
  return crypto.randomBytes(32).toString('hex');
}

// Express middleware factory: resolves the bearer token to a user.
function authMiddleware(db) {
  const findSession = db.prepare(`
    SELECT u.id, u.email, u.role
    FROM sessions s JOIN users u ON u.id = s.user_id
    WHERE s.token = ?
  `);
  return (req, res, next) => {
    const header = req.get('authorization') || '';
    const match = header.match(/^Bearer\s+(.+)$/i);
    if (match) {
      const user = findSession.get(match[1]);
      if (user) req.user = user;
    }
    next();
  };
}

function requireAuth(req, res, next) {
  if (!req.user) return res.status(401).json({ error: 'authentication required' });
  next();
}

function requireRole(role) {
  return (req, res, next) => {
    if (!req.user) return res.status(401).json({ error: 'authentication required' });
    if (req.user.role !== role) {
      return res.status(403).json({ error: `requires role: ${role}` });
    }
    next();
  };
}

module.exports = {
  hashPassword,
  verifyPassword,
  newToken,
  authMiddleware,
  requireAuth,
  requireRole,
};
