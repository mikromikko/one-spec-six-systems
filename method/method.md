# Run it twice — experiment plan

Method plan for the /resources essay "Run it twice" (working title). The essay's claim: give the same coding agent the same specification twice and you get materially different systems. This document is pre-registered method — it is written and frozen **before** the first run, and the essay links to it as a receipt. Changes after the first run are recorded in the deviations log at the end, never edited in place.

Status: **FROZEN 2026-07-11** (see freeze checklist at the end). Post-freeze changes go to the deviations log only.

## Hypothesis

Two runs of the same agent CLI on an identical, frozen specification produce systems that differ **materially** — not at the bit level (expected everywhere, including Taiga's factory), but at the level a CTO or auditor cares about: requirement coverage, data schema, API surface, dependency set, and security findings.

The essay may claim only what the data supports. If the runs come out substantially similar, that is the essay ("the diff was smaller than we expected") or no essay. Honesty-as-strategy applies to our own experiments first.

## Pre-registered materiality criteria

A pair of runs counts as "materially different systems" only if at least **three** of the following hold. Decided now so the essay cannot move the goalposts after seeing the data:

1. **Requirement coverage differs** — at least one MUST requirement from the spec implemented in one run and absent or broken in the other.
2. **Data schema differs** — different entities, or same entity with different fields/types/constraints (beyond naming).
3. **API surface differs** — different endpoints, methods, or a different authentication/authorization model.
4. **Dependency set differs** — different direct dependencies beyond version numbers (different framework, ORM, auth library), or a count difference > 25%.
5. **Security findings differ** — the same scanners produce non-overlapping findings (each run has at least one finding class the other does not).
6. **Stack differs** — different language or framework chosen outright. (If this happens, it is the headline; criteria 2–5 become supporting.)

Explicitly **not** evidence: formatting, comments, identifier naming, file layout, LOC alone. The essay must say so — Taiga's own repeatability claim is scoped to the deliverable set and bar, never bit-identical code, and the experiment holds agents to the same standard.

## Subjects

Two agents — the CLIs installed and licensed on Mikko's machine. All run non-interactively, default settings, no custom system prompts, no MCP servers, no retries (a hard crash is rerun once and logged as a deviation).

| Agent | Invocation | Model (pinned) | Version/model to record |
|---|---|---|---|
| Claude Code | `claude -p "$(cat spec.md)" --model opus --dangerously-skip-permissions` in an empty git repo | Opus 4.8 (vendor flagship) | `claude --version`, resolved model ID from transcript |
| GitHub Copilot CLI | `copilot -p "$(cat spec.md)" --model gpt-5.5 --allow-all-tools` equivalent | GPT-5.5 (vendor flagship) | `copilot --version`, model ID |

Model policy (decided pre-freeze): the model is pinned explicitly per agent — same model for all three of that agent's runs — rather than left on product defaults. Reason: if a default "auto" selector picks different models between runs, model routing variance would be conflated with the variance under study. Flagship-per-vendor keeps the runs representative; the essay still makes no agent-vs-agent claim (each agent is compared only to itself).

Gemini CLI was planned as a third subject and dropped before freeze: login failed and Google directs users to Antigravity (Gemini CLI discontinued June 2026). Recorded here because the essay may use it as dated color.

Run mechanics: runs of the same agent execute **sequentially** (never concurrently — concurrent runs share account rate limits, a throttling confound between runs); the two agents may run in parallel with each other. Exact non-interactive invocations are smoke-tested on a throwaway prompt and locked in the freeze checklist — flags recorded verbatim in every run log.

Rules:

- **Runs per agent: 3.** Headline diff is run 1 vs run 2 ("run it twice"); run 3 is robustness so the essay can say the variance is not a fluke of one pair.
- All runs for one agent execute the same day, same machine, same tool version, to rule out silent product updates. All agents within the same calendar week.
- Each run starts in a fresh empty git repo, fresh session, no shared cache or context. The agent runs to natural completion — no follow-up prompts, no "continue", no fixes. One shot, because the claim is about one instruction in, system out.
- Verbatim identical prompt: the frozen spec file, nothing else. SHA-256 of the spec recorded in every run log.

## The specification

A purpose-written, ~1 page synthetic spec, frozen before any run: **an internal invoice-approval service** for a fictional mid-size EU company. Written to be realistic for Taiga's audience and to carry a designed-in security and governance surface, so that misses are meaningful rather than gotchas:

Draft requirement outline (final wording in `spec.md`, frozen separately):

- Users log in; two roles (submitter, approver). Approvers cannot approve their own invoices.
- Submit invoice with amount, currency, counterparty, due date, and a PDF attachment.
- Approval threshold: invoices over €10,000 need two approvers.
- Every state change recorded in an append-only audit log (who, what, when).
- Counterparty contact data is personal data: deletable on request, and access to it is logged.
- List/filter views for pending approvals; email notification stub on state change.
- Include tests and a README explaining how to run it.

Roughly 10 MUST requirements, each independently checkable. The spec deliberately does **not** name a language, framework, or database — stack choice is the agent's, and stack variance is a legitimate finding (criterion 6).

Also frozen before the runs: `acceptance.md`, a checklist mapping each MUST requirement to a concrete manual check ("create invoice of €12,000, verify a single approval does not release it"). The same person executes it identically against every run.

## Measurements

Collected identically for every run, scripted where possible:

| Dimension | How |
|---|---|
| Requirement coverage | `acceptance.md` executed per run; per-requirement pass/fail/partial matrix. **This is the headline table.** |
| Stack | Language, framework, database, as chosen |
| Data schema | Entities, fields, constraints (from migrations/models) |
| API surface | Endpoints x methods, auth model |
| Dependencies | Direct dependency list from manifest; count + names diff |
| Security | Same scanners on every run: Semgrep (default rulesets), gitleaks; ecosystem audit (`npm audit`/`pip-audit`) where applicable. Findings by class |
| Tests | Present? Count? Do they pass as shipped? |
| Runs at all | Does the README procedure produce a working system, unmodified? |
| Size/shape | File tree, LOC (context only, never headline) |

## Receipts to retain

Everything needed for a third party to check or rerun. Stored in this repository (per-agent self-contained folders: `claude/` and `copilot/`, each holding `spec.md`, `acceptance.md`, `run.sh`, `run1..3/`, `logs/`; the two spec copies must stay hash-identical, verified in every run log). The essay publishes the spec, the acceptance checklist, the coverage matrix, diff summaries, and scanner outputs. Full transcripts kept, published as excerpts.

- `spec.md` + SHA-256, `acceptance.md` (frozen before runs, hash in every log)
- Per run: full session transcript, resulting repo at final commit, tool version output, model ID, timestamps (start/end), machine/OS note
- Scanner raw outputs per run
- This plan, unmodified, plus deviations log

## Fairness rules

The HN reader is the adversarial reviewer. Decided in advance:

- **No cherry-picking**: all runs are published, including boring ones. Run count is fixed at 3 per agent before starting.
- **No tuning**: default settings, no custom instructions. If a defaults choice is contested (e.g. permissions flag), record the exact flags.
- **The spec is public**: anyone can rerun the experiment. The essay invites it.
- **Scope discipline**: the essay claims variance between runs. It does not claim the code is bad, insecure in general, or that any named vendor is worse than another. Per-vendor comparison is explicitly out of scope — results are reported per agent but the thesis is about the category.
- **Disclosure**: the essay states that Taiga's factory also does not produce bit-identical code, and that Taiga's repeatability claim is the artifact set and the published gate. The scoping sentence ships adjacent to the thesis, same rule as the site.
- Vendors are named with versions and dates; "as of" framing throughout, since all these products update monthly.

## Schedule and effort

| Step | Effort |
|---|---|
| Write + freeze `spec.md` and `acceptance.md` | 0.5 day |
| Runs (2 agents x 3 runs, scripted) | 0.5–1 day, mostly wall-clock |
| Acceptance checks + scanners + diff tables | 0.5–1 day |
| Decision point: does the data meet the materiality bar? | — |
| Essay draft (write-taiga-content pipeline) | separate |

API cost is small (agent-CLI sessions); the main cost is the acceptance-check hours.

## Risks

- **Null-ish result**: runs come out similar. Published anyway, as a smaller, honest piece — or shelved with the data kept. Pre-committed above.
- **"Your spec was vague"**: partially true by design (stack unspecified). Mitigated by publishing the spec and inviting reruns; requirement-level MUSTs are not vague.
- **"n=3 is not science"**: correct — the essay claims an observation with published method, not a study. Say so in the piece.
- **Product update mid-window**: all runs per agent same day; versions pinned and recorded.
- **One agent fails to produce anything**: that is a result, reported as such, not retried into success.

## Pre-freeze smoke runs (run 0, 2026-07-11)

Both agents ran the full spec once as a dress rehearsal (`run0/`, excluded from analysis):

- Copilot CLI 1.0.70 / GPT-5.5: 130 s, dependency-free Node.js, file-backed storage, 8/8 tests. Flags `-p --model gpt-5.5 --allow-all-tools` confirmed working. Its instruction channel was verifiably empty (it searched `**/AGENTS.md`, none found; no instruction files in `~/.copilot`).
- Claude Code 2.1.170 / Opus: 534 s, Python + FastAPI + SQLite via uv, 31 tests. **Method finding: the operator's global `~/.claude/CLAUDE.md` and plugin session hook leaked into the run** — the transcript says "I haven't committed anything, per your git workflow rules." This violated the no-custom-instructions rule, so `run.sh` was amended before freeze: Claude runs move `~/.claude/CLAUDE.md` and `~/.claude/settings.json` aside for the run's duration and restore them on exit (logged as `operator_config_neutralized`). Verified by a clean re-smoke before freeze; run-1..3 transcripts are checked for any sign of operator config. The leak itself is publishable color: the environment is part of the system, and the experiment's first catch was its own operator.

## Deviations log

- **2026-07-11 — acceptance checks executed as scripted HTTP checks, not manually.** acceptance.md said "executed by the same person following the run's README only." Actual execution: Claude (the supervising session, not either subject agent) started each system per its README and ran the ten checks as scripted HTTP requests (curl/python), identically shaped per check across all six runs; a browser was used only where a run shipped a UI. What is preserved: one executor for all six, identical check semantics, README-only startup (check 10 unchanged), per-check pass/partial/fail with notes. What is lost: human-in-the-browser observation. Per-run adapter scripts are retained in `analysis/acceptance/` — the fact that each run needs a different adapter (different paths, auth schemes, ports) is itself variance evidence. Mikko reviews the recorded results rather than executing them.

## Freeze checklist

- [x] `spec.md` written, hashed, frozen — SHA-256 `8248b275bf599924db0acc059631c005875c8153cf43b707a4d5b2753149b7af` (identical copies in `claude/` and `copilot/`)
- [x] `acceptance.md` written, frozen
- [x] Agents + versions locked, invocations dress-rehearsed on the full spec (run 0 / 0b):
  - Claude Code 2.1.170, `claude -p "$(cat spec.md)" --model opus --dangerously-skip-permissions --verbose`, operator config neutralized for run duration
  - GitHub Copilot CLI 1.0.70, `copilot -p "$(cat spec.md)" --model gpt-5.5 --allow-all-tools`
- [x] Scanner set installed and versions recorded: Semgrep 1.169.0 (p/default, via uvx), gitleaks 8.30.1 (brew) — run 2026-07-11 over all six runs, outputs in `two-agents-test/analysis/`
- [x] Plan reviewed by Mikko during setup (agents, spec type, no Taiga arm, model pins); freeze date: **2026-07-11**
