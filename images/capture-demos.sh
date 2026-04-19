#!/usr/bin/env bash
# Helper for capturing README demo screenshots.
#
# Run each section in your terminal (dark theme, ~100 cols wide, clean prompt),
# then take a screenshot of the terminal window and save as the indicated filename.
#
# Prerequisites:
#   - agentfluent installed on PATH (uv tool install agentfluent)
#   - Terminal window wide enough that no tables wrap (aim for 100+ columns)
#
# Usage:
#   ./images/capture-demos.sh          # runs all four in sequence with pauses
#   ./images/capture-demos.sh list     # run a single demo by name
#
# Demo names: list, analyze, analyze-verbose, config-check

set -euo pipefail

DEMO_PROJECT="${DEMO_PROJECT:-agentfluent}"

pause() {
  echo
  read -rp "→ Screenshot saved as '$1'? Press Enter to continue... "
  clear
}

run_list() {
  clear
  agentfluent list
  pause "images/demo-list.png"
}

run_analyze() {
  clear
  agentfluent analyze --project "$DEMO_PROJECT"
  pause "images/demo-analyze.png"
}

run_analyze_verbose() {
  clear
  agentfluent analyze --project "$DEMO_PROJECT" --verbose
  pause "images/demo-analyze-verbose.png"
}

run_config_check() {
  clear
  agentfluent config-check --scope user
  pause "images/demo-config-check.png"
}

case "${1:-all}" in
  list)             run_list ;;
  analyze)          run_analyze ;;
  analyze-verbose)  run_analyze_verbose ;;
  config-check)     run_config_check ;;
  all)
    run_list
    run_analyze
    run_analyze_verbose
    run_config_check
    ;;
  *)
    echo "Unknown demo: $1" >&2
    echo "Valid: list, analyze, analyze-verbose, config-check, all" >&2
    exit 1
    ;;
esac

echo "All requested captures complete."
