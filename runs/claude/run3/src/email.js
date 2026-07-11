import { mkdirSync, appendFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

// Email stub (requirement 9). No real SMTP: every notification is written
// as an individual .eml-like file and appended to a single log file so the
// full history is easy to inspect.
export function sendEmail(to, subject, body, dir = process.env.EMAIL_DIR || './emails') {
  mkdirSync(dir, { recursive: true });
  const now = new Date();
  const stamp = now.toISOString().replace(/[:.]/g, '-');
  const rand = Math.random().toString(36).slice(2, 8);
  const contents =
    `To: ${to}\n` +
    `Subject: ${subject}\n` +
    `Date: ${now.toISOString()}\n` +
    `\n${body}\n`;

  writeFileSync(join(dir, `${stamp}-${rand}.txt`), contents);
  appendFileSync(join(dir, 'outbox.log'), `${'='.repeat(60)}\n${contents}\n`);
  return { to, subject, body };
}
