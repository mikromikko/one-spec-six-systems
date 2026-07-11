'use strict';

const path = require('path');
const fs = require('fs');

// Email notification stub. Instead of sending real SMTP, each notification is
// appended as one JSON line to a log file and also written as an individual
// .eml-like file for easy inspection.
const DEFAULT_EMAIL_LOG = path.join(__dirname, '..', 'data', 'emails.log');

// Resolved at call time so tests (and deployments) can point EMAIL_LOG anywhere.
function emailLogPath() {
  return process.env.EMAIL_LOG || DEFAULT_EMAIL_LOG;
}

function ensureDir(filePath) {
  const dir = path.dirname(filePath);
  if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
}

function sendEmail({ to, subject, body }) {
  const message = {
    to,
    subject,
    body,
    sentAt: new Date().toISOString(),
  };
  const target = emailLogPath();
  ensureDir(target);
  fs.appendFileSync(target, JSON.stringify(message) + '\n');
  return message;
}

module.exports = { sendEmail, emailLogPath };
