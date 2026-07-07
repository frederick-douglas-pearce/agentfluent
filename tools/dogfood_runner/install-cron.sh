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
strip_existing() { crontab -l 2>/dev/null | grep -vF "$MARKER" || true; }

# --uninstall needs neither the schedule nor the window, so handle it BEFORE any
# DOGFOOD_CRON / DOGFOOD_WINDOW validation — otherwise a stale/invalid env var in
# the caller's shell (e.g. `export DOGFOOD_WINDOW=1w`) would make removal exit 1.
if [[ "${1:-}" == "--uninstall" ]]; then
  strip_existing | crontab -
  echo "Removed the dogfood-runner cron entry."
  exit 0
fi

# Daily at 12:30 local — a midday time is likely to hit while the machine is on,
# unlike 3am. Cron does not back-fill missed runs; the overlapping rolling window
# self-heals the next day. Override with $DOGFOOD_CRON.
SCHEDULE="${DOGFOOD_CRON:-30 12 * * *}"
# Rolling analysis window passed to `analyze --since`. Default 7d — robust to a
# sporadically-worked corpus and to missed cron days (see DEFAULT_WINDOW in
# cli_runner.py). Override with $DOGFOOD_WINDOW. Restricted to a relative
# Nd/Nh/Nm form since it is spliced into the crontab command.
WINDOW="${DOGFOOD_WINDOW:-7d}"
if ! [[ "$WINDOW" =~ ^[0-9]+[dhm]$ ]]; then
  echo "error: DOGFOOD_WINDOW must be a relative window like 7d, 48h, or 90m (got: '$WINDOW')." >&2
  exit 1
fi
# Validate the schedule before it is spliced into the crontab line. Cron parses the
# first five whitespace-separated tokens as the schedule and treats the REST as the
# command, so an unsanitized $DOGFOOD_CRON like "* * * * * rm -rf ~ #" would inject
# arbitrary commands. Constrain to exactly five standard cron fields (digits and
# * , - / only); anything fancier, edit the crontab directly.
_CRON_FIELD='[0-9*,/-]+'
if ! [[ "$SCHEDULE" =~ ^${_CRON_FIELD}([[:space:]]+${_CRON_FIELD}){4}$ ]]; then
  echo "error: DOGFOOD_CRON must be a 5-field cron schedule using only [0-9 * , - /]" >&2
  echo "       (got: '$SCHEDULE')." >&2
  exit 1
fi

# Resolve the script's real location (follow a symlink if it was installed as one)
# before deriving the repo root, so `cd $REPO_DIR` in the baked entry is always the
# real checkout — not the dir the symlink happens to sit in.
SOURCE="${BASH_SOURCE[0]}"
while [[ -L "$SOURCE" ]]; do
  DIR="$(cd -P "$(dirname "$SOURCE")" && pwd)"
  SOURCE="$(readlink "$SOURCE")"
  [[ "$SOURCE" != /* ]] && SOURCE="$DIR/$SOURCE"
done
REPO_DIR="$(cd -P "$(dirname "$SOURCE")/../.." && pwd)"

# The runner reads $XDG_STATE_HOME at runtime to place snapshots. Cron has a minimal
# env (no XDG_STATE_HOME), so unless we bake it, cron would write snapshots under the
# ~/.local/state fallback while an interactive shell that exports XDG_STATE_HOME
# writes elsewhere — they'd never share a baseline and the window-over-window diff
# would silently never run under cron. Resolve it ONCE here and bake it into the
# entry so cron and interactive runs agree.
STATE_HOME="${XDG_STATE_HOME:-$HOME/.local/state}"
LOG_DIR="$STATE_HOME/agentfluent/dogfood"

UV_BIN="$(command -v uv || true)"
if [[ -z "$UV_BIN" ]]; then
  echo "error: 'uv' not found on PATH — install uv first (https://docs.astral.sh/uv/)." >&2
  exit 1
fi

mkdir -p "$LOG_DIR"
# Cron runs with a minimal PATH (typically /usr/bin:/bin), but the SDK synthesis
# step spawns the `claude`/node CLI, which usually lives elsewhere (~/.local/bin,
# nvm/fnm, etc.). Baking the FULL interactive $PATH overflows crontab's line-length
# limit ("command too long"), so bake only the dirs that actually matter — where
# uv/node/claude resolve at install time — plus the base dirs. Run via -m from the
# repo root so the tools.* package imports resolve.
_baked_path() {
  local bin resolved dirs=()
  for bin in uv node claude; do
    resolved="$(command -v "$bin" 2>/dev/null)" && dirs+=("$(dirname "$resolved")")
  done
  # Base dirs after the resolved ones: the Claude Code synthesis subprocess may
  # shell out to auxiliary tools (git, ripgrep, …). Include the common user/brew
  # bin dirs so those resolve under cron too; non-existent entries are harmless in
  # PATH. awk dedups (order-preserving) and paste joins with ':'.
  printf '%s\n' "${dirs[@]}" \
    "$HOME/.local/bin" "$HOME/.cargo/bin" /opt/homebrew/bin /usr/local/bin /usr/bin /bin \
    | awk 'NF && !seen[$0]++' | paste -sd:
}
BAKED_PATH="$(_baked_path)"
# Single-quote every interpolated value in the emitted command so that spaces or
# shell metacharacters in a path/env var (REPO_DIR, STATE_HOME, PATH, UV_BIN,
# LOG_DIR) are treated as literals when cron's /bin/sh parses the line, rather than
# breaking the entry or injecting commands. (A literal single quote in one of these
# paths would still break it, but that is pathological for a filesystem path.)
CRON_CMD="cd '$REPO_DIR' && XDG_STATE_HOME='$STATE_HOME' PATH='$BAKED_PATH' '$UV_BIN' run --group research python -m tools.dogfood_runner.runner --window '$WINDOW' >> '$LOG_DIR/cron.log' 2>&1"
CRON_LINE="$SCHEDULE $CRON_CMD $MARKER"

# cron treats an unescaped `%` in the command as a literal newline (it truncates the
# command and feeds the rest as stdin), so escape any `%` that appears in a baked
# path before writing the crontab.
CRON_LINE="${CRON_LINE//%/\\%}"

{ strip_existing; echo "$CRON_LINE"; } | crontab -
echo "Installed dogfood-runner cron entry:"
echo "  schedule: $SCHEDULE"
echo "  window  : $WINDOW"
echo "  command : cd $REPO_DIR && uv run --group research python -m tools.dogfood_runner.runner --window $WINDOW"
echo "  log     : $LOG_DIR/cron.log"
echo "Verify with: crontab -l | grep dogfood-runner"
echo
echo "NOTE: the SDK narrative synthesis needs local Claude auth (ANTHROPIC_API_KEY,"
echo "or Claude Code credentials under ~/.claude) available in the cron environment."
echo "Without it, the deterministic gate still runs and reports; only the synthesis"
echo "step is skipped (logged to cron.log). Add secrets via the crontab or a sourced"
echo "env file if you want nightly synthesis to run."
