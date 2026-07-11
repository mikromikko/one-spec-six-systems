# Acceptance results — copilot-run3

Executed as scripted HTTP checks (see deviations log), 2026-07-11.

| # | Check | Score | Notes |
|---|---|---|---|
| 1 | Sign-in + roles | pass | user creation + 3 logins ok; submitter approve attempt -> 403 |
| 2 | Invoice creation | pass | created id=2; fields ok=True; pdf download -> 200 ok=True |
| 3 | Lifecycle | pass | reject->True, approve->True |
| 4 | No self-approval | pass | approver submit attempt -> 403 (roles exclusive at submission) |
| 5 | Two-approver threshold | pass | 12k after 1 approval still submitted=True; after 2nd approved=True; exactly 10k one-approval=True |
| 6 | Append-only audit log | pass | 3 entries, actor+timestamp=True; PUT->404 DELETE->404 |
| 7 | PII erasure + access logging | pass | read audited=True; erased=True (post-erase GET -> 200); invoice survives=True |
| 8 | Pending list + filters | pass | name filter ok=True (1 rows); min_amount_euros=999999 -> 0 rows |
| 9 | Notification stub | pass | notifications.log exists=True, addressed to submitter=True |
| 10 | README + tests | pass | README steps worked (python3 app.py); unittest rc=0:  | OK |

**Total: 10 pass / 0 partial / 0 fail**
