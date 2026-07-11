import express from 'express';
import session from 'express-session';
import multer from 'multer';
import { mkdirSync, existsSync } from 'node:fs';
import { randomUUID } from 'node:crypto';
import { join, extname } from 'node:path';

import { hashPassword, verifyPassword, requireAuth, requireRole } from './auth.js';
import { recordAudit } from './audit.js';
import { sendEmail } from './email.js';

const AMOUNT_THRESHOLD_CENTS = 1000000; // 10,000.00 EUR

// Parse a euro amount ("1234.56", 1234.56) into integer cents.
function toCents(amount) {
  if (typeof amount !== 'number' && typeof amount !== 'string') return null;
  const n = Number(amount);
  if (!Number.isFinite(n) || n <= 0) return null;
  return Math.round(n * 100);
}

function centsToEuros(cents) {
  return Number((cents / 100).toFixed(2));
}

// Number of distinct approvers required for a given amount (requirement 5).
function approvalsRequired(amountCents) {
  return amountCents > AMOUNT_THRESHOLD_CENTS ? 2 : 1;
}

function publicInvoice(inv) {
  return {
    id: inv.id,
    submitter_id: inv.submitter_id,
    amount_eur: centsToEuros(inv.amount_cents),
    counterparty_name: inv.counterparty_name,
    counterparty_email_erased: !!inv.counterparty_email_erased,
    due_date: inv.due_date,
    state: inv.state,
    created_at: inv.created_at,
    approvals_required: approvalsRequired(inv.amount_cents),
  };
}

export function createApp(db, options = {}) {
  const uploadDir = options.uploadDir || process.env.UPLOAD_DIR || './uploads';
  const emailDir = options.emailDir || process.env.EMAIL_DIR || './emails';
  mkdirSync(uploadDir, { recursive: true });

  const storage = multer.diskStorage({
    destination: (req, file, cb) => cb(null, uploadDir),
    filename: (req, file, cb) => cb(null, `${randomUUID()}${extname(file.originalname) || '.pdf'}`),
  });
  const upload = multer({
    storage,
    limits: { fileSize: 25 * 1024 * 1024 },
    fileFilter: (req, file, cb) => {
      if (file.mimetype === 'application/pdf') return cb(null, true);
      cb(new Error('attachment must be a PDF'));
    },
  });

  const app = express();
  app.use(express.json());
  app.use(express.urlencoded({ extended: true }));
  app.use(
    session({
      secret: process.env.SESSION_SECRET || 'dev-secret-change-me',
      resave: false,
      saveUninitialized: false,
      cookie: { httpOnly: true, sameSite: 'lax' },
    })
  );

  const auth = requireAuth(db);

  const notifySubmitter = (invoice, subject, body) => {
    const submitter = db.prepare('SELECT email FROM users WHERE id = ?').get(invoice.submitter_id);
    if (submitter) sendEmail(submitter.email, subject, body, emailDir);
  };

  // --- Auth ---------------------------------------------------------------

  app.post('/api/auth/register', (req, res) => {
    const { email, password, role } = req.body || {};
    if (!email || !password || !['submitter', 'approver'].includes(role)) {
      return res.status(400).json({ error: 'email, password and role (submitter|approver) are required' });
    }
    const exists = db.prepare('SELECT id FROM users WHERE email = ?').get(email);
    if (exists) return res.status(409).json({ error: 'email already registered' });
    const info = db
      .prepare('INSERT INTO users (email, password_hash, role) VALUES (?, ?, ?)')
      .run(email, hashPassword(password), role);
    res.status(201).json({ id: Number(info.lastInsertRowid), email, role });
  });

  app.post('/api/auth/login', (req, res) => {
    const { email, password } = req.body || {};
    const user = db.prepare('SELECT * FROM users WHERE email = ?').get(email || '');
    if (!user || !verifyPassword(password || '', user.password_hash)) {
      return res.status(401).json({ error: 'invalid credentials' });
    }
    req.session.userId = user.id;
    res.json({ id: user.id, email: user.email, role: user.role });
  });

  app.post('/api/auth/logout', (req, res) => {
    req.session.destroy(() => res.json({ ok: true }));
  });

  app.get('/api/auth/me', auth, (req, res) => res.json(req.user));

  // --- Invoice creation (submitter) --------------------------------------

  app.post('/api/invoices', auth, requireRole('submitter'), upload.single('pdf'), (req, res) => {
    const { amount, counterparty_name, counterparty_email, due_date } = req.body || {};
    const amountCents = toCents(amount);
    const errors = [];
    if (amountCents === null) errors.push('amount must be a positive number of euros');
    if (!counterparty_name) errors.push('counterparty_name is required');
    if (!counterparty_email) errors.push('counterparty_email is required');
    if (!due_date || Number.isNaN(Date.parse(due_date))) errors.push('due_date must be a valid date');
    if (!req.file) errors.push('a PDF attachment is required');
    if (errors.length) return res.status(400).json({ error: errors.join('; ') });

    const createdAt = new Date().toISOString();
    const info = db
      .prepare(
        `INSERT INTO invoices
           (submitter_id, amount_cents, counterparty_name, counterparty_email, due_date, pdf_path, state, created_at)
         VALUES (?, ?, ?, ?, ?, ?, 'submitted', ?)`
      )
      .run(req.user.id, amountCents, counterparty_name, counterparty_email, due_date, req.file.path, createdAt);

    const invoiceId = Number(info.lastInsertRowid);
    recordAudit(db, {
      invoiceId,
      actorId: req.user.id,
      action: 'create',
      stateBefore: null,
      stateAfter: 'submitted',
      detail: `amount ${centsToEuros(amountCents)} EUR`,
    });

    const invoice = db.prepare('SELECT * FROM invoices WHERE id = ?').get(invoiceId);
    res.status(201).json(publicInvoice(invoice));
  });

  // --- Listing ------------------------------------------------------------

  // Approver view: invoices pending their approval, filterable (requirement 8).
  app.get('/api/invoices/pending', auth, requireRole('approver'), (req, res) => {
    const { counterparty, min_amount, max_amount } = req.query;
    const clauses = [
      "i.state = 'submitted'",
      'i.submitter_id != ?', // cannot approve own invoices (requirement 4)
      'NOT EXISTS (SELECT 1 FROM approvals a WHERE a.invoice_id = i.id AND a.approver_id = ?)',
    ];
    const params = [req.user.id, req.user.id];

    if (counterparty) {
      clauses.push('i.counterparty_name LIKE ?');
      params.push(`%${counterparty}%`);
    }
    const minC = toCents(min_amount);
    if (min_amount != null && min_amount !== '' && minC !== null) {
      clauses.push('i.amount_cents >= ?');
      params.push(minC);
    }
    const maxC = toCents(max_amount);
    if (max_amount != null && max_amount !== '' && maxC !== null) {
      clauses.push('i.amount_cents <= ?');
      params.push(maxC);
    }

    const rows = db
      .prepare(`SELECT i.* FROM invoices i WHERE ${clauses.join(' AND ')} ORDER BY i.created_at`)
      .all(...params);
    res.json(rows.map(publicInvoice));
  });

  // General listing: submitters see their own; approvers see all.
  app.get('/api/invoices', auth, (req, res) => {
    const rows =
      req.user.role === 'approver'
        ? db.prepare('SELECT * FROM invoices ORDER BY created_at DESC').all()
        : db.prepare('SELECT * FROM invoices WHERE submitter_id = ? ORDER BY created_at DESC').all(req.user.id);
    res.json(rows.map(publicInvoice));
  });

  app.get('/api/invoices/:id', auth, (req, res) => {
    const inv = db.prepare('SELECT * FROM invoices WHERE id = ?').get(req.params.id);
    if (!inv) return res.status(404).json({ error: 'invoice not found' });
    if (req.user.role !== 'approver' && inv.submitter_id !== req.user.id) {
      return res.status(403).json({ error: 'not permitted to view this invoice' });
    }
    const approvals = db
      .prepare('SELECT approver_id, decision, created_at FROM approvals WHERE invoice_id = ? ORDER BY created_at')
      .all(inv.id);
    res.json({ ...publicInvoice(inv), approvals });
  });

  // --- Personal data: read + erase (requirement 7) -----------------------

  // Every read of the counterparty email is audited.
  app.get('/api/invoices/:id/counterparty-email', auth, (req, res) => {
    const inv = db.prepare('SELECT * FROM invoices WHERE id = ?').get(req.params.id);
    if (!inv) return res.status(404).json({ error: 'invoice not found' });
    if (req.user.role !== 'approver' && inv.submitter_id !== req.user.id) {
      return res.status(403).json({ error: 'not permitted to view this invoice' });
    }

    recordAudit(db, {
      invoiceId: inv.id,
      actorId: req.user.id,
      action: 'read_counterparty_email',
      detail: inv.counterparty_email_erased ? 'erased' : 'disclosed',
    });

    if (inv.counterparty_email_erased || inv.counterparty_email == null) {
      return res.json({ invoice_id: inv.id, counterparty_email: null, erased: true });
    }
    res.json({ invoice_id: inv.id, counterparty_email: inv.counterparty_email, erased: false });
  });

  // Erasure on request. The erasure itself is audited; the email value is not
  // repeated in the audit detail.
  app.delete('/api/invoices/:id/counterparty-email', auth, (req, res) => {
    const inv = db.prepare('SELECT * FROM invoices WHERE id = ?').get(req.params.id);
    if (!inv) return res.status(404).json({ error: 'invoice not found' });
    if (req.user.role !== 'approver' && inv.submitter_id !== req.user.id) {
      return res.status(403).json({ error: 'not permitted' });
    }

    db.prepare(
      'UPDATE invoices SET counterparty_email = NULL, counterparty_email_erased = 1 WHERE id = ?'
    ).run(inv.id);
    recordAudit(db, {
      invoiceId: inv.id,
      actorId: req.user.id,
      action: 'erase_counterparty_email',
      detail: 'counterparty contact email erased on request',
    });
    res.json({ invoice_id: inv.id, erased: true });
  });

  // --- Approve / reject (approver) ---------------------------------------

  app.post('/api/invoices/:id/approve', auth, requireRole('approver'), (req, res) => {
    const inv = db.prepare('SELECT * FROM invoices WHERE id = ?').get(req.params.id);
    if (!inv) return res.status(404).json({ error: 'invoice not found' });
    if (inv.state !== 'submitted') {
      return res.status(409).json({ error: `invoice is already ${inv.state}` });
    }
    if (inv.submitter_id === req.user.id) {
      return res.status(403).json({ error: 'approvers cannot approve their own invoice' });
    }
    const already = db
      .prepare('SELECT 1 FROM approvals WHERE invoice_id = ? AND approver_id = ?')
      .get(inv.id, req.user.id);
    if (already) return res.status(409).json({ error: 'you have already recorded a decision for this invoice' });

    const now = new Date().toISOString();
    db.prepare('INSERT INTO approvals (invoice_id, approver_id, decision, created_at) VALUES (?, ?, ?, ?)').run(
      inv.id,
      req.user.id,
      'approve',
      now
    );

    const approveCount = db
      .prepare("SELECT COUNT(*) AS c FROM approvals WHERE invoice_id = ? AND decision = 'approve'")
      .get(inv.id).c;
    const required = approvalsRequired(inv.amount_cents);

    if (approveCount >= required) {
      db.prepare('UPDATE invoices SET state = ? WHERE id = ?').run('approved', inv.id);
      recordAudit(db, {
        invoiceId: inv.id,
        actorId: req.user.id,
        action: 'approve',
        stateBefore: 'submitted',
        stateAfter: 'approved',
        detail: `${approveCount}/${required} approvals`,
      });
      notifySubmitter(
        inv,
        `Invoice #${inv.id} approved`,
        `Your invoice #${inv.id} (${centsToEuros(inv.amount_cents)} EUR, ${inv.counterparty_name}) has been approved.`
      );
      return res.json({ ...publicInvoice({ ...inv, state: 'approved' }), approvals_recorded: approveCount });
    }

    // Recorded, but not yet enough approvals: state unchanged.
    recordAudit(db, {
      invoiceId: inv.id,
      actorId: req.user.id,
      action: 'approve_partial',
      stateBefore: 'submitted',
      stateAfter: 'submitted',
      detail: `${approveCount}/${required} approvals`,
    });
    res.json({
      ...publicInvoice(inv),
      approvals_recorded: approveCount,
      message: `approval recorded; ${required - approveCount} more approval(s) required`,
    });
  });

  app.post('/api/invoices/:id/reject', auth, requireRole('approver'), (req, res) => {
    const inv = db.prepare('SELECT * FROM invoices WHERE id = ?').get(req.params.id);
    if (!inv) return res.status(404).json({ error: 'invoice not found' });
    if (inv.state !== 'submitted') {
      return res.status(409).json({ error: `invoice is already ${inv.state}` });
    }
    if (inv.submitter_id === req.user.id) {
      return res.status(403).json({ error: 'approvers cannot reject their own invoice' });
    }
    const already = db
      .prepare('SELECT 1 FROM approvals WHERE invoice_id = ? AND approver_id = ?')
      .get(inv.id, req.user.id);
    if (already) return res.status(409).json({ error: 'you have already recorded a decision for this invoice' });

    const now = new Date().toISOString();
    db.prepare('INSERT INTO approvals (invoice_id, approver_id, decision, created_at) VALUES (?, ?, ?, ?)').run(
      inv.id,
      req.user.id,
      'reject',
      now
    );
    db.prepare('UPDATE invoices SET state = ? WHERE id = ?').run('rejected', inv.id);
    recordAudit(db, {
      invoiceId: inv.id,
      actorId: req.user.id,
      action: 'reject',
      stateBefore: 'submitted',
      stateAfter: 'rejected',
      detail: req.body?.reason ? `reason: ${req.body.reason}` : null,
    });
    notifySubmitter(
      inv,
      `Invoice #${inv.id} rejected`,
      `Your invoice #${inv.id} (${centsToEuros(inv.amount_cents)} EUR, ${inv.counterparty_name}) has been rejected.`
    );
    res.json(publicInvoice({ ...inv, state: 'rejected' }));
  });

  // --- Audit log (read-only) ---------------------------------------------

  app.get('/api/invoices/:id/audit', auth, (req, res) => {
    const inv = db.prepare('SELECT * FROM invoices WHERE id = ?').get(req.params.id);
    if (!inv) return res.status(404).json({ error: 'invoice not found' });
    if (req.user.role !== 'approver' && inv.submitter_id !== req.user.id) {
      return res.status(403).json({ error: 'not permitted' });
    }
    const rows = db
      .prepare('SELECT * FROM audit_log WHERE invoice_id = ? ORDER BY id').all(inv.id);
    res.json(rows);
  });

  // Serve the minimal UI.
  if (existsSync(join(process.cwd(), 'public'))) {
    app.use(express.static(join(process.cwd(), 'public')));
  }

  // Multer / generic error handler.
  // eslint-disable-next-line no-unused-vars
  app.use((err, req, res, next) => {
    res.status(400).json({ error: err.message || 'bad request' });
  });

  return app;
}
