# Acceptance rerun — claude-run3

Executed as scripted HTTP checks at 2026-10-11T00:55:44.386137+00:00.
Startup/runtime deviations are documented in this directory's receipt.json; July results are unchanged.

| # | Check | Score | Notes |
|---|---|---|---|
| 1 | Sign-in + roles | pass | 3 logins ok (cookie sessions); submitter approve attempt -> 403 |
| 2 | Invoice creation | partial | created id=2; fields ok=True; pdf download -> 404 ok=False |
| 3 | Lifecycle | pass | reject->True, approve->True |
| 4 | No self-approval | pass | approver submit attempt -> 403 (roles exclusive at submission) |
| 5 | Two-approver threshold | pass | 12k after 1 approval still submitted=True; after 2nd approved=True; exactly 10k one-approval=True |
| 6 | Append-only audit log | pass | 3 entries, actor+timestamp=True; PUT->404 DELETE->404 |
| 7 | PII erasure + access logging | pass | read audited=True; erased=True (post-erase GET -> 200); invoice survives=True |
| 8 | Pending list + filters | pass | name filter ok=True (1 rows); min_amount=999999 -> 0 rows |
| 9 | Notification stub | pass | emails dir has 5 files; addressed to submitter=True |
| 10 | README + tests | pass | Unit tests (startup deviations are in the rerun receipt); npm test rc=0: ℹ skipped 0 | ℹ todo 0 | ℹ duration_ms 3757.068265 |

**Total: 9 pass / 1 partial / 0 fail**
