'use strict';

const fs = require('node:fs');
const path = require('node:path');

/**
 * Email notification stub (req 9).
 *
 * No real SMTP: every message is appended as a JSON line to an outbox file and
 * echoed to the console. Tests read the outbox file to assert notifications.
 */
function createMailer(outboxPath) {
  fs.mkdirSync(path.dirname(outboxPath), { recursive: true });

  function send({ to, subject, body }) {
    const message = {
      to,
      subject,
      body,
      sentAt: new Date().toISOString(),
    };
    fs.appendFileSync(outboxPath, JSON.stringify(message) + '\n');
    // eslint-disable-next-line no-console
    console.log(`[email] -> ${to}: ${subject}`);
    return message;
  }

  return { send, outboxPath };
}

module.exports = { createMailer };
