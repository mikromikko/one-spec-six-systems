'use strict';

const os = require('os');
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

// Each test file gets an isolated temp dir for its DB, uploads and email log.
function makeTmpDir() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'invoice-test-'));
  return dir;
}

// A minimal but valid PDF file as a buffer.
const SAMPLE_PDF = Buffer.from(
  '%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n' +
    '2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n' +
    '3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]>>endobj\n' +
    'trailer<</Root 1 0 R>>\n%%EOF\n',
  'utf8'
);

function buildTestApp() {
  const dir = makeTmpDir();
  process.env.EMAIL_LOG = path.join(dir, 'emails.log');
  // Require after env is set so the email stub picks up the log path.
  const { createDb } = require('../src/db');
  const { createApp } = require('../src/app');
  const { seed } = require('../src/seed');

  const db = createDb(path.join(dir, 'test.db'));
  seed(db);
  const app = createApp({ db, uploadDir: path.join(dir, 'uploads') });
  return { app, db, dir, emailLog: process.env.EMAIL_LOG };
}

function readEmails(emailLog) {
  if (!fs.existsSync(emailLog)) return [];
  return fs
    .readFileSync(emailLog, 'utf8')
    .split('\n')
    .filter(Boolean)
    .map((l) => JSON.parse(l));
}

async function login(request, app, email, password = 'password123') {
  const res = await request(app).post('/login').send({ email, password });
  if (res.status !== 200) throw new Error('login failed: ' + JSON.stringify(res.body));
  return res.body.token;
}

module.exports = { buildTestApp, readEmails, login, SAMPLE_PDF };
