import crypto from 'node:crypto';
import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const MAX_BODY_BYTES = 15 * 1024 * 1024;
const APPROVAL_THRESHOLD_CENTS = 1_000_000;

const seedUsers = [
  { id: 'user_submitter', email: 'submitter@nordvik.example', password: 'submitter-password', role: 'submitter' },
  { id: 'user_approver_1', email: 'approver1@nordvik.example', password: 'approver-password', role: 'approver' },
  { id: 'user_approver_2', email: 'approver2@nordvik.example', password: 'approver-password', role: 'approver' },
  { id: 'user_approver_3', email: 'approver3@nordvik.example', password: 'approver-password', role: 'approver' }
];

function nowIso() {
  return new Date().toISOString();
}

function randomId(prefix) {
  return `${prefix}_${crypto.randomUUID()}`;
}

function parseAmountToCents(value) {
  if (typeof value !== 'number' && typeof value !== 'string') {
    throw httpError(400, 'amountEuros must be a number or numeric string');
  }
  const text = String(value).trim();
  if (!/^\d+(\.\d{1,2})?$/.test(text)) {
    throw httpError(400, 'amountEuros must be a non-negative euro amount with at most two decimals');
  }
  const [euros, cents = ''] = text.split('.');
  return Number(euros) * 100 + Number(cents.padEnd(2, '0'));
}

function centsToEuros(cents) {
  return Number((cents / 100).toFixed(2));
}

function httpError(status, message) {
  const error = new Error(message);
  error.status = status;
  return error;
}

function hashPassword(password, salt = crypto.randomBytes(16).toString('hex')) {
  const hash = crypto.scryptSync(password, salt, 64).toString('hex');
  return `${salt}:${hash}`;
}

function verifyPassword(password, stored) {
  const [salt, hash] = stored.split(':');
  if (!salt || !hash) return false;
  const candidate = crypto.scryptSync(password, salt, 64);
  const expected = Buffer.from(hash, 'hex');
  return expected.length === candidate.length && crypto.timingSafeEqual(candidate, expected);
}

class Store {
  constructor(dataDir) {
    this.dataDir = dataDir;
    this.dbPath = path.join(dataDir, 'db.json');
    this.attachmentsDir = path.join(dataDir, 'attachments');
    this.emailLogPath = path.join(dataDir, 'emails.log');
    fs.mkdirSync(this.attachmentsDir, { recursive: true });
    this.db = this.load();
  }

  load() {
    if (fs.existsSync(this.dbPath)) {
      return JSON.parse(fs.readFileSync(this.dbPath, 'utf8'));
    }
    const db = {
      users: seedUsers.map(({ password, ...user }) => ({ ...user, passwordHash: hashPassword(password) })),
      invoices: [],
      auditLog: []
    };
    this.save(db);
    return db;
  }

  save(db = this.db) {
    fs.mkdirSync(this.dataDir, { recursive: true });
    const tmpPath = `${this.dbPath}.${process.pid}.tmp`;
    fs.writeFileSync(tmpPath, JSON.stringify(db, null, 2));
    fs.renameSync(tmpPath, this.dbPath);
  }

  addAudit(entry) {
    this.db.auditLog.push({
      id: randomId('audit'),
      at: nowIso(),
      ...entry
    });
    this.save();
  }

  sendStateChangeEmail(invoice, stateBefore, stateAfter) {
    const submitter = this.db.users.find((user) => user.id === invoice.submitterId);
    const record = {
      at: nowIso(),
      to: submitter.email,
      subject: `Invoice ${invoice.id} is now ${stateAfter}`,
      body: `Invoice ${invoice.id} changed from ${stateBefore ?? 'none'} to ${stateAfter}.`
    };
    fs.appendFileSync(this.emailLogPath, `${JSON.stringify(record)}\n`);
  }
}

function publicUser(user) {
  return { id: user.id, email: user.email, role: user.role };
}

function serializeInvoice(invoice, includeContactEmail = false) {
  const serialized = {
    id: invoice.id,
    amountEuros: centsToEuros(invoice.amountCents),
    counterpartyName: invoice.counterpartyName,
    dueDate: invoice.dueDate,
    pdfFileName: invoice.pdfFileName,
    submitterId: invoice.submitterId,
    state: invoice.state,
    approvals: invoice.approvals.map((approval) => ({ ...approval })),
    createdAt: invoice.createdAt,
    updatedAt: invoice.updatedAt
  };
  if (includeContactEmail) {
    serialized.counterpartyContactEmail = invoice.counterpartyContactEmail;
  }
  return serialized;
}

function isValidEmail(email) {
  return typeof email === 'string' && /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email);
}

function isValidDate(value) {
  return typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value) && !Number.isNaN(Date.parse(`${value}T00:00:00Z`));
}

async function readJson(req) {
  let body = '';
  for await (const chunk of req) {
    body += chunk;
    if (Buffer.byteLength(body) > MAX_BODY_BYTES) {
      throw httpError(413, 'request body is too large');
    }
  }
  if (!body) return {};
  try {
    return JSON.parse(body);
  } catch {
    throw httpError(400, 'request body must be valid JSON');
  }
}

function writeJson(res, status, payload) {
  res.writeHead(status, {
    'content-type': 'application/json; charset=utf-8',
    'cache-control': 'no-store'
  });
  res.end(JSON.stringify(payload));
}

function getBearerToken(req) {
  const header = req.headers.authorization || '';
  const [scheme, token] = header.split(' ');
  return scheme === 'Bearer' ? token : null;
}

function requireAuth(req, sessions) {
  const token = getBearerToken(req);
  const user = token ? sessions.get(token) : null;
  if (!user) throw httpError(401, 'authentication required');
  return user;
}

function requireRole(user, role) {
  if (user.role !== role) throw httpError(403, `${role} role required`);
}

function matchRoute(method, pathname) {
  if (pathname === '/invoices/pending') return { method, name: pathname };
  const invoiceAction = pathname.match(/^\/invoices\/([^/]+)\/(approve|reject|erase-counterparty-contact)$/);
  if (invoiceAction) return { method, name: `invoice:${invoiceAction[2]}`, id: invoiceAction[1] };
  const invoiceRead = pathname.match(/^\/invoices\/([^/]+)$/);
  if (invoiceRead) return { method, name: 'invoice:read', id: invoiceRead[1] };
  return { method, name: pathname };
}

function findInvoice(store, id) {
  const invoice = store.db.invoices.find((item) => item.id === id);
  if (!invoice) throw httpError(404, 'invoice not found');
  return invoice;
}

function validatePdfBase64(value) {
  if (typeof value !== 'string' || value.length === 0) {
    throw httpError(400, 'pdfBase64 is required');
  }
  let bytes;
  try {
    bytes = Buffer.from(value, 'base64');
  } catch {
    throw httpError(400, 'pdfBase64 must be valid base64');
  }
  if (bytes.length === 0 || bytes.subarray(0, 4).toString('utf8') !== '%PDF') {
    throw httpError(400, 'attachment must be a PDF encoded as base64');
  }
  return bytes;
}

function createServer({ dataDir = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', 'data') } = {}) {
  const store = new Store(dataDir);
  const sessions = new Map();

  return http.createServer(async (req, res) => {
    try {
      const url = new URL(req.url, 'http://localhost');
      const route = matchRoute(req.method, url.pathname);

      if (route.method === 'GET' && route.name === '/health') {
        return writeJson(res, 200, { ok: true });
      }

      if (route.method === 'POST' && route.name === '/auth/login') {
        const body = await readJson(req);
        const user = store.db.users.find((candidate) => candidate.email === body.email);
        if (!user || typeof body.password !== 'string' || !verifyPassword(body.password, user.passwordHash)) {
          throw httpError(401, 'invalid email or password');
        }
        const token = randomId('token');
        sessions.set(token, user);
        return writeJson(res, 200, { token, user: publicUser(user) });
      }

      const user = requireAuth(req, sessions);

      if (route.method === 'GET' && route.name === '/me') {
        return writeJson(res, 200, { user: publicUser(user) });
      }

      if (route.method === 'POST' && route.name === '/invoices') {
        requireRole(user, 'submitter');
        const body = await readJson(req);
        const amountCents = parseAmountToCents(body.amountEuros);
        if (amountCents <= 0) throw httpError(400, 'amountEuros must be greater than zero');
        if (typeof body.counterpartyName !== 'string' || body.counterpartyName.trim().length === 0) {
          throw httpError(400, 'counterpartyName is required');
        }
        if (!isValidEmail(body.counterpartyContactEmail)) {
          throw httpError(400, 'counterpartyContactEmail must be a valid email address');
        }
        if (!isValidDate(body.dueDate)) {
          throw httpError(400, 'dueDate must be an ISO date in YYYY-MM-DD format');
        }
        const pdfBytes = validatePdfBase64(body.pdfBase64);
        const id = randomId('inv');
        const pdfFileName = typeof body.pdfFileName === 'string' && body.pdfFileName.trim() ? body.pdfFileName.trim() : `${id}.pdf`;
        const attachmentPath = path.join(store.attachmentsDir, `${id}.pdf`);
        fs.writeFileSync(attachmentPath, pdfBytes);
        const invoice = {
          id,
          amountCents,
          counterpartyName: body.counterpartyName.trim(),
          counterpartyContactEmail: body.counterpartyContactEmail,
          dueDate: body.dueDate,
          pdfFileName,
          attachmentPath,
          submitterId: user.id,
          state: 'submitted',
          approvals: [],
          createdAt: nowIso(),
          updatedAt: nowIso()
        };
        store.db.invoices.push(invoice);
        store.addAudit({
          actorUserId: user.id,
          action: 'submit_invoice',
          invoiceId: id,
          stateBefore: null,
          stateAfter: 'submitted'
        });
        store.sendStateChangeEmail(invoice, null, 'submitted');
        store.save();
        return writeJson(res, 201, { invoice: serializeInvoice(invoice, true) });
      }

      if (route.method === 'GET' && route.name === '/invoices/pending') {
        requireRole(user, 'approver');
        const counterpartyName = url.searchParams.get('counterpartyName')?.toLowerCase();
        const minAmount = url.searchParams.has('minAmount') ? parseAmountToCents(url.searchParams.get('minAmount')) : null;
        const maxAmount = url.searchParams.has('maxAmount') ? parseAmountToCents(url.searchParams.get('maxAmount')) : null;
        const invoices = store.db.invoices
          .filter((invoice) => invoice.state === 'submitted')
          .filter((invoice) => invoice.submitterId !== user.id)
          .filter((invoice) => !invoice.approvals.some((approval) => approval.approverId === user.id))
          .filter((invoice) => !counterpartyName || invoice.counterpartyName.toLowerCase().includes(counterpartyName))
          .filter((invoice) => minAmount === null || invoice.amountCents >= minAmount)
          .filter((invoice) => maxAmount === null || invoice.amountCents <= maxAmount)
          .map((invoice) => serializeInvoice(invoice, false));
        return writeJson(res, 200, { invoices });
      }

      if (route.method === 'GET' && route.name === 'invoice:read') {
        const invoice = findInvoice(store, route.id);
        if (user.role !== 'approver' && invoice.submitterId !== user.id) {
          throw httpError(403, 'invoice access denied');
        }
        store.addAudit({
          actorUserId: user.id,
          action: 'read_counterparty_contact_email',
          invoiceId: invoice.id,
          stateBefore: invoice.state,
          stateAfter: invoice.state
        });
        return writeJson(res, 200, { invoice: serializeInvoice(invoice, true) });
      }

      if (route.method === 'POST' && route.name === 'invoice:approve') {
        requireRole(user, 'approver');
        const invoice = findInvoice(store, route.id);
        if (invoice.submitterId === user.id) throw httpError(403, 'approvers cannot approve invoices they submitted');
        if (invoice.state !== 'submitted') throw httpError(409, 'only submitted invoices can be approved');
        if (invoice.approvals.some((approval) => approval.approverId === user.id)) {
          throw httpError(409, 'this approver has already approved the invoice');
        }
        invoice.approvals.push({ approverId: user.id, at: nowIso() });
        const requiredApprovals = invoice.amountCents > APPROVAL_THRESHOLD_CENTS ? 2 : 1;
        const before = invoice.state;
        if (invoice.approvals.length >= requiredApprovals) {
          invoice.state = 'approved';
          invoice.updatedAt = nowIso();
          store.addAudit({
            actorUserId: user.id,
            action: 'approve_invoice',
            invoiceId: invoice.id,
            stateBefore: before,
            stateAfter: invoice.state
          });
          store.sendStateChangeEmail(invoice, before, invoice.state);
        } else {
          invoice.updatedAt = nowIso();
          store.addAudit({
            actorUserId: user.id,
            action: 'record_invoice_approval',
            invoiceId: invoice.id,
            stateBefore: before,
            stateAfter: invoice.state
          });
        }
        store.save();
        return writeJson(res, 200, { invoice: serializeInvoice(invoice, false) });
      }

      if (route.method === 'POST' && route.name === 'invoice:reject') {
        requireRole(user, 'approver');
        const invoice = findInvoice(store, route.id);
        if (invoice.submitterId === user.id) throw httpError(403, 'approvers cannot reject invoices they submitted');
        if (invoice.state !== 'submitted') throw httpError(409, 'only submitted invoices can be rejected');
        const before = invoice.state;
        invoice.state = 'rejected';
        invoice.updatedAt = nowIso();
        store.addAudit({
          actorUserId: user.id,
          action: 'reject_invoice',
          invoiceId: invoice.id,
          stateBefore: before,
          stateAfter: invoice.state
        });
        store.sendStateChangeEmail(invoice, before, invoice.state);
        store.save();
        return writeJson(res, 200, { invoice: serializeInvoice(invoice, false) });
      }

      if (route.method === 'POST' && route.name === 'invoice:erase-counterparty-contact') {
        const invoice = findInvoice(store, route.id);
        if (user.role !== 'approver' && invoice.submitterId !== user.id) {
          throw httpError(403, 'invoice access denied');
        }
        invoice.counterpartyContactEmail = null;
        invoice.updatedAt = nowIso();
        store.addAudit({
          actorUserId: user.id,
          action: 'erase_counterparty_contact_email',
          invoiceId: invoice.id,
          stateBefore: invoice.state,
          stateAfter: invoice.state
        });
        store.save();
        return writeJson(res, 200, { invoice: serializeInvoice(invoice, true) });
      }

      if (route.method === 'GET' && route.name === '/audit') {
        const invoiceId = url.searchParams.get('invoiceId');
        const auditLog = store.db.auditLog.filter((entry) => !invoiceId || entry.invoiceId === invoiceId);
        return writeJson(res, 200, { auditLog });
      }

      throw httpError(404, 'route not found');
    } catch (error) {
      const status = Number(error.status) || 500;
      writeJson(res, status, { error: status === 500 ? 'internal server error' : error.message });
    }
  });
}

export { createServer };
