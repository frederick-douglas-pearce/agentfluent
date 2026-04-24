"""Generate SVG screenshots for the README from the real agentfluent project.

Produces four reproducible SVGs under ``images/``:

- ``demo-analyze.svg`` — token/cost/tool/agent tables (no --diagnostics)
- ``demo-diagnostics.svg`` — trimmed Diagnostic Signals + aggregated Recommendations
- ``demo-subagents.svg`` — Suggested Subagents table + one YAML draft block
- ``demo-config-check.svg`` — config-check scoring + recommendations

Regenerate after feature changes:

    uv run python scripts/generate_readme_screenshots.py

The script uses the CLI's own formatter functions against the current machine's
``~/.claude/projects/agentfluent`` session data, so numbers reflect live data.
Commit the regenerated SVGs alongside README / feature changes.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

from rich.console import Console

from agentfluent.analytics.pipeline import analyze_sessions
from agentfluent.cli.formatters.table import (
    _format_delegation_suggestions,
    _format_diagnostics_table,
    format_analysis_table,
    format_config_check_table,
)
from agentfluent.config import assess_agents
from agentfluent.core.discovery import find_project
from agentfluent.diagnostics.models import DiagnosticsResult
from agentfluent.diagnostics.pipeline import run_diagnostics

REPO_ROOT = Path(__file__).resolve().parent.parent
IMAGES_DIR = REPO_ROOT / "images"
WIDTH = 120
PROJECT_NAME = "agentfluent"

# Cropping knobs — signals/recs are truncated to keep screenshots within a
# reasonable vertical so the Count column and built-in-agent text stay visible
# without excessive scrolling. Raise if you want more content per shot.
MAX_SIGNALS = 6
MAX_AGGREGATED_RECS = 8


def make_console() -> Console:
    # ``file=StringIO()`` silences the live terminal write; ``record=True``
    # keeps the internal buffer that ``save_svg`` consumes.
    return Console(
        record=True, width=WIDTH, force_terminal=True, file=io.StringIO(),
    )


def save(console: Console, filename: str, title: str) -> None:
    out = IMAGES_DIR / filename
    console.save_svg(str(out), title=title)
    print(f"wrote {out.relative_to(REPO_ROOT)}")


def _trimmed_diagnostics(diag: DiagnosticsResult) -> DiagnosticsResult:
    """Return a copy of ``diag`` with signal / recommendation lists
    truncated to keep the diagnostics screenshot within a reasonable
    vertical footprint. Ordering is preserved (severity desc, count desc)
    so the most actionable rows stay visible."""
    return diag.model_copy(
        update={
            "signals": diag.signals[:MAX_SIGNALS],
            "recommendations": diag.recommendations[:MAX_AGGREGATED_RECS],
            "aggregated_recommendations": diag.aggregated_recommendations[
                :MAX_AGGREGATED_RECS
            ],
            # Suggested Subagents lives in its own SVG; strip it here.
            "delegation_suggestions": [],
        },
    )


def _subagents_only_diagnostics(diag: DiagnosticsResult) -> DiagnosticsResult:
    """Return a copy of ``diag`` trimmed to one MEDIUM-confidence suggestion
    (falling back to the first of any tier), so the subagents SVG shows a
    single clean table row + one YAML draft block — the headline "surface
    an actionable new agent" story — rather than a wall of low-confidence
    drafts. Signals / recommendations are stripped to isolate the
    Suggested Subagents section."""
    preferred = next(
        (s for s in diag.delegation_suggestions if s.confidence == "medium"),
        diag.delegation_suggestions[0] if diag.delegation_suggestions else None,
    )
    return diag.model_copy(
        update={
            "signals": [],
            "recommendations": [],
            "aggregated_recommendations": [],
            "delegation_suggestions": [preferred] if preferred else [],
        },
    )


def generate_analyze(result) -> None:
    console = make_console()
    format_analysis_table(
        console, result, verbose=False, show_diagnostics=False,
    )
    save(console, "demo-analyze.svg", "agentfluent analyze --project agentfluent")


def generate_diagnostics(diag: DiagnosticsResult) -> None:
    console = make_console()
    _format_diagnostics_table(console, _trimmed_diagnostics(diag), verbose=False)
    save(
        console,
        "demo-diagnostics.svg",
        "agentfluent analyze --project agentfluent --diagnostics",
    )


def generate_subagents(diag: DiagnosticsResult) -> None:
    console = make_console()
    _format_delegation_suggestions(
        console, _subagents_only_diagnostics(diag), verbose=True,
    )
    save(
        console,
        "demo-subagents.svg",
        "agentfluent analyze --project agentfluent --diagnostics --verbose",
    )


def generate_config_check() -> None:
    scores = assess_agents("all", agent_filter=None, user_path=None)
    if not scores:
        print("no agent configs found; skipping config-check screenshot")
        return
    console = make_console()
    format_config_check_table(console, scores, verbose=False)
    save(console, "demo-config-check.svg", "agentfluent config-check")


def main() -> int:
    IMAGES_DIR.mkdir(exist_ok=True)

    project = find_project(PROJECT_NAME)
    if project is None:
        print(f"project not found: {PROJECT_NAME}", file=sys.stderr)
        return 1

    paths = [s.path for s in project.sessions]
    if not paths:
        print(f"no sessions for project: {PROJECT_NAME}", file=sys.stderr)
        return 1

    result = analyze_sessions(paths, agent_filter=None)
    all_invocations = [inv for s in result.sessions for inv in s.invocations]
    all_mcp_calls = [c for s in result.sessions for c in s.mcp_tool_calls]

    if all_invocations:
        result.diagnostics = run_diagnostics(
            all_invocations,
            mcp_tool_calls=all_mcp_calls,
        )

    generate_analyze(result)
    if result.diagnostics is not None:
        generate_diagnostics(result.diagnostics)
        if result.diagnostics.delegation_suggestions:
            generate_subagents(result.diagnostics)
    generate_config_check()

    return 0


if __name__ == "__main__":
    sys.exit(main())
