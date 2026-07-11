'use strict';

const path = require('node:path');
const fs = require('node:fs');
const crypto = require('node:crypto');
const express = require('express');
const multer = require('multer');

const { openDb } = require('./db');
const { createMailer } = require('./email');
const { recordAudit } = require('./audit');
const {
  hashPassword,
  verifyPassword,
  signToken,
  verifyToken,
} = require('./auth');

// Amount (in cents) above which two distinct approvers are required (req 5).
const DUAL_APPROVAL_THRESHOLD_CENTS = 10000 * 100;

/**
 * Build a fully configured Express app.
 *
 * @param {object} opts
 * @param {string} opts.dbPath      SQLite path or ':memory:'
 * @param {string} opts.uploadDir   Directory for stored PDF attachments
 * @param {string} opts.outboxPath  File path for the email stub outbox
 * @param {string} opts.jwtSecret   Secret used to sign auth tokens
 */
function createApp(opts = {}) {
  const dbPath = opts.dbPath || path.join(__dirname, '..', 'data', 'app.db');
  const uploadDir = opts.uploadDir || path.join(__dirname, '..', 'data', 'uploads');
  const outboxPath = opts.outboxPath || path.join(__dirname, '..', 'data', 'outbox.log');
  const jwtSecret = opts.jwtSecret || process.env.JWT_SECRET || 'dev-insecure-secret';

  fs.mkdirSync(uploadDir, { recursive: true });

  const db = openDb(dbPath);
  const mailer = createMailer(outboxPath);

  const app = express();
  app.use(express.json());

  // ---- File upload (PDF only) --------------------------------------------
  const storage = multer.diskStorage({
    destination: (req, file, cb) => cb(null, uploadDir),
    filename: (req, file, cb) => {
      const unique = crypto.randomUUID();
      cb(null, `${unique}.pdf`);
    },
  });
  const upload = multer({
    storage,
    limits: { fileSize: 20 * 1024 * 1024 }, // 20 MB
    fileFilter: (req, file, cb) => {
      if (file.mimetype === 'application/pdf') return cb(null, true);
      cb(new Error('Only PDF attachments are allowed'));
    },
  });

  // ---- Helpers ------------------------------------------------------------
  const now = () => new Date().toISOString();

  function findUserById(id) {
    return db.prepare('SELECT id, email, role FROM users WHERE id = ?').get(id);
  }

  /** Auth middleware: verifies the bearer token and attaches req.user. */
  function authenticate(req, res, next) {
    const header = req.get('authorization') || '';
    const token = header.startsWith('Bearer ') ? header.slice(7) : null;
    const payload = token ? verifyToken(token, jwtSecret) : null;
    if (!payload || !payload.sub) {
      return res.status(401).json({ error: 'Authentication required' });
    }
    const user = findUserById(payload.sub);
    if (!user) return res.status(401).json({ error: 'Invalid token' });
    req.user = user;
    next();
  }

  function requireRole(role) {
    return (req, res, next) => {
      if (req.user.role !== role) {
        return res.status(403).json({ error: `Requires ${role} role` });
      }
      next();
    };
  }

  /** Serialize an invoice for API responses. Never exposes the contact email
   *  (personal data) here — that is only readable via the audited endpoint. */
  function serializeInvoice(inv) {
    const approvals = db
      .prepare('SELECT approver_id, created_at FROM approvals WHERE invoice_id = ? ORDER BY id')
      .all(inv.id);
    return {
      id: inv.id,
      amountEuros: inv.amount_cents / 100,
      counterpartyName: inv.counterparty_name,
      counterpartyEmailErased: !!inv.counterparty_email_erased,
      counterpartyEmailPresent: inv.counterparty_email != null,
      dueDate: inv.due_date,
      state: inv.state,
      submitterId: inv.submitter_id,
      requiredApprovals: inv.amount_cents > DUAL_APPROVAL_THRESHOLD_CENTS ? 2 : 1,
      approvals: approvals.map((a) => ({ approverId: a.approver_id, at: a.created_at })),
      pdfOriginalName: inv.pdf_original_name,
      createdAt: inv.created_at,
      updatedAt: inv.updated_at,
    };
  }

  function getInvoice(id) {
    return db.prepare('SELECT * FROM invoices WHERE id = ?').get(id);
  }

  function notifySubmitter(invoice, subject, body) {
    const submitter = db.prepare('SELECT email FROM users WHERE id = ?').get(invoice.submitter_id);
    if (submitter) mailer.send({ to: submitter.email, subject, body });
  }

  // ========================================================================
  // Auth
  // ========================================================================

  // Create a user. In a real deployment this would be admin-gated; for an
  // internal tool it doubles as the provisioning endpoint and is used by the
  // seed script and tests. Role is fixed at creation (req 1: exactly one role).
  app.post('/api/auth/register', (req, res) => {
    const { email, password, role } = req.body || {};
    if (!email || !password || !role) {
      return res.status(400).json({ error: 'email, password and role are required' });
    }
    if (role !== 'submitter' && role !== 'approver') {
      return res.status(400).json({ error: "role must be 'submitter' or 'approver'" });
    }
    try {
      const info = db
        .prepare('INSERT INTO users (email, password_hash, role, created_at) VALUES (?, ?, ?, ?)')
        .run(String(email).toLowerCase(), hashPassword(password), role, now());
      return res.status(201).json({ id: Number(info.lastInsertRowid), email, role });
    } catch (err) {
      if (String(err.message).includes('UNIQUE')) {
        return res.status(409).json({ error: 'A user with that email already exists' });
      }
      throw err;
    }
  });

  app.post('/api/auth/login', (req, res) => {
    const { email, password } = req.body || {};
    if (!email || !password) {
      return res.status(400).json({ error: 'email and password are required' });
    }
    const user = db
      .prepare('SELECT * FROM users WHERE email = ?')
      .get(String(email).toLowerCase());
    if (!user || !verifyPassword(password, user.password_hash)) {
      return res.status(401).json({ error: 'Invalid credentials' });
    }
    const token = signToken({ sub: user.id, role: user.role, iat: Date.now() }, jwtSecret);
    return res.json({ token, user: { id: user.id, email: user.email, role: user.role } });
  });

  app.get('/api/me', authenticate, (req, res) => {
    res.json({ user: req.user });
  });

  // ========================================================================
  // Invoices
  // ========================================================================

  // Create an invoice (submitter only, req 2).
  app.post(
    '/api/invoices',
    authenticate,
    requireRole('submitter'),
    upload.single('pdf'),
    (req, res) => {
      const { amountEuros, counterpartyName, counterpartyEmail, dueDate } = req.body || {};

      const cleanup = () => {
        if (req.file) fs.rm(req.file.path, () => {});
      };

      if (!req.file) {
        return res.status(400).json({ error: 'A PDF attachment (field "pdf") is required' });
      }
      const amountNum = Number(amountEuros);
      if (!Number.isFinite(amountNum) || amountNum <= 0) {
        cleanup();
        return res.status(400).json({ error: 'amountEuros must be a positive number' });
      }
      if (!counterpartyName || !String(counterpartyName).trim()) {
        cleanup();
        return res.status(400).json({ error: 'counterpartyName is required' });
      }
      if (!counterpartyEmail || !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(String(counterpartyEmail))) {
        cleanup();
        return res.status(400).json({ error: 'A valid counterpartyEmail is required' });
      }
      if (!dueDate || !/^\d{4}-\d{2}-\d{2}$/.test(String(dueDate)) || Number.isNaN(Date.parse(dueDate))) {
        cleanup();
        return res.status(400).json({ error: 'dueDate must be a valid YYYY-MM-DD date' });
      }

      const amountCents = Math.round(amountNum * 100);
      const ts = now();
      const info = db
        .prepare(`
          INSERT INTO invoices
            (amount_cents, counterparty_name, counterparty_email, due_date,
             pdf_path, pdf_original_name, submitter_id, state, created_at, updated_at)
          VALUES (?, ?, ?, ?, ?, ?, ?, 'submitted', ?, ?)
        `)
        .run(
          amountCents,
          String(counterpartyName).trim(),
          String(counterpartyEmail).trim(),
          String(dueDate),
          req.file.path,
          req.file.originalname,
          req.user.id,
          ts,
          ts,
        );

      const invoice = getInvoice(Number(info.lastInsertRowid));
      recordAudit(db, {
        invoiceId: invoice.id,
        actorId: req.user.id,
        action: 'invoice_submitted',
        stateBefore: null,
        stateAfter: 'submitted',
        details: `amount=${amountCents / 100} EUR`,
      });
      notifySubmitter(
        invoice,
        `Invoice #${invoice.id} submitted`,
        `Your invoice #${invoice.id} for ${amountCents / 100} EUR to ${invoice.counterparty_name} has been submitted.`,
      );
      return res.status(201).json({ invoice: serializeInvoice(invoice) });
    },
  );

  // List invoices.
  //  - approvers: pending invoices awaiting approval (req 8), filterable by
  //    counterpartyName (substring) and amount range (minAmount/maxAmount).
  //  - submitters: their own invoices.
  app.get('/api/invoices', authenticate, (req, res) => {
    const { counterpartyName, minAmount, maxAmount, state } = req.query;

    const clauses = [];
    const params = [];

    if (req.user.role === 'approver') {
      // Pending-for-me by default: submitted, not my own, not already approved by me.
      if (state) {
        clauses.push('state = ?');
        params.push(String(state));
      } else {
        clauses.push('state = ?');
        params.push('submitted');
        clauses.push('submitter_id != ?');
        params.push(req.user.id);
        clauses.push('id NOT IN (SELECT invoice_id FROM approvals WHERE approver_id = ?)');
        params.push(req.user.id);
      }
    } else {
      clauses.push('submitter_id = ?');
      params.push(req.user.id);
      if (state) {
        clauses.push('state = ?');
        params.push(String(state));
      }
    }

    if (counterpartyName) {
      clauses.push('counterparty_name LIKE ?');
      params.push(`%${counterpartyName}%`);
    }
    if (minAmount !== undefined && minAmount !== '') {
      clauses.push('amount_cents >= ?');
      params.push(Math.round(Number(minAmount) * 100));
    }
    if (maxAmount !== undefined && maxAmount !== '') {
      clauses.push('amount_cents <= ?');
      params.push(Math.round(Number(maxAmount) * 100));
    }

    const where = clauses.length ? `WHERE ${clauses.join(' AND ')}` : '';
    const rows = db
      .prepare(`SELECT * FROM invoices ${where} ORDER BY id DESC`)
      .all(...params);
    res.json({ invoices: rows.map(serializeInvoice) });
  });

  app.get('/api/invoices/:id', authenticate, (req, res) => {
    const invoice = getInvoice(Number(req.params.id));
    if (!invoice) return res.status(404).json({ error: 'Invoice not found' });
    res.json({ invoice: serializeInvoice(invoice) });
  });

  // Download the PDF attachment.
  app.get('/api/invoices/:id/pdf', authenticate, (req, res) => {
    const invoice = getInvoice(Number(req.params.id));
    if (!invoice) return res.status(404).json({ error: 'Invoice not found' });
    if (!fs.existsSync(invoice.pdf_path)) {
      return res.status(410).json({ error: 'Attachment no longer available' });
    }
    res.setHeader('Content-Type', 'application/pdf');
    res.setHeader(
      'Content-Disposition',
      `attachment; filename="${invoice.pdf_original_name.replace(/"/g, '')}"`,
    );
    fs.createReadStream(invoice.pdf_path).pipe(res);
  });

  // ---- Approve / reject (approver only, reqs 3, 4, 5) --------------------

  app.post('/api/invoices/:id/approve', authenticate, requireRole('approver'), (req, res) => {
    const invoice = getInvoice(Number(req.params.id));
    if (!invoice) return res.status(404).json({ error: 'Invoice not found' });
    if (invoice.state !== 'submitted') {
      return res.status(409).json({ error: `Invoice is already ${invoice.state}` });
    }
    if (invoice.submitter_id === req.user.id) {
      return res.status(403).json({ error: 'You cannot approve an invoice you submitted' });
    }

    // Record this approver's approval (idempotent per approver via UNIQUE).
    try {
      db.prepare('INSERT INTO approvals (invoice_id, approver_id, created_at) VALUES (?, ?, ?)')
        .run(invoice.id, req.user.id, now());
    } catch (err) {
      if (String(err.message).includes('UNIQUE')) {
        return res.status(409).json({ error: 'You have already approved this invoice' });
      }
      throw err;
    }

    const approvalCount = db
      .prepare('SELECT COUNT(*) AS n FROM approvals WHERE invoice_id = ?')
      .get(invoice.id).n;
    const required = invoice.amount_cents > DUAL_APPROVAL_THRESHOLD_CENTS ? 2 : 1;

    if (approvalCount >= required) {
      db.prepare('UPDATE invoices SET state = ?, updated_at = ? WHERE id = ?')
        .run('approved', now(), invoice.id);
      recordAudit(db, {
        invoiceId: invoice.id,
        actorId: req.user.id,
        action: 'invoice_approved',
        stateBefore: 'submitted',
        stateAfter: 'approved',
        details: `approvals=${approvalCount}/${required}`,
      });
      notifySubmitter(
        invoice,
        `Invoice #${invoice.id} approved`,
        `Your invoice #${invoice.id} has been approved (${approvalCount} of ${required} approvals).`,
      );
    } else {
      // Partial approval — not yet a state change, but recorded for the trail.
      recordAudit(db, {
        invoiceId: invoice.id,
        actorId: req.user.id,
        action: 'approval_recorded',
        stateBefore: 'submitted',
        stateAfter: 'submitted',
        details: `approvals=${approvalCount}/${required}`,
      });
    }

    return res.json({ invoice: serializeInvoice(getInvoice(invoice.id)) });
  });

  app.post('/api/invoices/:id/reject', authenticate, requireRole('approver'), (req, res) => {
    const invoice = getInvoice(Number(req.params.id));
    if (!invoice) return res.status(404).json({ error: 'Invoice not found' });
    if (invoice.state !== 'submitted') {
      return res.status(409).json({ error: `Invoice is already ${invoice.state}` });
    }
    if (invoice.submitter_id === req.user.id) {
      return res.status(403).json({ error: 'You cannot reject an invoice you submitted' });
    }

    db.prepare('UPDATE invoices SET state = ?, updated_at = ? WHERE id = ?')
      .run('rejected', now(), invoice.id);
    recordAudit(db, {
      invoiceId: invoice.id,
      actorId: req.user.id,
      action: 'invoice_rejected',
      stateBefore: 'submitted',
      stateAfter: 'rejected',
      details: req.body && req.body.reason ? `reason=${String(req.body.reason)}` : null,
    });
    notifySubmitter(
      invoice,
      `Invoice #${invoice.id} rejected`,
      `Your invoice #${invoice.id} has been rejected.`,
    );

    return res.json({ invoice: serializeInvoice(getInvoice(invoice.id)) });
  });

  // ========================================================================
  // Personal data: counterparty contact email (req 7)
  // ========================================================================

  // Read the contact email. EVERY read is recorded in the audit log.
  app.get('/api/invoices/:id/contact-email', authenticate, (req, res) => {
    const invoice = getInvoice(Number(req.params.id));
    if (!invoice) return res.status(404).json({ error: 'Invoice not found' });

    recordAudit(db, {
      invoiceId: invoice.id,
      actorId: req.user.id,
      action: 'contact_email_read',
      details: invoice.counterparty_email_erased ? 'email already erased' : 'email disclosed',
    });

    if (invoice.counterparty_email == null) {
      return res.status(410).json({ error: 'Contact email has been erased', erased: true });
    }
    return res.json({ counterpartyEmail: invoice.counterparty_email });
  });

  // Erase the contact email on request (right to erasure).
  app.delete('/api/invoices/:id/contact-email', authenticate, (req, res) => {
    const invoice = getInvoice(Number(req.params.id));
    if (!invoice) return res.status(404).json({ error: 'Invoice not found' });

    if (invoice.counterparty_email == null) {
      return res.json({ erased: true, alreadyErased: true });
    }
    db.prepare('UPDATE invoices SET counterparty_email = NULL, counterparty_email_erased = 1, updated_at = ? WHERE id = ?')
      .run(now(), invoice.id);
    recordAudit(db, {
      invoiceId: invoice.id,
      actorId: req.user.id,
      action: 'contact_email_erased',
      details: 'counterparty contact email erased on request',
    });
    return res.json({ erased: true });
  });

  // ========================================================================
  // Audit log (read-only; req 6)
  // ========================================================================

  app.get('/api/invoices/:id/audit', authenticate, (req, res) => {
    const invoice = getInvoice(Number(req.params.id));
    if (!invoice) return res.status(404).json({ error: 'Invoice not found' });
    const entries = db
      .prepare('SELECT * FROM audit_log WHERE invoice_id = ? ORDER BY id')
      .all(invoice.id);
    res.json({ entries });
  });

  app.get('/api/audit', authenticate, requireRole('approver'), (req, res) => {
    const entries = db.prepare('SELECT * FROM audit_log ORDER BY id DESC LIMIT 500').all();
    res.json({ entries });
  });

  // ---- Error handler (e.g. multer rejections) ----------------------------
  // eslint-disable-next-line no-unused-vars
  app.use((err, req, res, next) => {
    res.status(400).json({ error: err.message || 'Request failed' });
  });

  app.locals.db = db;
  app.locals.mailer = mailer;
  return app;
}

module.exports = { createApp, DUAL_APPROVAL_THRESHOLD_CENTS };
