import { scryptSync, randomBytes, timingSafeEqual } from 'node:crypto';

// Password hashing using scrypt from the standard library (no native deps).
// Stored format: scrypt$<saltHex>$<hashHex>
export function hashPassword(password) {
  const salt = randomBytes(16);
  const hash = scryptSync(password, salt, 64);
  return `scrypt$${salt.toString('hex')}$${hash.toString('hex')}`;
}

export function verifyPassword(password, stored) {
  const parts = String(stored).split('$');
  if (parts.length !== 3 || parts[0] !== 'scrypt') return false;
  const salt = Buffer.from(parts[1], 'hex');
  const expected = Buffer.from(parts[2], 'hex');
  const actual = scryptSync(password, salt, expected.length);
  return actual.length === expected.length && timingSafeEqual(actual, expected);
}

// Express middleware factories --------------------------------------------

export function requireAuth(db) {
  return (req, res, next) => {
    const userId = req.session?.userId;
    if (!userId) return res.status(401).json({ error: 'authentication required' });
    const user = db.prepare('SELECT id, email, role FROM users WHERE id = ?').get(userId);
    if (!user) return res.status(401).json({ error: 'authentication required' });
    req.user = user;
    next();
  };
}

export function requireRole(role) {
  return (req, res, next) => {
    if (req.user?.role !== role) {
      return res.status(403).json({ error: `requires role: ${role}` });
    }
    next();
  };
}
