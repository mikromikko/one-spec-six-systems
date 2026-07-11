# One spec in. Six systems out.

Artifact bundle for the experiment described in the essay: https://tai.ga/en/resources/one-spec-six-systems

On 11 July 2026 we gave two coding agents the same frozen one-page specification, three times each, one message per run, in an empty directory, with no follow-ups. Every resulting system worked. No two were alike in architecture, authentication, or dependencies. This repository contains everything needed to check that claim or rerun the experiment.

## Contents

| Path | What it is |
|---|---|
| `spec.md` | The frozen specification. SHA-256 `8248b275bf599924db0acc059631c005875c8153cf43b707a4d5b2753149b7af` (recorded in every run log) |
| `acceptance.md` | The ten-point acceptance checklist, one check per requirement |
| `run.sh` | The runner: fresh directory per run, versions and timestamps logged, operator config neutralized for Claude runs |
| `method/method.md` | The full method, frozen before the first counted run, including the pre-set "materially different" bar and the deviations log |
| `runs/claude/run1..3` | Claude Code 2.1.170 (Opus 4.8) output, tree at each run's final commit |
| `runs/copilot/run1..3` | GitHub Copilot CLI 1.0.70 (GPT-5.5) output, same |
| `runs/*/run0*` | Smoke runs (dress rehearsals), excluded from analysis. `claude/run0` is the contaminated run described in the essay: the agent read the operator's personal config ("per your git workflow rules" appears in `logs/claude-run0.log`) |
| `logs/` | Per-run logs: versions, model flags, spec hash, timestamps, durations, exit codes |
| `analysis/` | The comparison matrix, acceptance results per system, and scanner outputs (Semgrep 1.169.0 default rules, gitleaks 8.30.1) |

## Rerunning it

You need the two CLIs installed and authenticated. Copy `spec.md` and `run.sh` into a folder named `claude` or `copilot` (the runner infers the agent from the folder name), then:

```sh
./run.sh 1
./run.sh 2   # after run 1 finishes; same-agent runs must be sequential
./run.sh 3
```

Note: for Claude runs the script temporarily moves `~/.claude/CLAUDE.md` and `~/.claude/settings.json` aside and restores them on exit, because the smoke run showed operator config leaking into the output. Read `run.sh` before running it.

Both products update continuously; your versions will not be our versions, and your results will be your own draw. That is rather the point. If your diff comes back boring, publish it — we would like to see that result.

## Notes

- The runs used each product's non-interactive mode with default settings and a pinned model flag. Neither product exposes sampling controls in this mode.
- `runs/claude/run2` contains a hardcoded fallback JWT secret (`dev-insecure-secret`). It is left in place deliberately: it is the experiment's one genuine security finding. Do not deploy any of these systems.
- Logs are published as recorded, minus Copilot session-resume identifiers.
- Generated code in `runs/` is agent output, provided for verification only, no license granted or implied. Everything else in this repo: CC BY 4.0.
