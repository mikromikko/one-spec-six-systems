# Static comparison matrix — six runs, one frozen spec

Source: read-only code extraction, 2026-07-11. Spec SHA-256 `8248b2…`. Runs 1–3 per agent (run 0/0b smoke excluded). Manual acceptance checks (acceptance.md) still pending — requirement-coverage rows below are *static reading*, not executed verification.

## Claude Code (Opus 4.8) — three runs, all Node.js/Express/SQLite

| Dimension | run1 | run2 | run3 |
|---|---|---|---|
| Direct deps | express, multer | express, multer | express, express-session, multer |
| Auth model | **opaque bearer token** (sessions table) | **hand-rolled signed JWT** (stateless) | **session cookie** (httpOnly, sameSite) |
| Password hashing | scrypt | scrypt | scrypt |
| API shape | bare paths (`/invoices`) | `/api/*` prefix, has `/register` | `/api/*` prefix, register+logout |
| Sessions table | yes | no (JWT) | no (express-session memory) |
| Approvals table | (invoice, approver) unique | (invoice, approver) unique | + `decision` column (approve\|reject) |
| Can approvers submit invoices? | **no** (403) | **no** | **no** (403 — verified empirically; an earlier static read claimed yes and was wrong) |
| Extra features | /health | **public/index.html UI**, GET /api/audit | .eml files per notification |
| Notification trigger | only on state change (partial approval silent) | state changes | state changes |
| Security oddities | orphaned PDFs on disk | **hardcoded fallback JWT secret `dev-insecure-secret`**; email sent cleartext unaudited at submission | none flagged |
| Tests | 18 (node:test + supertest) | 16 | 13 |
| Duration | 428 s | 439 s | 351 s |

## Copilot CLI (GPT-5.5) — three runs, all Python/SQLite

| Dimension | run1 | run2 | run3 |
|---|---|---|---|
| Framework | **FastAPI + uvicorn** (6 pinned deps) | **stdlib http.server** (zero deps) | **stdlib wsgiref** (zero deps) |
| Layout | app/ package | flat, service module split | flat, single app.py |
| PDF storage | file on disk (pdf_path) | **BLOB in SQLite** | **BLOB in SQLite** |
| Auth | bearer, PBKDF2 **390k iterations** | bearer, PBKDF2 200k | bearer, PBKDF2 200k |
| Erasure API | POST …/erase-counterparty-contact-email | POST …/erase-contact | **DELETE …/contact-email** |
| Erasure marker | NULL only | NULL only | NULL + erased_at timestamp |
| Audit-log access | authenticated | **any authenticated user, no scoping** | any authenticated user |
| Known bug (static) | — | stale approval_count in reject flow | possible interleaved writes to notifications.log |
| Tests | 7 (pytest) | 8 (unittest) | 8 (unittest) |
| Duration | 146 s | 174 s | 164 s |

## Where all six runs agreed (also a finding)

- Amounts as integer cents; threshold implemented as strictly `> 1_000_000` cents; exactly 10,000 = one approval — all six, all tested.
- Append-only audit log enforced with SQLite BEFORE UPDATE/DELETE triggers — all six independently chose DB triggers.
- PII reads logged as a dedicated audit action; erasure sets NULL and is itself audited — all six.
- Three states, role-gated transitions, distinct-approver uniqueness constraint — all six.

## Pre-registered materiality criteria — pairwise within agent

Criteria (≥3 required): 1 requirement coverage, 2 schema, 3 API surface/auth, 4 dependency set, 5 security findings, 6 stack/framework.

| Pair | Criteria met | Detail |
|---|---|---|
| claude r1 vs r2 | **2, 3, 4(borderline), 5** | sessions table vs none; auth opaque-token vs JWT + register endpoint + /api prefix; same manifest but hand-rolled JWT ≈ same deps (borderline); hardcoded fallback secret only in r2 |
| claude r1 vs r3 | **2, 3, 4** | approvals.decision column + no sessions table; bearer vs cookie auth + approver-submit allowed only in r3; express-session added |
| claude r2 vs r3 | **2, 3, 4** | JWT vs cookie; register/logout surface; deps differ |
| copilot r1 vs r2 | **2, 3, 4, 6** | FastAPI vs stdlib http.server; 6 deps vs zero; pdf_path vs BLOB; erase endpoint differs |
| copilot r1 vs r3 | **2, 3, 4, 6** | FastAPI vs wsgiref; deps; BLOB; DELETE vs POST erasure |
| copilot r2 vs r3 | **2, 3** (+5 candidate) | http.server vs wsgiref (both stdlib), erase API differs, erased_at column only in r3 — weakest pair, may fall under the bar pending scanner + acceptance results |

**Provisional verdict:** 5 of 6 within-agent pairs meet the pre-registered bar on static reading alone; copilot r2/r3 is the closest pair. The headline "run it twice" pairs (r1 vs r2 for each agent) both clear it decisively. Cross-agent comparison: out of scope per plan.

## Requirement-interpretation variance (candidate essay material)

The spec's requirement 4 ambiguity ("approver cannot approve own invoice" implies approvers might submit) was resolved **three different ways by the same agent**: claude r1/r2 forbid approver submission entirely; claude r3 allows it and guards at approval time. The smoke runs (excluded, but color): claude 0/0b explicitly argued for allowing it. Same brief, opposite policy decisions.

## Scanner results (2026-07-11)

Semgrep 1.169.0 (`p/default`, via uvx) and gitleaks 8.30.1, identical invocation per run:

| Run | Semgrep | Gitleaks |
|---|---|---|
| claude r1 | 1 INFO (csurf middleware note) | 0 |
| claude r2 | 1 INFO (csurf middleware note) | 0 |
| claude r3 | 0 | 0 |
| copilot r1 | 2 ERROR (raw-SQL rule) | 0 |
| copilot r2 | 2 ERROR (raw-SQL rule) | 0 |
| copilot r3 | 2 ERROR (raw-SQL rule) | 0 |

**Manual verification of the ERROR findings:** all six flagged sites are the same idiom — WHERE clause assembled from hardcoded fragments, values passed as `?` parameters. **Not injectable; false positives.** All three Copilot runs independently converged on this identical query-building pattern.

**Scanner blind spot worth noting:** claude r2's hardcoded fallback JWT secret (`dev-insecure-secret`, used when JWT_SECRET is unset) was caught by neither tool — found only by code reading. Criterion 5 (differing security findings) therefore rests on the manual finding, not scanner output; state it that way in the essay.

Raw outputs: `analysis/semgrep-summary.txt`, `analysis/gitleaks-summary.txt`, `/tmp/gl-*.json`.

## Requirement-coverage matrix (acceptance checks, executed 2026-07-11)

Scripted HTTP execution per the deviations log; per-run detail in `acceptance/results-*.md`, adapter scripts in `acceptance/check-*.py`. All six services started from their README alone and passed their own test suites.

| Check | cl-r1 | cl-r2 | cl-r3 | cp-r1 | cp-r2 | cp-r3 |
|---|---|---|---|---|---|---|
| 1 Sign-in + roles | pass | pass | pass | pass | pass | pass |
| 2 Invoice creation (attachment retrievable) | **partial** | pass | **partial** | **partial** | pass | pass |
| 3 Lifecycle | pass | pass | pass | pass | pass | pass |
| 4 No self-approval | pass | pass | pass | pass | pass | pass |
| 5 Two-approver threshold (incl. 10k boundary) | pass | pass | pass | pass | pass | pass |
| 6 Append-only audit log | pass | pass | pass | pass | pass* | pass |
| 7 PII erasure + read logging | pass | pass | pass | pass | pass | pass |
| 8 Pending list + filters | pass | pass | pass | pass | pass | pass |
| 9 Notification stub | pass | pass | pass | pass | pass | pass |
| 10 README + tests | pass | pass | pass | pass | pass | pass |
| **Total** | 9P/1p | **10P** | 9P/1p | 9P/1p | **10P*** | **10P** |

\* cp-r2 check 6 regraded from partial: PUT/DELETE returned 501 Not Implemented, which satisfies "no modification capability" (status-code strictness in the script, human judgment applied).

The one recurring partial: three of six systems store the uploaded PDF but expose no way to retrieve it (claude r1/r3 store a path with no download route; copilot r1 likewise). The other three added a download endpoint (claude r2, copilot r3) or return the document inline (copilot r2). Same spec sentence, six different readings of whether an attachment must come back out.

**Functional bottom line for the essay:** every run correctly implemented the hard rules — the 10,000 boundary, dual approval, self-approval prohibition, append-only audit, PII read-logging — while no two runs agreed on architecture, API shape, auth mechanism, or dependency philosophy. The variance is not in whether the system works; it is in what system you get.

## Remaining

- [x] Scanners (semgrep 1.169.0 default rules, gitleaks 8.30.1) over all six runs
- [x] Acceptance checks x6 (scripted HTTP; deviation recorded) — Mikko to review results
- [x] Coverage matrix assembled
- [ ] Essay draft (write-taiga-content pipeline)
