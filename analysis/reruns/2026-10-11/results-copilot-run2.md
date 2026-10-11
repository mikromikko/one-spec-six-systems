# Acceptance rerun — copilot-run2

Executed as scripted HTTP checks at 2026-10-11T00:55:58.863939+00:00.
Startup/runtime deviations are documented in this directory's receipt.json; July results are unchanged.

| # | Check | Score | Notes |
|---|---|---|---|
| 1 | Sign-in + roles | pass | 3 seeded logins ok; submitter approve attempt -> 403 |
| 2 | Invoice creation | pass | created id=2; fields ok=True; attachment field=True; /pdf endpoint -> 404 |
| 3 | Lifecycle | pass | reject->True, approve->True |
| 4 | No self-approval | pass | approver submit attempt -> 403 (roles exclusive at submission) |
| 5 | Two-approver threshold | pass | 12k after 1 approval still submitted=True; after 2nd approved=True; exactly 10k one-approval=True |
| 6 | Append-only audit log | partial | 4 entries, actor+timestamp=True; PUT->501 DELETE->501 |
| 7 | PII erasure + access logging | pass | read audited=True; erased=True; invoice survives=True |
| 8 | Pending list + filters | pass | name filter ok=True (1 rows); min_amount_euros=999999 -> 0 rows |
| 9 | Notification stub | pass | email_notifications.log exists=True, addressed to submitter=True |
| 10 | README + tests | pass | Unit tests (startup deviations are in the rerun receipt); unittest rc=0:  | OK |

**Total: 9 pass / 1 partial / 0 fail**
