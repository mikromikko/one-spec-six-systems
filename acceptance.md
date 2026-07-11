# Acceptance checklist: invoice approval service

Executed identically against every run, by the same person, following the run's README only. One row per MUST requirement in `spec.md`. Scoring: **pass** (check succeeds as described), **partial** (feature exists but the check fails in some respect — note how), **fail** (feature absent or check cannot be executed). Record notes for every non-pass.

Setup for all checks: following the README, create three accounts — submitter S1, approvers A1 and A2. If the README provides seed accounts, use those and note it.

| # | Requirement | Check |
|---|---|---|
| 1 | Sign-in + roles | S1, A1, A2 can sign in with email/password. Signed in as S1, attempt to approve any invoice (via UI and, if an API exists, direct request): refused. |
| 2 | Invoice creation | As S1, create invoice: 9,000 EUR, counterparty "Valmet Steel", contact `laskutus@valmetsteel.example`, due date 30 days out, attach a small PDF. Invoice appears with all fields and retrievable attachment. |
| 3 | Lifecycle | The invoice from check 2 is in a submitted state. As A1, reject it: state becomes rejected. Create another; as A1, approve it: state becomes approved. |
| 4 | No self-approval | Create an approver-submitted invoice (as A1 if the system allows approvers to submit; if roles are exclusive per spec, verify A1 cannot submit at all — pass either way, note which). If A1 could submit one, A1 attempting to approve/reject it is refused; A2 can. |
| 5 | Two-approver threshold | As S1, create invoice of 12,000 EUR. A1 approves: state is NOT approved yet. A2 approves: state becomes approved. Separately, a 10,000 EUR invoice approved by A1 alone becomes approved (boundary: 10,000 is not "over"). |
| 6 | Append-only audit log | After checks 3–5, an audit entry exists for every state change with actor, before/after state, timestamp. Attempt to edit or delete any entry through every interface the app provides (UI, API): no such capability exists. (Direct database edits are out of scope.) |
| 7 | PII erasure + access logging | View an invoice's counterparty contact email; an audit entry recording that read exists. Invoke the erasure mechanism for that counterparty contact: the email is removed or anonymized everywhere it was shown, and the invoice record otherwise survives. |
| 8 | Pending list + filters | As A1, the pending list shows submitted invoices awaiting A1's approval. Filter by counterparty "Valmet Steel": only matching invoices. Filter by amount: only matching invoices. |
| 9 | Notification stub | Perform one state change; the notification output (log line, file, or equivalent stub) exists, addressed to the submitter, reflecting the change. |
| 10 | README + tests | On a clean checkout, the README steps alone produce a running system (record every deviation needed, however small — any deviation is at best partial). Run the tests per README: record count and pass/fail. |

## Recording

Per run, record: run ID, date, checker, per-check score + notes, and the total (n pass / n partial / n fail). The per-run tables are combined into the requirement-coverage matrix — the headline table of the essay.
