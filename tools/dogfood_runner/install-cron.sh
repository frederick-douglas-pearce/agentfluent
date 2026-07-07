#!/usr/bin/env bash
# Install (or refresh) the local cron entry that runs AgentFluent's dogfood-runner
# daily (S0 / #590). Idempotent — re-running replaces the existing entry in place.
#
# Why local cron and not a cloud/schedule-skill routine: the runner analyzes the
# LOCAL corpus at ~/.claude/projects/, which a cloud routine cannot see, and cron
# runs unattended (no live Claude session required) whenever the machine is on.
# See tools/dogfood_runner/README.md and decisions.md (D050).
#
# Usage:
#   tools/dogfood_runner/install-cron.sh            # install/refresh (default 12:30 daily)
#   DOGFOOD_CRON="0 13 * * *" tools/dogfood_runner/install-cron.sh   # custom schedule
#   tools/dogfood_runner/install-cron.sh --uninstall # remove the entry
set -euo pipefail

MARKER="# agentfluent-dogfood-runner (S0/#590) — managed by install-cron.sh"
# Daily at 12:30 local — a midday time is likely to hit while the machine is on,
# unlike 3am. Cron does not back-fill missed runs; the overlapping rolling window
# self-heals the next day. Override with $DOGFOOD_CRON.
SCHEDULE="${DOGFOOD_CRON:-30 12 * * *}"

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LOG_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/agentfluent/dogfood"

strip_existing() { crontab -l 2>/dev/null | grep -vF "$MARKER" || true; }

if [[ "${1:-}" == "--uninstall" ]]; then
  strip_existing | crontab -
  echo "Removed the dogfood-runner cron entry."
  exit 0
fi

UV_BIN="$(command -v uv || true)"
if [[ -z "$UV_BIN" ]]; then
  echo "error: 'uv' not found on PATH — install uv first (https://docs.astral.sh/uv/)." >&2
  exit 1
fi

mkdir -p "$LOG_DIR"
# Bake absolute paths (cron has a minimal PATH) and run via -m from the repo root
# so the tools.* package imports resolve. Synthesis needs local Claude auth; if it
# is unavailable the gate still runs and the synthesis step is skipped (logged).
CRON_LINE="$SCHEDULE cd $REPO_DIR && $UV_BIN run --group research python -m tools.dogfood_runner.runner >> $LOG_DIR/cron.log 2>&1 $MARKER"

{ strip_existing; echo "$CRON_LINE"; } | crontab -
echo "Installed dogfood-runner cron entry:"
echo "  schedule: $SCHEDULE"
echo "  command : cd $REPO_DIR && uv run --group research python -m tools.dogfood_runner.runner"
echo "  log     : $LOG_DIR/cron.log"
echo "Verify with: crontab -l | grep dogfood-runner"
