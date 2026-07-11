'use strict';

const path = require('path');
const fs = require('fs');
const express = require('express');
const multer = require('multer');

const { createDb, transaction } = require('./db');
const {
  hashPassword,
  verifyPassword,
  newToken,
  authMiddleware,
  requireAuth,
  requireRole,
} = require('./auth');
const { sendEmail } = require('./email');

const APPROVAL_THRESHOLD_CENTS = 1000000; // 10,000.00 EUR

function now() {
  return new Date().toISOString();
}

// ---- validation helpers -------------------------------------------------

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

function parseAmountToCents(value) {
  if (value === undefined || value === null || value === '') return null;
  const num = Number(value);
  if (!Number.isFinite(num) || num <= 0) return null;
  // Round to cents to avoid floating point noise.
  return Math.round(num * 100);
}

function isValidDate(value) {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const d = new Date(value + 'T00:00:00Z');
  return !Number.isNaN(d.getTime());
}

// ---- app factory --------------------------------------------------------

function createApp(options = {}) {
  const db = options.db || createDb();
  const uploadDir = options.uploadDir || process.env.UPLOAD_DIR || path.join(__dirname, '..', 'data', 'uploads');
  if (!fs.existsSync(uploadDir)) fs.mkdirSync(uploadDir, { recursive: true });

  const storage = multer.diskStorage({
    destination: (req, file, cb) => cb(null, uploadDir),
    filename: (req, file, cb) => {
      const safe = Date.now() + '-' + Math.random().toString(16).slice(2) + '.pdf';
      cb(null, safe);
    },
  });
  const upload = multer({
    storage,
    limits: { fileSize: 20 * 1024 * 1024 },
    fileFilter: (req, file, cb) => {
      const isPdf =
        file.mimetype === 'application/pdf' ||
        path.extname(file.originalname).toLowerCase() === '.pdf';
      if (!isPdf) return cb(new Error('attachment must be a PDF'));
      cb(null, true);
    },
  });

  const app = express();
  app.use(express.json());
  app.use(authMiddleware(db));

  // ---- prepared statements ----------------------------------------------
  const stmt = {
    userByEmail: db.prepare('SELECT * FROM users WHERE email = ?'),
    insertSession: db.prepare('INSERT INTO sessions (token, user_id, created_at) VALUES (?, ?, ?)'),
    deleteSession: db.prepare('DELETE FROM sessions WHERE token = ?'),
    insertInvoice: db.prepare(`
      INSERT INTO invoices
        (submitter_id, amount_cents, counterparty_name, counterparty_email, due_date, pdf_path, state, created_at)
      VALUES (?, ?, ?, ?, ?, ?, 'submitted', ?)
    `),
    invoiceById: db.prepare('SELECT * FROM invoices WHERE id = ?'),
    setInvoiceState: db.prepare('UPDATE invoices SET state = ? WHERE id = ?'),
    eraseEmail: db.prepare('UPDATE invoices SET counterparty_email = NULL, email_erased = 1 WHERE id = ?'),
    insertApproval: db.prepare('INSERT INTO approvals (invoice_id, approver_id, created_at) VALUES (?, ?, ?)'),
    countApprovals: db.prepare('SELECT COUNT(*) AS n FROM approvals WHERE invoice_id = ?'),
    hasApproved: db.prepare('SELECT 1 FROM approvals WHERE invoice_id = ? AND approver_id = ?'),
    insertAudit: db.prepare(`
      INSERT INTO audit_log (invoice_id, actor_id, action, state_before, state_after, detail, created_at)
      VALUES (?, ?, ?, ?, ?, ?, ?)
    `),
    auditForInvoice: db.prepare('SELECT * FROM audit_log WHERE invoice_id = ? ORDER BY id ASC'),
  };

  function audit({ invoiceId, actorId, action, before = null, after = null, detail = null }) {
    stmt.insertAudit.run(invoiceId, actorId, action, before, after, detail, now());
  }

  function publicInvoice(inv) {
    return {
      id: inv.id,
      submitterId: inv.submitter_id,
      amountEuros: inv.amount_cents / 100,
      counterpartyName: inv.counterparty_name,
      counterpartyEmailErased: !!inv.email_erased,
      counterpartyEmailPresent: inv.counterparty_email !== null,
      dueDate: inv.due_date,
      state: inv.state,
      requiredApprovals: inv.amount_cents > APPROVAL_THRESHOLD_CENTS ? 2 : 1,
      approvalsCount: stmt.countApprovals.get(inv.id).n,
      createdAt: inv.created_at,
    };
  }

  function notifySubmitter(inv, before, after, actorEmail) {
    const submitter = db.prepare('SELECT email FROM users WHERE id = ?').get(inv.submitter_id);
    sendEmail({
      to: submitter.email,
      subject: `Invoice #${inv.id} is now ${after}`,
      body:
        `Your invoice #${inv.id} (counterparty: ${inv.counterparty_name}, ` +
        `amount: EUR ${(inv.amount_cents / 100).toFixed(2)}) changed state ` +
        `from "${before}" to "${after}"` +
        (actorEmail ? ` by ${actorEmail}.` : '.'),
    });
  }

  // ---- auth routes ------------------------------------------------------

  app.post('/login', (req, res) => {
    const { email, password } = req.body || {};
    if (!email || !password) return res.status(400).json({ error: 'email and password required' });
    const user = stmt.userByEmail.get(String(email).toLowerCase());
    if (!user || !verifyPassword(password, user.password_hash)) {
      return res.status(401).json({ error: 'invalid credentials' });
    }
    const token = newToken();
    stmt.insertSession.run(token, user.id, now());
    res.json({ token, role: user.role, email: user.email });
  });

  app.post('/logout', requireAuth, (req, res) => {
    const header = req.get('authorization') || '';
    const match = header.match(/^Bearer\s+(.+)$/i);
    if (match) stmt.deleteSession.run(match[1]);
    res.json({ ok: true });
  });

  app.get('/me', requireAuth, (req, res) => {
    res.json({ id: req.user.id, email: req.user.email, role: req.user.role });
  });

  // ---- invoice creation (submitter) -------------------------------------

  app.post('/invoices', requireAuth, requireRole('submitter'), (req, res) => {
    upload.single('pdf')(req, res, (err) => {
      if (err) return res.status(400).json({ error: err.message });
      if (!req.file) return res.status(400).json({ error: 'PDF attachment (field "pdf") is required' });

      const cleanup = () => fs.existsSync(req.file.path) && fs.unlinkSync(req.file.path);

      const amountCents = parseAmountToCents(req.body.amount);
      if (amountCents === null) { cleanup(); return res.status(400).json({ error: 'amount must be a positive number of euros' }); }

      const name = (req.body.counterpartyName || '').trim();
      if (!name) { cleanup(); return res.status(400).json({ error: 'counterpartyName is required' }); }

      const email = (req.body.counterpartyEmail || '').trim();
      if (!EMAIL_RE.test(email)) { cleanup(); return res.status(400).json({ error: 'counterpartyEmail must be a valid email' }); }

      const dueDate = (req.body.dueDate || '').trim();
      if (!isValidDate(dueDate)) { cleanup(); return res.status(400).json({ error: 'dueDate must be a valid YYYY-MM-DD date' }); }

      const info = stmt.insertInvoice.run(
        req.user.id, amountCents, name, email, dueDate, req.file.path, now()
      );
      const inv = stmt.invoiceById.get(info.lastInsertRowid);
      audit({
        invoiceId: inv.id,
        actorId: req.user.id,
        action: 'submit',
        before: null,
        after: 'submitted',
        detail: `amount_cents=${amountCents}`,
      });
      res.status(201).json(publicInvoice(inv));
    });
  });

  // ---- list invoices ----------------------------------------------------

  // Approvers: invoices pending THEIR approval, filterable by counterparty name and amount.
  app.get('/invoices/pending', requireAuth, requireRole('approver'), (req, res) => {
    const filters = ['i.state = \'submitted\'', 'i.submitter_id != @uid'];
    const params = { uid: req.user.id };

    // Exclude invoices this approver has already approved.
    filters.push('NOT EXISTS (SELECT 1 FROM approvals a WHERE a.invoice_id = i.id AND a.approver_id = @uid)');

    if (req.query.counterparty) {
      filters.push('i.counterparty_name LIKE @name');
      params.name = '%' + req.query.counterparty + '%';
    }
    const minCents = parseAmountToCents(req.query.minAmount);
    if (minCents !== null) { filters.push('i.amount_cents >= @minC'); params.minC = minCents; }
    const maxCents = parseAmountToCents(req.query.maxAmount);
    if (maxCents !== null) { filters.push('i.amount_cents <= @maxC'); params.maxC = maxCents; }

    const rows = db
      .prepare(`SELECT i.* FROM invoices i WHERE ${filters.join(' AND ')} ORDER BY i.id ASC`)
      .all(params);
    res.json(rows.map(publicInvoice));
  });

  // Any authenticated user can list invoices they are involved with.
  app.get('/invoices', requireAuth, (req, res) => {
    let rows;
    if (req.user.role === 'submitter') {
      rows = db.prepare('SELECT * FROM invoices WHERE submitter_id = ? ORDER BY id ASC').all(req.user.id);
    } else {
      rows = db.prepare('SELECT * FROM invoices ORDER BY id ASC').all();
    }
    res.json(rows.map(publicInvoice));
  });

  app.get('/invoices/:id', requireAuth, (req, res) => {
    const inv = stmt.invoiceById.get(req.params.id);
    if (!inv) return res.status(404).json({ error: 'not found' });
    res.json(publicInvoice(inv));
  });

  // ---- approve / reject (approver) --------------------------------------

  app.post('/invoices/:id/approve', requireAuth, requireRole('approver'), (req, res) => {
    const inv = stmt.invoiceById.get(req.params.id);
    if (!inv) return res.status(404).json({ error: 'not found' });
    if (inv.state !== 'submitted') return res.status(409).json({ error: `invoice is already ${inv.state}` });
    if (inv.submitter_id === req.user.id) {
      return res.status(403).json({ error: 'you cannot approve an invoice you submitted' });
    }
    if (stmt.hasApproved.get(inv.id, req.user.id)) {
      return res.status(409).json({ error: 'you have already approved this invoice' });
    }

    const result = transaction(db, () => {
      stmt.insertApproval.run(inv.id, req.user.id, now());
      const count = stmt.countApprovals.get(inv.id).n;
      const required = inv.amount_cents > APPROVAL_THRESHOLD_CENTS ? 2 : 1;
      const before = inv.state;

      if (count >= required) {
        stmt.setInvoiceState.run('approved', inv.id);
        audit({
          invoiceId: inv.id, actorId: req.user.id, action: 'approve',
          before, after: 'approved',
          detail: `approvals=${count}/${required}`,
        });
        return { stateChanged: true, after: 'approved', count, required };
      }
      // Partial approval: state unchanged, but still recorded in the audit log.
      audit({
        invoiceId: inv.id, actorId: req.user.id, action: 'approve_partial',
        before, after: before,
        detail: `approvals=${count}/${required}`,
      });
      return { stateChanged: false, after: before, count, required };
    })();

    if (result.stateChanged) {
      notifySubmitter(inv, 'submitted', 'approved', req.user.email);
    }
    res.json(publicInvoice(stmt.invoiceById.get(inv.id)));
  });

  app.post('/invoices/:id/reject', requireAuth, requireRole('approver'), (req, res) => {
    const inv = stmt.invoiceById.get(req.params.id);
    if (!inv) return res.status(404).json({ error: 'not found' });
    if (inv.state !== 'submitted') return res.status(409).json({ error: `invoice is already ${inv.state}` });
    if (inv.submitter_id === req.user.id) {
      return res.status(403).json({ error: 'you cannot reject an invoice you submitted' });
    }

    transaction(db, () => {
      stmt.setInvoiceState.run('rejected', inv.id);
      audit({
        invoiceId: inv.id, actorId: req.user.id, action: 'reject',
        before: 'submitted', after: 'rejected',
        detail: req.body && req.body.reason ? `reason=${String(req.body.reason)}` : null,
      });
    })();

    notifySubmitter(inv, 'submitted', 'rejected', req.user.email);
    res.json(publicInvoice(stmt.invoiceById.get(inv.id)));
  });

  // ---- personal data: counterparty email --------------------------------

  // Reading the counterparty email is a personal-data access and is audited.
  app.get('/invoices/:id/counterparty-email', requireAuth, (req, res) => {
    const inv = stmt.invoiceById.get(req.params.id);
    if (!inv) return res.status(404).json({ error: 'not found' });

    audit({
      invoiceId: inv.id, actorId: req.user.id, action: 'read_counterparty_email',
      detail: inv.email_erased ? 'erased' : 'present',
    });

    if (inv.email_erased || inv.counterparty_email === null) {
      return res.status(410).json({ error: 'counterparty email has been erased', erased: true });
    }
    res.json({ invoiceId: inv.id, counterpartyEmail: inv.counterparty_email });
  });

  // Erasure on request (GDPR-style right to erasure).
  app.delete('/invoices/:id/counterparty-email', requireAuth, (req, res) => {
    const inv = stmt.invoiceById.get(req.params.id);
    if (!inv) return res.status(404).json({ error: 'not found' });

    transaction(db, () => {
      stmt.eraseEmail.run(inv.id);
      audit({
        invoiceId: inv.id, actorId: req.user.id, action: 'erase_counterparty_email',
        detail: inv.email_erased ? 'already_erased' : 'erased',
      });
    })();
    res.json({ ok: true, erased: true });
  });

  // ---- audit log (read-only) --------------------------------------------

  app.get('/invoices/:id/audit', requireAuth, (req, res) => {
    const inv = stmt.invoiceById.get(req.params.id);
    if (!inv) return res.status(404).json({ error: 'not found' });
    res.json(stmt.auditForInvoice.all(inv.id));
  });

  app.get('/health', (req, res) => res.json({ ok: true }));

  app.locals.db = db;
  return app;
}

module.exports = { createApp, APPROVAL_THRESHOLD_CENTS };
