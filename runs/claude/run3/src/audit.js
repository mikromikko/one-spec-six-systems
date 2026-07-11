// Helper for writing append-only audit log entries.
// Every state change and every read of the personal counterparty email
// flows through here (requirements 6 and 7).
export function recordAudit(db, { invoiceId = null, actorId = null, action, stateBefore = null, stateAfter = null, detail = null }) {
  db.prepare(
    `INSERT INTO audit_log (invoice_id, actor_id, action, state_before, state_after, detail, created_at)
     VALUES (?, ?, ?, ?, ?, ?, ?)`
  ).run(invoiceId, actorId, action, stateBefore, stateAfter, detail, new Date().toISOString());
}
