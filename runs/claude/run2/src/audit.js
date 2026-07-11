'use strict';

/**
 * Append a single immutable entry to the audit log (req 6 & 7).
 *
 * Records who (actor_id), what changed (action + state_before/state_after +
 * details) and when (created_at). Callers never update or delete audit rows;
 * DB triggers reject any such attempt as a defence in depth.
 */
function recordAudit(db, {
  invoiceId = null,
  actorId = null,
  action,
  stateBefore = null,
  stateAfter = null,
  details = null,
}) {
  db.prepare(`
    INSERT INTO audit_log
      (invoice_id, actor_id, action, state_before, state_after, details, created_at)
    VALUES (?, ?, ?, ?, ?, ?, ?)
  `).run(
    invoiceId,
    actorId,
    action,
    stateBefore,
    stateAfter,
    details == null ? null : String(details),
    new Date().toISOString(),
  );
}

module.exports = { recordAudit };
