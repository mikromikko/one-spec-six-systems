'use strict';

const path = require('node:path');
const express = require('express');
const { createApp } = require('./app');

const PORT = process.env.PORT || 3000;

const app = createApp({
  dbPath: process.env.DB_PATH || path.join(__dirname, '..', 'data', 'app.db'),
  uploadDir: process.env.UPLOAD_DIR || path.join(__dirname, '..', 'data', 'uploads'),
  outboxPath: process.env.OUTBOX_PATH || path.join(__dirname, '..', 'data', 'outbox.log'),
});

// Serve the minimal demo UI from /public.
app.use('/', express.static(path.join(__dirname, '..', 'public')));

app.listen(PORT, () => {
  // eslint-disable-next-line no-console
  console.log(`Invoice approval service listening on http://localhost:${PORT}`);
  // eslint-disable-next-line no-console
  console.log(`Email notifications are written to ${app.locals.mailer.outboxPath}`);
});
