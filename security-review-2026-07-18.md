# Security review: One spec, six systems

**Review date:** 18 July 2026  
**Repository:** [mikromikko/one-spec-six-systems](https://github.com/mikromikko/one-spec-six-systems)  
**Reviewed snapshot:** [`aa1f22fa04d6dfb5acd701194b0aa7a5fc4b3a4a`](https://github.com/mikromikko/one-spec-six-systems/tree/aa1f22fa04d6dfb5acd701194b0aa7a5fc4b3a4a)  
**In scope:** `runs/claude/run1..3` and `runs/copilot/run1..3`  
**Out of scope:** smoke runs `run0`/`run0b`, the agent products themselves, production infrastructure that is not present in the repository, and attacks requiring direct write access to the SQLite files or host filesystem  
**Review type:** source-assisted application-security review, dependency audit, test execution, and targeted verification. This is not a production penetration test.

> Context for this review: the experiment is described in [One spec in. Six systems out.](https://tai.ga/en/resources/one-spec-six-systems/) (Part 1), and the findings below are the basis for the follow-up, [Six systems passed their tests. None was safe to deploy as shipped.](https://tai.ga/en/resources/one-spec-six-systems-part-2/) (Part 2). Published by [Taiga](https://tai.ga/).

## Executive conclusion

None of the six systems is safe to deploy as shipped, even on a nominally internal network.

The most important result is not a dependency CVE or a cryptographic mistake. It is repeated failure to define and enforce trust boundaries:

- Four systems let an unauthenticated caller create an account while choosing the `approver` role. This is direct vertical privilege escalation.
- Five systems have clear object-level authorization failures: changing a sequential invoice ID is enough for one authenticated user to read another user's invoice data, read or erase personal data, inspect audit records, and in two systems download the PDF.
- All six omit login throttling and production-grade session expiry. Several ship public, known passwords or hardcoded signing secrets.
- Three Python implementations have no request-body or attachment-size limit. Two use standard-library reference/development HTTP servers that are especially easy to exhaust.
- Copilot run 1 pins a currently vulnerable multipart parser. Two directly reachable denial-of-service advisories are High severity.

The six functional suites still pass. I ran all of them successfully: 71 named tests, plus one helper module counted by Node's test runner. This distinction matters: the applications implement the functional specification, but the specification and acceptance checks do not test hostile-user boundaries.

## Ranking: worst to best

The score is a comparative **threat score from 0 to 100**, not CVSS and not a probability of compromise. Higher is worse. It weights the shipped attack surface as follows:

| Dimension | Maximum | What raises the score |
|---|---:|---|
| Identity and vertical privilege | 25 | Public role selection, forgeable identities, known privileged credentials |
| Object authorization and data protection | 25 | Cross-user invoice/PDF/PII/audit access or destructive erasure |
| Session and credential security | 20 | Hardcoded secrets, non-expiring sessions, insecure cookie/session design |
| Availability, upload, and input handling | 20 | Unbounded bodies, parser CVEs, process crashes, blocking password work |
| Client, runtime, and deployment hardening | 10 | Stored XSS, unsafe development server, broad bind, missing hardening |

Exposure is scored using the default README startup. The Claude/Node servers omit the bind host and therefore listen on the unspecified address (`::` or `0.0.0.0`), while the Copilot/Python servers default to `127.0.0.1`. A reverse proxy or a changed bind address removes much of that local-only mitigation.

| Rank | Run | Score | Worst concise reason |
|---:|---|---:|---|
| 1 | **Claude run 2** | **92** | Public approver creation, a published fallback JWT secret that permits token forgery, broad invoice/PDF/PII access, and stored XSS |
| 2 | **Copilot run 1** | **87** | Public approver creation, cross-user PII erasure, unbounded upload buffering, and currently vulnerable multipart dependencies |
| 3 | **Copilot run 3** | **85** | Public approver creation, global PDF/PII/audit access, and an unbounded single-threaded request server |
| 4 | **Claude run 3** | **76** | Public approver creation plus known privileged accounts, a hardcoded session fallback, and a development-only in-memory session store |
| 5 | **Claude run 1** | **67** | Cross-user PII access/erasure, known credentials on an all-interface listener, and a verified process-terminating input path |
| 6 | **Copilot run 2** | **64** | Best only comparatively: known privileged credentials, full cross-user PDF/PII disclosure, and unbounded threaded request handling remain serious |

Score breakdown:

| Run | Identity /25 | Object and data /25 | Session /20 | Availability /20 | Runtime /10 | Total |
|---|---:|---:|---:|---:|---:|---:|
| Claude run 2 | 25 | 25 | 20 | 12 | 10 | **92** |
| Copilot run 1 | 25 | 23 | 12 | 20 | 7 | **87** |
| Copilot run 3 | 25 | 25 | 10 | 20 | 5 | **85** |
| Claude run 3 | 25 | 17 | 17 | 12 | 5 | **76** |
| Claude run 1 | 15 | 22 | 13 | 14 | 3 | **67** |
| Copilot run 2 | 15 | 25 | 12 | 10 | 2 | **64** |

Small score differences should not be read as statistically significant. The useful tiers are: Claude run 2 is distinctly worst; Copilot runs 1 and 3 form the next high-risk group; Claude run 3 follows; Claude run 1 and Copilot run 2 are the least dangerous but still non-deployable.

### Top three at a glance

| Run | #1 | #2 | #3 |
|---|---|---|---|
| Claude run 1 | **High:** cross-user invoice/PII/audit access and erasure | **High:** public known credentials on all interfaces | **High:** verified sub-cent input process crash |
| Claude run 2 | **Critical:** unauthenticated approver creation | **Critical:** forgeable JWTs under shipped fallback secret | **High:** global invoice/PDF/PII/audit access and erasure |
| Claude run 3 | **Critical:** unauthenticated approver creation | **High:** auto-seeded known privileged accounts | **High:** fallback session secret and development-only session store |
| Copilot run 1 | **Critical:** unauthenticated approver creation | **High:** cross-user PII access/erasure and global audit | **High:** unbounded uploads plus reachable parser CVEs |
| Copilot run 2 | **High:** global PDF/PII/audit access and erasure | **High:** auto-seeded known privileged accounts | **High/Medium:** unbounded threaded request-body handling |
| Copilot run 3 | **Critical:** unauthenticated approver creation | **High:** global PDF/PII/audit access and erasure | **High/Medium:** unbounded single-threaded request-body handling |

## Threat model and severity meanings

The primary attacker is an unauthenticated person or ordinary employee who can reach the HTTP service. Secondary attackers are a malicious submitter, a compromised ordinary account, a hostile webpage targeting a browser using the service, and a local user/process where the server is loopback-only.

- **Critical:** a low-complexity, unauthenticated path to privileged workflow control or broad sensitive data, assuming the service is reachable.
- **High:** a practical loss of confidentiality, integrity, or availability, commonly requiring only an ordinary account.
- **Medium:** a material weakness with a stronger precondition, narrower impact, or meaningful default-localhost mitigation.
- **Low:** defense-in-depth or deployment hardening that is unlikely to be the first successful attack.

The invoice IDs are sequential database primary keys in every run. Guessing them is not considered a security barrier. OWASP's [IDOR guidance](https://cheatsheetseries.owasp.org/cheatsheets/Insecure_Direct_Object_Reference_Prevention_Cheat_Sheet.html) requires authorization for each requested object and for every operation on it.

## Cross-system findings

### 1. The role model was implemented, but account provisioning was not secured

Claude runs 2 and 3 and Copilot runs 1 and 3 expose unauthenticated user creation and accept the role from the request. An attacker can request `approver`, log in, and immediately act on other users' invoices. Where an IDOR exists, the attacker can also read or erase other users' data. Where object checks exist, as in Claude run 3, becoming an approver intentionally satisfies those checks.

This is a useful specification finding. “Users sign in” does not specify how identities or roles are provisioned. Four agents filled that gap with a public bootstrap API that remains enabled forever. A secure specification should explicitly say that users are provisioned by an administrator or trusted identity provider and that clients can never select their own role.

### 2. Authentication was mistaken for authorization

Five runs routinely call `getInvoice(id)` or `SELECT ... WHERE id = ?`, then return or mutate the record after checking only that some user is authenticated. Ordinary submitters can therefore change `1` to `2` and cross ownership boundaries. The affected operations include:

- invoice metadata;
- counterparty contact email;
- destructive erasure of that email;
- audit history and actor identities;
- PDF content in Copilot runs 2 and 3;
- PDF download in Claude run 2, for any authenticated role.

Claude run 3 is the partial exception: it scopes submitters to their own invoices on detail, PII, erasure, and audit routes. It still grants every approver organization-wide access, including PII erasure, and its public approver registration collapses that boundary.

OWASP recommends deny-by-default authorization and permission checks on every request, not merely most routes ([Authorization Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Authorization_Cheat_Sheet.html)).

### 3. Personal-data “access logging” does not make the access legitimate

All systems correctly log reads of the counterparty email. Most do not first determine whether the reader is entitled to that personal data. This creates a complete record of a breach rather than preventing one. Likewise, an “erasure on request” endpoint should not mean that every authenticated user is a data-protection administrator.

A production policy needs at least:

- submitter access only to invoices they own;
- approver access only where required for a pending decision;
- a distinct, authorized erasure workflow with request identity, approval, and scope;
- data-minimized audit output rather than a global employee directory;
- authorization-denial events in the audit trail.

### 4. Sessions are valid indefinitely or until restart/browser close

The opaque bearer-token systems store random tokens but give them no expiry and usually no logout route. Claude run 2's home-grown JWT has no `exp`, issuer, audience, rotation, or revocation. Claude run 3 uses a browser-session cookie but no server-side TTL and the default `MemoryStore`.

Long-lived bearer tokens stored in plaintext in SQLite are reusable credentials after database disclosure. A practical system needs short idle and absolute lifetimes, logout/revocation, rotation after authentication, hashed server-side token storage where appropriate, and TLS.

### 5. Every login is brute-forceable and computational work is attacker-controlled

All six use respectable password primitives—scrypt or PBKDF2 with per-user salts—and constant-time digest comparison. None rate-limits login. Most avoid differing error text, but all skip the password hash when the email does not exist, leaving a timing-based account-enumeration signal. The Node runs call synchronous scrypt on the event-loop thread. The Python runs perform synchronous PBKDF2 in request-handling capacity.

OWASP recommends login throttling and carefully designed lockout/backoff controls ([Authentication Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html)).

### 6. Upload checks are superficial and limits are inconsistent

The Node runs cap files at 20–25 MiB but trust the user-controlled MIME type, extension, or either. The Python runs check only the first four bytes (`%PDF`). Copilot runs 1–3 do not impose an application-level file or request size limit; runs 2 and 3 place the entire document in SQLite and later construct whole-document base64 or byte responses in memory.

OWASP recommends layered extension, media-type, signature, content, filename, authorization, storage, and size controls rather than any single check ([File Upload Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/File_Upload_Cheat_Sheet.html)). For invoices, malware scanning or PDF content-disarm/reconstruction is appropriate before a document reaches approvers.

### 7. The audit-log design is one of the strongest repeated choices

All six create SQLite `BEFORE UPDATE` and `BEFORE DELETE` triggers for the audit table. State transition and audit insertion are transactional in several implementations. Amounts are stored as cents, distinct approvals have uniqueness constraints, state and role fields have `CHECK` constraints, and SQL values are parameterized. Existing Semgrep “raw SQL” reports against the Copilot runs are false positives because only hardcoded clause fragments are interpolated and user values remain bound parameters.

The triggers prevent accidental or application-level row mutation. They do not make SQLite tamper-evident against a host or database administrator: an attacker with database-file control can drop the triggers, alter the file, replace the database, or suppress future entries. Production audit evidence should be shipped to a separately controlled, append-oriented log system and monitored.

## Detailed review: Claude run 1

**Threat score: 67/100 — fifth (second best), but not deployable.**  
**Architecture:** Express 4, Multer, `node:sqlite`, opaque bearer tokens in a sessions table, filesystem PDFs.  
**Default exposure:** all network interfaces because [`app.listen(port)` omits a host](https://github.com/mikromikko/one-spec-six-systems/blob/aa1f22fa04d6dfb5acd701194b0aa7a5fc4b3a4a/runs/claude/run1/src/server.js#L5-L9).

### Top 3 threats

#### CL1-1 — High — Broken object-level authorization permits cross-user reads and PII erasure

**Evidence:** [`GET /invoices/:id`](https://github.com/mikromikko/one-spec-six-systems/blob/aa1f22fa04d6dfb5acd701194b0aa7a5fc4b3a4a/runs/claude/run1/src/app.js#L227-L231), [`GET/DELETE .../counterparty-email`](https://github.com/mikromikko/one-spec-six-systems/blob/aa1f22fa04d6dfb5acd701194b0aa7a5fc4b3a4a/runs/claude/run1/src/app.js#L299-L328), and [`GET .../audit`](https://github.com/mikromikko/one-spec-six-systems/blob/aa1f22fa04d6dfb5acd701194b0aa7a5fc4b3a4a/runs/claude/run1/src/app.js#L330-L335) require authentication but never compare the invoice's submitter to the current user or apply a per-operation policy.

**Attack:** log in as any submitter, enumerate invoice IDs, read other submitters' invoice metadata and contact emails, delete their contact emails, and read audit trails. The PII reads are logged, but they are not denied.

**Impact:** cross-employee confidentiality breach, unauthorized personal-data processing, destructive integrity loss, and audit metadata disclosure.

**Fix:** centralize an invoice authorization function and call it before every read, download, PII, audit, and mutation. Use ownership/pending-assignment predicates directly in SQL so an unauthorized row is never loaded.

#### CL1-2 — High — Known demo credentials are exposed on a network-wide listener

**Evidence:** four predictable accounts use `password123` in [`src/seed.js`](https://github.com/mikromikko/one-spec-six-systems/blob/aa1f22fa04d6dfb5acd701194b0aa7a5fc4b3a4a/runs/claude/run1/src/seed.js#L7-L12), the README publishes them, and the server does not bind to loopback explicitly.

Node documents that omitting the host listens on `::` when available or `0.0.0.0` otherwise ([Node `net.Server.listen`](https://nodejs.org/api/net.html#serverlistenport-host-backlog-callback)). A machine following the README is therefore reachable by network peers unless the host firewall intervenes.

**Attack:** use the published approver credentials from another machine on the same reachable network, then approve/reject invoices and exploit CL1-1.

**Impact:** immediate privileged account compromise in the default demo deployment.

**Fix:** never seed fixed production accounts; require an explicit development flag, generate one-time random passwords, force change on first use, and bind `127.0.0.1` unless an operator deliberately configures another trusted interface.

#### CL1-3 — High — A valid sub-cent amount causes an uncaught exception and process termination

**Evidence:** amount validation accepts every finite positive number and rounds later ([`parseAmountToCents`](https://github.com/mikromikko/one-spec-six-systems/blob/aa1f22fa04d6dfb5acd701194b0aa7a5fc4b3a4a/runs/claude/run1/src/app.js#L29-L35)). `0.001` becomes zero, violates the database `CHECK (amount_cents > 0)`, and throws from inside the callback passed to Multer ([invoice route](https://github.com/mikromikko/one-spec-six-systems/blob/aa1f22fa04d6dfb5acd701194b0aa7a5fc4b3a4a/runs/claude/run1/src/app.js#L156-L188)). Express cannot catch an exception raised in this asynchronous callback.

**Verification:** a targeted request with `amount=0.001` produced `ERR_SQLITE_ERROR: CHECK constraint failed: amount_cents > 0` as uncaught asynchronous activity and made the Node test process exit non-zero. In the production Node default, an uncaught exception terminates the process. The uploaded file is also left orphaned because cleanup is not reached.

**Attack:** authenticate as a submitter—easy with the shipped credentials—and upload a small PDF with a sub-cent amount.

**Impact:** repeatable service denial and disk litter.

**Fix:** validate the normalized cents (`Number.isSafeInteger(cents) && cents > 0`), use decimal/string parsing, make Multer a normal route middleware, pass errors to `next(err)`, wrap the database write and audit in a transaction, and delete staged files on every failure path.

### Other findings

| Severity | Finding |
|---|---|
| Medium | Session tokens are random and revocable on logout, but have no idle or absolute expiry and are stored in plaintext ([session schema](https://github.com/mikromikko/one-spec-six-systems/blob/aa1f22fa04d6dfb5acd701194b0aa7a5fc4b3a4a/runs/claude/run1/src/db.js#L53-L57)). |
| Medium | Login has no rate limit and synchronous scrypt blocks the event loop; unknown emails skip scrypt and are timing-distinguishable. |
| Medium | PDF validation accepts either a spoofable MIME type or a `.pdf` suffix; it does not verify a PDF signature or content ([upload configuration](https://github.com/mikromikko/one-spec-six-systems/blob/aa1f22fa04d6dfb5acd701194b0aa7a5fc4b3a4a/runs/claude/run1/src/app.js#L50-L67)). |
| Medium | Invoice insertion and the required audit entry are separate, non-transactional operations. A crash between them leaves an unaudited invoice. |
| Low | No security headers, explicit TLS requirement, request logging, authorization-denial logging, account lifecycle, or database/file permission setup is supplied. |

## Detailed review: Claude run 2

**Threat score: 92/100 — worst.**  
**Architecture:** Express 4, Multer, `node:sqlite`, home-grown HMAC bearer token, filesystem PDFs, static browser UI.  
**Default exposure:** all network interfaces.

### Top 3 threats

#### CL2-1 — Critical — Anyone can register as an approver

**Evidence:** [`POST /api/auth/register`](https://github.com/mikromikko/one-spec-six-systems/blob/aa1f22fa04d6dfb5acd701194b0aa7a5fc4b3a4a/runs/claude/run2/src/app.js#L128-L150) is public, accepts `role`, and permits `approver`. The source comment acknowledges that a real deployment would gate it but does not implement that gate.

**Attack:** send a single unauthenticated JSON request selecting `approver`, log in, then approve/reject every pending invoice. Because the object routes are unscoped, the same account can access attachments, PII, and audit data.

**Impact:** complete workflow-control compromise and broad confidentiality/integrity loss.

**Fix:** remove public role-bearing registration. Provision identities through a trusted administrator/IdP; if self-registration is required, force the least-privileged role and require a separate, audited role-assignment process.

#### CL2-2 — Critical — Published fallback JWT secret permits offline token forgery

**Evidence:** the application uses `process.env.JWT_SECRET || 'dev-insecure-secret'` ([`src/app.js`](https://github.com/mikromikko/one-spec-six-systems/blob/aa1f22fa04d6dfb5acd701194b0aa7a5fc4b3a4a/runs/claude/run2/src/app.js#L31-L36)). Tokens are a custom `base64url(payload).HMAC` construction with no expiry check ([`src/auth.js`](https://github.com/mikromikko/one-spec-six-systems/blob/aa1f22fa04d6dfb5acd701194b0aa7a5fc4b3a4a/runs/claude/run2/src/auth.js#L26-L53)). The login payload contains `sub`, `role`, and `iat`, but no `exp` ([login](https://github.com/mikromikko/one-spec-six-systems/blob/aa1f22fa04d6dfb5acd701194b0aa7a5fc4b3a4a/runs/claude/run2/src/app.js#L152-L165)).

**Verification:** signing `{sub: 1, role: 'approver', iat: 0}` with the published fallback produced a token accepted by the shipped `verifyToken`. The request middleware trusts `sub`, loads that sequential user ID, and does not require a current session row.

**Attack:** if the environment variable is omitted, forge a token for a seeded approver ID. Tokens remain valid indefinitely and cannot be revoked individually.

**Impact:** unauthenticated account impersonation, including privileged users. This remains independently critical even if CL2-1 is fixed.

**Fix:** fail startup unless a high-entropy secret is supplied from a secret manager; use a mature session/token library; validate expiry, issuer, audience, and algorithm; support rotation and revocation. Opaque server-side sessions are simpler for this local internal service.

#### CL2-3 — High — Any account can read every invoice/PDF/PII/audit trail and erase PII

**Evidence:** invoice detail and PDF download load solely by ID ([routes](https://github.com/mikromikko/one-spec-six-systems/blob/aa1f22fa04d6dfb5acd701194b0aa7a5fc4b3a4a/runs/claude/run2/src/app.js#L300-L319)); contact-email read/erase and per-invoice audit do the same ([routes](https://github.com/mikromikko/one-spec-six-systems/blob/aa1f22fa04d6dfb5acd701194b0aa7a5fc4b3a4a/runs/claude/run2/src/app.js#L413-L465)). Only the global audit route requires the approver role.

**Attack:** an ordinary submitter iterates `/api/invoices/1`, `/pdf`, `/contact-email`, and `/audit`, then sends `DELETE` to erase another submitter's contact data.

**Impact:** full document and personal-data disclosure across submitters, destructive modification, and audit intelligence leakage.

**Fix:** enforce object ownership/assignment for every operation and introduce a dedicated erasure permission and workflow.

### Other findings

| Severity | Finding |
|---|---|
| High | Stored XSS: an approver-controlled rejection `reason` is copied to audit details ([server sink](https://github.com/mikromikko/one-spec-six-systems/blob/aa1f22fa04d6dfb5acd701194b0aa7a5fc4b3a4a/runs/claude/run2/src/app.js#L380-L399)) and rendered without escaping through `detailBody.innerHTML` ([browser sink](https://github.com/mikromikko/one-spec-six-systems/blob/aa1f22fa04d6dfb5acd701194b0aa7a5fc4b3a4a/runs/claude/run2/public/index.html#L166-L176)). A malicious approver can execute script in a submitter's authenticated browser and steal the in-memory bearer token. No CSP is set. |
| Medium | Approval, state update, audit insertion, and notification are not wrapped in one transaction/outbox operation. Multi-process use or failures can create partial records. |
| Medium | Upload validation trusts only the multipart MIME header and allows 20 MiB; content is not parsed/scanned. |
| Medium | Public registration and login accept weak/very long passwords, use synchronous scrypt, and have no rate limiting. |
| Low | The generic error handler returns raw exception messages, potentially leaking filesystem/database detail. No security headers or explicit TLS requirement are supplied. |

## Detailed review: Claude run 3

**Threat score: 76/100 — fourth.**  
**Architecture:** Express 4, `express-session`, Multer, `node:sqlite`, filesystem PDFs, static browser UI.  
**Default exposure:** all network interfaces.

### Top 3 threats

#### CL3-1 — Critical — Public registration allows self-assignment of the approver role

**Evidence:** [`POST /api/auth/register`](https://github.com/mikromikko/one-spec-six-systems/blob/aa1f22fa04d6dfb5acd701194b0aa7a5fc4b3a4a/runs/claude/run3/src/app.js#L82-L95) is unauthenticated and inserts the caller-supplied `role`.

**Attack and impact:** identical vertical escalation to CL2-1. This run's submitter ownership checks are better, but an attacker simply registers as an approver, after which the code intentionally permits organization-wide invoice, PII, erasure, and audit access.

**Fix:** trusted provisioning only; never accept an authorization role from an unauthenticated client.

#### CL3-2 — High — Multiple known privileged accounts are auto-created on every fresh start

**Evidence:** the server automatically calls the seed function ([`server.js`](https://github.com/mikromikko/one-spec-six-systems/blob/aa1f22fa04d6dfb5acd701194b0aa7a5fc4b3a4a/runs/claude/run3/server.js#L5-L16)); three approver accounts and two submitters all use `password123` ([`src/seed.js`](https://github.com/mikromikko/one-spec-six-systems/blob/aa1f22fa04d6dfb5acd701194b0aa7a5fc4b3a4a/runs/claude/run3/src/seed.js#L4-L11)). The all-interface listener makes those credentials reachable to network peers.

**Attack:** log in with a public approver credential and control invoice decisions or erase PII.

**Impact:** privileged compromise without exploiting registration.

**Fix:** development seeding must be opt-in and must not use static credentials; production startup should refuse a demo-data mode.

#### CL3-3 — High — Session security uses a published fallback and a development-only store

**Evidence:** [`express-session`](https://github.com/mikromikko/one-spec-six-systems/blob/aa1f22fa04d6dfb5acd701194b0aa7a5fc4b3a4a/runs/claude/run3/src/app.js#L63-L73) falls back to `dev-secret-change-me`, does not set `cookie.secure` or a TTL, and does not configure a store. The package therefore uses `MemoryStore`.

The official middleware documentation says `MemoryStore` is intentionally not designed for production, leaks memory under most conditions, and cannot scale past one process. It also recommends a random secret with at least 32 bytes of entropy and secure cookies over HTTPS ([express-session documentation](https://expressjs.com/en/resources/middleware/session/)).

**Impact:** memory-exhaustion exposure through accumulated sessions, loss of all sessions on restart, no horizontal scaling, cookies usable over plaintext HTTP, and weakened cookie-signature defense. Knowledge of the secret alone does not reveal the server-side session data or create an existing session ID, so this is less immediately exploitable than Claude run 2's stateless JWT flaw.

**Fix:** require a high-entropy secret, rotate it, use a durable bounded store with TTL, regenerate the session on login, enforce idle/absolute expiry, set `Secure`, and serve only over TLS.

### Other findings

| Severity | Finding |
|---|---|
| High | Any approver may read and erase PII for every invoice. If this is intended for approval reads, erasure is still an excessive privilege; after public approver registration it becomes attacker-controlled ([PII routes](https://github.com/mikromikko/one-spec-six-systems/blob/aa1f22fa04d6dfb5acd701194b0aa7a5fc4b3a4a/runs/claude/run3/src/app.js#L203-L245)). |
| Medium | Upload validation trusts MIME only, preserves the attacker-chosen extension after a random UUID, and leaves files behind on all validation failures after Multer has written them ([upload and route](https://github.com/mikromikko/one-spec-six-systems/blob/aa1f22fa04d6dfb5acd701194b0aa7a5fc4b3a4a/runs/claude/run3/src/app.js#L45-L61)). |
| Medium | State update, decision insertion, audit insertion, and notification lack a transaction/outbox boundary. |
| Medium | Registration performs almost no email/password validation; login has no throttling and synchronous scrypt blocks the event loop. |
| Medium | Cookie `SameSite=Lax` provides useful cross-site protection, but there is no CSRF token or Origin validation for same-site/subdomain attack scenarios. |

## Detailed review: Copilot run 1

**Threat score: 87/100 — second worst.**  
**Architecture:** FastAPI 0.124.2, Uvicorn 0.38.0, SQLite, opaque bearer sessions, filesystem PDFs.  
**Default exposure:** Uvicorn's default `127.0.0.1`, but the README uses development `--reload` mode.

### Top 3 threats

#### CP1-1 — Critical — Public user creation accepts `role=approver`

**Evidence:** [`POST /users`](https://github.com/mikromikko/one-spec-six-systems/blob/aa1f22fa04d6dfb5acd701194b0aa7a5fc4b3a4a/runs/copilot/run1/app/main.py#L301-L316) has no authentication/authorization dependency and writes the request's role.

**Verification:** a targeted unauthenticated request creating `evil-approver@example.com` with role `approver` returned HTTP 201.

**Impact and fix:** direct vertical privilege escalation; remove the endpoint or restrict it to trusted administrative provisioning.

#### CP1-2 — High — Any account can read and erase any invoice's PII and list all audit data

**Evidence:** invoice detail, PII read, and PII erase load by ID with no owner/role check ([routes](https://github.com/mikromikko/one-spec-six-systems/blob/aa1f22fa04d6dfb5acd701194b0aa7a5fc4b3a4a/runs/copilot/run1/app/main.py#L421-L466)). [`GET /audit-logs`](https://github.com/mikromikko/one-spec-six-systems/blob/aa1f22fa04d6dfb5acd701194b0aa7a5fc4b3a4a/runs/copilot/run1/app/main.py#L544-L568) exposes the complete audit table to any token.

**Verification:** after a victim submitter created an invoice, a different submitter received HTTP 200 for invoice detail, contact email, PII erasure, and global audit listing.

**Impact:** cross-user invoice/PII disclosure and deletion, plus organization-wide behavioral metadata. This run lacks a PDF download route, limiting document-content disclosure but not fixing the authorization failure.

**Fix:** object-scoped policy for all routes and a separately authorized erasure workflow.

#### CP1-3 — High — Unbounded upload buffering combines with reachable vulnerable multipart parsers

**Application evidence:** [`contents = await pdf.read()`](https://github.com/mikromikko/one-spec-six-systems/blob/aa1f22fa04d6dfb5acd701194b0aa7a5fc4b3a4a/runs/copilot/run1/app/main.py#L333-L355) loads the entire accepted file into process memory, then writes it to disk. No app/proxy body limit is documented.

**Dependency evidence:** [`requirements.txt`](https://github.com/mikromikko/one-spec-six-systems/blob/aa1f22fa04d6dfb5acd701194b0aa7a5fc4b3a4a/runs/copilot/run1/requirements.txt#L1-L6) pins `python-multipart==0.0.20`. Current `pip-audit` resolved 14 advisory records across `python-multipart`, Starlette, and pytest. At least these parser issues are directly reachable by the form/file endpoint:

- [CVE-2026-42561 / GHSA-pp6c-gr5w-3c5g](https://github.com/advisories/GHSA-pp6c-gr5w-3c5g), High 7.5: CPU exhaustion through unbounded multipart part headers; fixed in `0.0.27`.
- [CVE-2026-53539 / GHSA-5rvq-cxj2-64vf](https://github.com/advisories/GHSA-5rvq-cxj2-64vf), High 7.5: quadratic `application/x-www-form-urlencoded` parsing; fixed in `0.0.30`.
- [CVE-2026-40347 / GHSA-mj87-hwqh-73pj](https://github.com/advisories/GHSA-mj87-hwqh-73pj), Moderate 5.3: large multipart preamble/epilogue CPU consumption; fixed in `0.0.26`.

FastAPI/Starlette parse the form body for this endpoint, and a hostile body is processed before the endpoint can rely on its PDF checks. Public account creation also makes any submitter-only precondition trivial.

**Impact:** worker/event-loop exhaustion, process memory pressure, and disk exhaustion.

**Fix:** upgrade `python-multipart` to at least `0.0.31` as of this review, upgrade FastAPI/Starlette on a tested compatible set, enforce small proxy and application request/file limits, stream to quarantine instead of `read()`-all, and scan/validate before persistence.

### Other findings

| Severity | Finding |
|---|---|
| Medium | Opaque sessions have no expiry or logout and are stored as plaintext bearer credentials ([schema and lookup](https://github.com/mikromikko/one-spec-six-systems/blob/aa1f22fa04d6dfb5acd701194b0aa7a5fc4b3a4a/runs/copilot/run1/app/main.py#L60-L64)). |
| Medium | User creation and login run expensive PBKDF2 without throttling. Passwords have a minimum but no maximum length; unknown emails skip PBKDF2. |
| Medium | A file is written before the database transaction. A later database failure leaves an orphan. Files and the database use ambient OS permissions. |
| Low | The README starts Uvicorn with `--reload`, which is a development posture. There are no security headers, TLS requirements, or denial-event logs. |
| Informational | `pip-audit` also reports advisories whose stated prerequisites are not present here (for example, `python-multipart` path traversal requires non-default keep-filename settings; several Starlette issues require `request.url`, `StaticFiles`, or `HTTPEndpoint` patterns this app does not use; pytest is test-only). They should still be cleared through compatible upgrades rather than counted as proven app exploits. |

## Detailed review: Copilot run 2

**Threat score: 64/100 — best, but still high risk.**  
**Architecture:** Python standard library `ThreadingHTTPServer`, SQLite, opaque bearer sessions, PDF BLOBs.  
**Default exposure:** fixed to `127.0.0.1`.

### Top 3 threats

#### CP2-1 — High — Any authenticated user can retrieve every PDF and PII value, erase PII, and list audit data

**Evidence:** [`get_invoice`](https://github.com/mikromikko/one-spec-six-systems/blob/aa1f22fa04d6dfb5acd701194b0aa7a5fc4b3a4a/runs/copilot/run2/invoice_service.py#L244-L260) does no policy check and returns the contact email plus the entire PDF as base64. [`erase_counterparty_contact_email` and `audit_log`](https://github.com/mikromikko/one-spec-six-systems/blob/aa1f22fa04d6dfb5acd701194b0aa7a5fc4b3a4a/runs/copilot/run2/invoice_service.py#L339-L373) accept any authenticated actor.

**Attack:** log in with any account, enumerate IDs, and download all invoice documents and PII; erase contacts; list all audit entries including actor emails.

**Impact:** the broadest single authenticated data breach among the otherwise “best” implementations.

**Fix:** owner/assignment checks on every object operation, no inline whole-document API response, and a distinct erasure privilege.

#### CP2-2 — High — Startup always seeds published privileged credentials

**Evidence:** the service auto-seeds `approver1@example.com` and `approver2@example.com` with `password123` ([defaults](https://github.com/mikromikko/one-spec-six-systems/blob/aa1f22fa04d6dfb5acd701194b0aa7a5fc4b3a4a/runs/copilot/run2/invoice_service.py#L16-L20), [initialization](https://github.com/mikromikko/one-spec-six-systems/blob/aa1f22fa04d6dfb5acd701194b0aa7a5fc4b3a4a/runs/copilot/run2/invoice_service.py#L156-L159)).

**Mitigating fact:** the shipped server is hard-bound to loopback ([`app.py`](https://github.com/mikromikko/one-spec-six-systems/blob/aa1f22fa04d6dfb5acd701194b0aa7a5fc4b3a4a/runs/copilot/run2/app.py#L121-L126)). That reduces remote exposure until a reverse proxy is added; it does not protect against local processes/users or a deployment adaptation.

**Impact:** immediate privileged compromise wherever the API is made reachable.

**Fix:** remove automatic demo identities from normal startup and make any development seed opt-in with random one-time credentials.

#### CP2-3 — High if network-exposed; Medium at default loopback — Unbounded threaded request reads enable memory and connection exhaustion

**Evidence:** `_body()` trusts attacker-controlled `Content-Length` and calls `self.rfile.read(length)` with no maximum or socket timeout ([`app.py`](https://github.com/mikromikko/one-spec-six-systems/blob/aa1f22fa04d6dfb5acd701194b0aa7a5fc4b3a4a/runs/copilot/run2/app.py#L102-L106)). `ThreadingHTTPServer` creates concurrent handlers without an application-level connection cap. This body read occurs on unauthenticated login requests. A negative length reaches `read(-1)`, which reads until EOF.

**Attack:** open many slow requests with large or negative content lengths, or send huge JSON/base64 bodies. Threads block waiting or allocate memory. Authenticated invoice responses additionally base64-encode whole BLOBs in memory.

**Impact:** thread, memory, CPU, and database/disk exhaustion.

**Fix:** use a production HTTP server/reverse proxy with header/body/time/concurrency limits; reject missing, negative, or oversized content lengths before reading; stream files; impose quotas and rate limits.

### Other findings

| Severity | Finding |
|---|---|
| Medium | Sessions have no expiration or logout and plaintext tokens accumulate indefinitely. |
| Medium | Login performs synchronous PBKDF2 without throttling and reveals account existence through timing. |
| Medium | The audit API exposes all actor email addresses to any token. Email-log appends are not synchronized across request threads. |
| Medium | PDF validation is just filename suffix plus `%PDF`; there is no file/body limit or malware/content validation. |
| Low | Python documents that `http.server` is unsuitable for production and implements only basic security checks ([Python security considerations](https://docs.python.org/3/library/security_warnings.html)). |

## Detailed review: Copilot run 3

**Threat score: 85/100 — third worst.**  
**Architecture:** Python standard library `wsgiref`, SQLite, opaque bearer sessions, PDF BLOBs.  
**Default exposure:** `127.0.0.1`, configurable with `HOST`.

### Top 3 threats

#### CP3-1 — Critical — Public `/users` lets the caller choose `approver`

**Evidence:** [`create_user`](https://github.com/mikromikko/one-spec-six-systems/blob/aa1f22fa04d6dfb5acd701194b0aa7a5fc4b3a4a/runs/copilot/run3/app.py#L330-L347) accepts the client role, and the WSGI router exposes it before authentication ([route](https://github.com/mikromikko/one-spec-six-systems/blob/aa1f22fa04d6dfb5acd701194b0aa7a5fc4b3a4a/runs/copilot/run3/app.py#L580-L598)). The README explicitly instructs callers to create approver users.

**Impact and fix:** direct vertical privilege escalation; move provisioning behind a trusted administrator/IdP and never accept self-assigned roles.

#### CP3-2 — High — Global invoice listing, PDF download, PII erasure, and audit data are available to every token

**Evidence:** after one bearer-token check, the router exposes all invoices, all audit entries, ID-based invoice detail, ID-based PDF BLOBs, and contact read/delete with no object policy ([routes](https://github.com/mikromikko/one-spec-six-systems/blob/aa1f22fa04d6dfb5acd701194b0aa7a5fc4b3a4a/runs/copilot/run3/app.py#L598-L642)). `list_invoices`, PII, and audit functions themselves also have no actor argument/policy.

**Attack:** create a low-privilege submitter account, log in, list every ID, download each PDF, inspect audit actor emails, and erase contact data. Creating an approver is not even necessary for the disclosure/destruction path.

**Impact:** complete authenticated document/PII confidentiality loss and destructive integrity loss.

**Fix:** object-scoped data access and separate authorization for list, PDF, PII, audit, and erasure.

#### CP3-3 — High if network-exposed; Medium at default loopback — One slow or huge unauthenticated body blocks the single server

**Evidence:** [`parse_json`](https://github.com/mikromikko/one-spec-six-systems/blob/aa1f22fa04d6dfb5acd701194b0aa7a5fc4b3a4a/runs/copilot/run3/app.py#L179-L191) reads the declared body in one call with no maximum. The public user/login routes parse before authentication. [`wsgiref.simple_server.make_server`](https://github.com/mikromikko/one-spec-six-systems/blob/aa1f22fa04d6dfb5acd701194b0aa7a5fc4b3a4a/runs/copilot/run3/app.py#L653-L659) is single-threaded by default and has no supplied read timeout or concurrency/body limiter.

Python explicitly calls `wsgiref` a reference implementation that is not recommended for production and performs only basic security checks ([official `wsgiref` documentation](https://docs.python.org/3.11/library/wsgiref.html)).

**Attack:** send a slow request declaring a body and never finish it; the only request thread blocks, denying all other users. A completed large base64 PDF can exhaust memory and SQLite/disk.

**Impact:** trivial whole-service denial once reachable.

**Fix:** production server/proxy, strict body/header/time limits, streaming, and quotas.

### Other findings

| Severity | Finding |
|---|---|
| Medium | Sessions have no expiry or logout; bearer tokens remain reusable indefinitely. |
| Medium | Passwords can be one character; login has no throttling; synchronous PBKDF2 runs on the single service thread. |
| Medium | PDF data is fully base64-decoded into memory, stored as a BLOB, and fully materialized again on download; validation is only suffix plus `%PDF`. |
| Low | User-controlled `pdf_filename` is inserted directly into `Content-Disposition`. Current patched Python rejects control characters, turning malicious filenames into a 500/download denial; robust code should use a safe filename encoder/allowlist and pin a patched Python release. |
| Low | Database schema initialization runs on every request, increasing cheap unauthenticated work. No security headers, TLS requirement, or denial-event logging are supplied. |

## Dependency and scanner results

### Node runs

On 18 July 2026, `npm audit --package-lock-only --ignore-scripts` reported **0 known vulnerabilities** for each Claude lockfile. This is good, but it does not cover application authorization, secrets, XSS logic, or unsafe defaults.

### Python runs

Copilot runs 2 and 3 use only the standard library, so there is no application dependency manifest to audit. Their security depends on the installed Python patch level; `>=3.11` is too broad for reproducible security maintenance.

For Copilot run 1, current `pip-audit` reported **14 advisory records in 3 resolved packages**. The three directly relevant `python-multipart` denial-of-service advisories are documented under CP1-3. The report deliberately does not treat every raw advisory as an exploitable application finding; configuration and code-path prerequisites were checked.

### Existing repository scanners

The repository's Semgrep 1.169.0 run reported:

- one informational CSRF-middleware note in Claude runs 1 and 2;
- zero findings in Claude run 3;
- two raw-SQL errors in each Copilot run.

The raw-SQL reports are false positives: query structures are assembled from hardcoded fragments, while all user values are placeholders. Gitleaks 8.30.1 reported zero findings in every run. It missed the active `dev-insecure-secret` and `dev-secret-change-me` fallbacks. This is an important experimental result: scanners did not find the worst risks because those risks are authorization and trust-model failures.

## Positive controls worth preserving

The report should not obscure what the generated systems did well:

- All passwords are salted and processed with scrypt or PBKDF2; no plaintext password storage was found.
- Authentication tokens use cryptographically secure random generation except for Claude run 2's signed stateless design, whose HMAC primitive itself is sound when the secret is sound.
- SQL values are parameterized. I found no practical SQL injection in the six runs.
- Money uses integer cents in normal cases and the €10,000 boundary is correctly strict.
- The two-approver rule uses database uniqueness to require distinct user IDs.
- State and role values use database `CHECK` constraints.
- Audit tables have database-level update/delete blocking triggers in all six runs.
- Ordinary invoice serializers generally avoid returning the contact email; the explicit PII route is auditable.
- Node upload filenames are randomized and stored outside the static web root.
- All shipped tests and README startup procedures had already passed the repository's acceptance work; the current review reconfirmed all test suites.

These controls make remediation easier. The systems are not cryptographic disasters; they mainly need explicit identity, authorization, resource, and deployment boundaries.

## Remediation plan across all six systems

### P0 — before any shared/internal deployment

1. **Close identity provisioning.** Delete public role-bearing registration. Integrate a trusted IdP or authenticated admin provisioning. Remove automatic static demo credentials.
2. **Define an authorization matrix.** Specify, per role and invoice state, who may list, view metadata, download PDF, read PII, erase PII, view audit, approve, and reject. Deny by default.
3. **Enforce object scope centrally.** Query through `invoice_id + allowed current-user scope`, not a global `id` lookup followed by ad hoc checks. Apply it to every method and role.
4. **Fix secrets and sessions.** Refuse startup with fallback secrets; set short idle and absolute lifetimes; implement logout/revocation and rotation; store server-side token digests where applicable; use TLS only.
5. **Remove known credentials and broad accidental exposure.** Bind loopback by default; require explicit trusted-network configuration; do not print or document fixed passwords.
6. **Patch Copilot run 1 dependencies.** At minimum, replace `python-multipart 0.0.20` with a compatible `>=0.0.31` set and update FastAPI/Starlette after compatibility testing.

### P1 — required hardening

1. Put every service behind a maintained production HTTP server/reverse proxy with strict body, header, connection, concurrency, and time limits.
2. Stream uploads to a quarantine area; cap size and per-user quota; validate PDF structure; scan or content-disarm; rename safely; clean up failures.
3. Add login and registration throttling/backoff, password maximum lengths, account lifecycle controls, and uniform password verification timing.
4. Make state change + audit insertion atomic. Use an outbox/worker for notification so email failure cannot corrupt transaction semantics.
5. Encode every browser-rendered value, remove unsafe `innerHTML`, and deploy a restrictive Content Security Policy.
6. Set secure headers and cookies; use `Secure`, `HttpOnly`, suitable `SameSite`, CSRF/Origin defenses for cookie-authenticated state changes, and HSTS at the TLS edge.
7. Ship audit events off-host to a separately controlled store and monitor privilege creation, authorization denial, PII reads/erasure, login abuse, and unusual download volume.

### P2 — security quality gates

Add negative tests that the original acceptance suite lacks:

- unauthenticated callers cannot create users or select roles;
- submitter A cannot read, download, audit, or erase submitter B's invoice;
- an approver sees only the objects and fields explicitly allowed by policy;
- erasure requires the designated permission and records request/approval context;
- missing secrets cause startup failure;
- sessions expire and logout/revocation works;
- login throttles repeated failures without creating a trivial account-lockout DoS;
- oversized/slow/malformed JSON, form, and PDF requests are rejected early;
- hostile filenames and audit/rejection strings cannot affect headers or execute script;
- state changes cannot commit without the matching audit record;
- dependency and secret scanning runs in CI, with a manual authorization review gate.

## Method and verification notes

1. Cloned the public repository to a temporary directory and kept the source snapshot unchanged for analysis purposes; no remote/repository changes were made.
2. Read the frozen specification, experiment method, acceptance matrix, all production source, manifests, lockfiles, READMEs, and relevant browser code for the six counted runs.
3. Reviewed authentication, authorization, PII, audit, state transitions, SQL, uploads, filesystem use, HTTP parsing, session lifecycle, browser sinks, runtime binding, and failure atomicity.
4. Re-ran all six shipped suites. Results: Claude 19 Node test items (18 named cases plus helper module), 16, and 14; Copilot 7, 8, and 8. All passed when Node tests were allowed to bind ephemeral localhost sockets.
5. Re-ran current ecosystem audits: all three npm locks clean; Copilot run 1 `pip-audit` findings triaged for applicability.
6. Used the repository's existing Semgrep/gitleaks output and manually verified the reported SQL sites.
7. Performed targeted, non-destructive verification:
   - Copilot run 1 accepted unauthenticated approver creation and allowed a different submitter to read invoice/PII, erase PII, and list all audit data.
   - Claude run 2's published fallback secret generated a token accepted by its verifier.
   - Claude run 1's sub-cent amount triggered the uncaught SQLite exception described in CL1-3.
   - Tested Copilot run 3's unsafe filename/header path on current Python; the patched standard library rejected control characters, so it is reported as a lower-severity robustness issue rather than proven response splitting.
8. No destructive exploit, persistence, external message, or change to the six codebases was performed.

## Final takeaway for the experiment

The security variance is materially larger than the original scanner summary suggests.

All six agents converged on several sound implementation details—password hashing, SQL binding, audit triggers, integer cents, and distinct-approver constraints. They did not converge on a safe trust model. Four independently invented public privilege assignment; five independently omitted object authorization; three independently accepted unbounded document bodies; and all six omitted abuse controls and production session lifecycle.

That is the central security lesson from “one spec, six systems”: a functional requirement such as “users sign in” or “contact data can be erased” does not implicitly specify secure provisioning, authorization, abuse limits, or operational boundaries. If those controls are not explicit in the spec and acceptance gate, a working implementation can pass every functional test while remaining trivially compromisable.

---

Part of the *One spec in. Six systems out.* experiment by [Taiga](https://tai.ga/). Read the essays: [Part 1 — One spec in. Six systems out.](https://tai.ga/en/resources/one-spec-six-systems/) and Part 2 — Six systems passed their tests. None was safe to deploy as shipped. Experiment bundle and method: [`README.md`](./README.md) and [`method/method.md`](./method/method.md). Licensed CC BY 4.0.
