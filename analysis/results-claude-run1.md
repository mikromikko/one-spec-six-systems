# Acceptance results — claude-run1

Executed as scripted HTTP checks (see deviations log), 2026-07-11.

| # | Check | Score | Notes |
|---|---|---|---|
| 1 | Sign-in + roles | pass | 3 logins ok; submitter approve attempt -> 403 |
| 2 | Invoice creation | partial | created id=2; fields ok=True; pdf field present=False (no download endpoint — stored path only) |
| 3 | Lifecycle | pass | reject->True, approve->True |
| 4 | No self-approval | pass | approver submit attempt -> 403 (roles exclusive; self-approval structurally impossible + 403 guard in code) |
| 5 | Two-approver threshold | pass | 12k after 1 approval still submitted=True; after 2nd approved=True; exactly 10k one-approval=True |
| 6 | Append-only audit log | pass | 3 entries, actor+timestamp=True; PUT->404 DELETE->404 (no modification API) |
| 7 | PII erasure + access logging | pass | read audited=True; erased=True; invoice survives=True |
| 8 | Pending list + filters | pass | name filter ok=True; minAmount=999999 -> 0 rows |
| 9 | Notification stub | pass | emails.log exists=True, addressed to submitter=True |
| 10 | README + tests | pass | README steps worked verbatim (install/seed/start); npm test rc=0: ℹ skipped 0 | ℹ todo 0 | ℹ duration_ms 1736.435167 |

**Total: 9 pass / 1 partial / 0 fail**
