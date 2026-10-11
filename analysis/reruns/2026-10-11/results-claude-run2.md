# Acceptance rerun — claude-run2

Executed as scripted HTTP checks at 2026-10-11T00:55:39.259476+00:00.
Startup/runtime deviations are documented in this directory's receipt.json; July results are unchanged.

| # | Check | Score | Notes |
|---|---|---|---|
| 1 | Sign-in + roles | pass | 3 logins ok; submitter approve attempt -> 403 |
| 2 | Invoice creation | pass | created id=2; fields ok=True; pdf download=True (status 200) |
| 3 | Lifecycle | pass | reject->True, approve->True |
| 4 | No self-approval | pass | approver submit attempt -> 403 (roles exclusive; 403 guard on approve for submitter_id match) |
| 5 | Two-approver threshold | pass | 12k after 1 approval still submitted=True; after 2nd approved=True; exactly 10k one-approval=True |
| 6 | Append-only audit log | pass | 3 entries, actor+timestamp=True; PUT->404 DELETE->404 |
| 7 | PII erasure + access logging | pass | read audited=True; erased=True (post-erase GET -> 410); invoice survives=True |
| 8 | Pending list + filters | pass | name filter ok=True (1 rows); minAmount=999999 -> 0 rows |
| 9 | Notification stub | pass | outbox.log exists=True, addressed to submitter=True |
| 10 | README + tests | pass | Unit tests (startup deviations are in the rerun receipt); npm test rc=0: ℹ skipped 0 | ℹ todo 0 | ℹ duration_ms 14505.736926 |

**Total: 10 pass / 0 partial / 0 fail**
