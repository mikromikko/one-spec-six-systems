#!/usr/bin/env bash
# Usage: ./run.sh 1|2|3
# One experiment run: fresh isolated dir, logged invocation, committed result.
# Agent is inferred from this folder's name (claude|copilot).
# Method: docs/content/run-it-twice-experiment.md in taiga-website (branch content-additions).
set -euo pipefail

N="${1:?usage: ./run.sh 1|2|3}"
ROOT="$(cd "$(dirname "$0")" && pwd)"
AGENT="$(basename "$ROOT")"
DIR="$ROOT/run$N"
LOG="$ROOT/logs/run$N.log"
SPEC="$ROOT/spec.md"

# Model pins — decided pre-freeze, same model for all three runs of an agent.
CLAUDE_MODEL="opus"      # resolves to latest Opus (4.8); resolved ID recorded from version/transcript
COPILOT_MODEL="gpt-5.5"  # confirmed in smoke run 0 (Copilot CLI 1.0.70)

# Operator-config neutralization (claude only): the smoke run showed the agent
# reading ~/.claude/CLAUDE.md and the plugin session hook ("per your git workflow
# rules"). Vanilla runs require moving these aside; trap restores them even on
# crash/Ctrl-C. Do not start other claude sessions while a run is active.
HOLD=".experiment-hold"
NEUTRALIZED=""
restore_config() {
  local f
  for f in CLAUDE.md settings.json; do
    if [ -f "$HOME/.claude/$f$HOLD" ]; then
      mv "$HOME/.claude/$f$HOLD" "$HOME/.claude/$f"
    fi
  done
}
neutralize_config() {
  local f
  for f in CLAUDE.md settings.json; do
    if [ -f "$HOME/.claude/$f$HOLD" ] && [ -f "$HOME/.claude/$f" ]; then
      echo "both $f and $f$HOLD exist in ~/.claude — resolve manually first" >&2
      exit 1
    fi
    if [ -f "$HOME/.claude/$f" ]; then
      mv "$HOME/.claude/$f" "$HOME/.claude/$f$HOLD"
      NEUTRALIZED="$NEUTRALIZED $f"
    fi
  done
  trap restore_config EXIT
}

[ -f "$SPEC" ] || { echo "spec.md missing in $ROOT" >&2; exit 1; }
if [ -d "$DIR" ] && [ -n "$(ls -A "$DIR" 2>/dev/null)" ]; then
  echo "refusing: $DIR already exists and is not empty (no reruns; see plan)" >&2
  exit 1
fi

mkdir -p "$DIR" "$ROOT/logs"
cd "$DIR"
git init -q

SPEC_HASH=$(shasum -a 256 "$SPEC" | cut -d' ' -f1)
{
  echo "agent: $AGENT"
  echo "run: $N"
  echo "start_utc: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "spec_sha256: $SPEC_HASH"
  echo "machine: $(uname -srm)"
  case "$AGENT" in
    claude)  echo "version: $(claude --version 2>&1)"; echo "model_flag: --model $CLAUDE_MODEL" ;;
    copilot) echo "version: $(copilot --version 2>&1)"; echo "model_flag: --model $COPILOT_MODEL" ;;
    *) echo "unknown agent folder: $AGENT" >&2; exit 1 ;;
  esac
} | tee "$LOG"

if [ "$AGENT" = "claude" ]; then
  neutralize_config
  echo "operator_config_neutralized:${NEUTRALIZED:- none}" | tee -a "$LOG"
fi
echo "---- transcript ----" | tee -a "$LOG"

START=$(date +%s)
set +e
case "$AGENT" in
  claude)
    claude -p "$(cat "$SPEC")" --model "$CLAUDE_MODEL" --dangerously-skip-permissions --verbose 2>&1 | tee -a "$LOG"
    ;;
  copilot)
    copilot -p "$(cat "$SPEC")" --model "$COPILOT_MODEL" --allow-all-tools 2>&1 | tee -a "$LOG"
    ;;
esac
EXIT_CODE=$?
set -e
END=$(date +%s)

if [ "$AGENT" = "claude" ]; then
  restore_config
  trap - EXIT
  echo "operator_config_restored: yes" | tee -a "$LOG"
fi

{
  echo "---- end transcript ----"
  echo "exit_code: $EXIT_CODE"
  echo "end_utc: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "duration_seconds: $((END - START))"
} | tee -a "$LOG"

git add -A
git commit -qm "$AGENT run$N output" --allow-empty
echo "final_commit: $(git rev-parse HEAD)" | tee -a "$LOG"
echo "done: $DIR (log: $LOG)"
