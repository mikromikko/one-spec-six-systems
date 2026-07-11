# Acceptance results — copilot-run1

Executed as scripted HTTP checks (see deviations log), 2026-07-11.

| # | Check | Score | Notes |
|---|---|---|---|
| 1 | Sign-in + roles | pass | user creation + 3 logins ok (no seed; POST /users per README); submitter approve attempt -> 403 |
| 2 | Invoice creation | partial | created id=2; fields ok=True; pdf download -> 404 ok=False |
| 3 | Lifecycle | pass | reject->True, approve->True |
| 4 | No self-approval | pass | approver submit attempt -> 403 (roles exclusive at submission) |
| 5 | Two-approver threshold | pass | 12k after 1 approval still submitted=True; after 2nd approved=True; exactly 10k one-approval=True |
| 6 | Append-only audit log | pass | 10 entries, actor+timestamp=True; PUT->404 DELETE->404 |
| 7 | PII erasure + access logging | pass | read audited=True; erased=True (post-erase GET -> 200); invoice survives=True |
| 8 | Pending list + filters | pass | name filter ok=True (1 rows); min_amount_euros=999999 -> 0 rows |
| 9 | Notification stub | pass | notifications.log exists=True, addressed to submitter=True |
| 10 | README + tests | pass | README steps worked (venv/pip/uvicorn); pytest rc=0: .......                                                                  [100%] | 7 passed in 0.96s |

**Total: 9 pass / 1 partial / 0 fail**
