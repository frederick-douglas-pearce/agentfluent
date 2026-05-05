"""Generate SVG screenshots for the README from the real agentfluent project.

Produces five reproducible SVGs under ``images/``:

- ``demo-analyze.svg`` — token/cost/tool/agent tables (no --diagnostics)
- ``demo-diagnostics.svg`` — trimmed Diagnostic Signals + Top-N priority fixes
  + aggregated Recommendations + Offload Candidates
- ``demo-subagents.svg`` — Suggested Subagents table + one YAML draft block
- ``demo-config-check.svg`` — config-check scoring + recommendations
- ``demo-diff.svg`` — agentfluent diff: new/resolved/persisting + deltas

Regenerate after feature changes:

    uv run python scripts/generate_readme_screenshots.py

The script uses the CLI's own formatter functions against the current machine's
``~/.claude/projects/agentfluent`` session data, so numbers reflect live data.
The diff screenshot is built by analyzing two slices of the same project (an
earlier half vs. all sessions) so it surfaces realistic new/resolved deltas.
Commit the regenerated SVGs alongside README / feature changes.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

from rich.console import Console

from agentfluent.analytics.pipeline import AnalysisResult, analyze_sessions
from agentfluent.cli.formatters.diff_table import format_diff_table
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
from agentfluent.diff import compute_diff

REPO_ROOT = Path(__file__).resolve().parent.parent
IMAGES_DIR = REPO_ROOT / "images"
# Terminal width matters for how readable the SVG is when GitHub renders
# it in a README column. 100 cols puts the resulting viewBox around 1235px;
# when GitHub scales that to the ~720px main-column width, the 20px Rich
# font lands around 11–12px rendered — legible without horizontal scroll.
WIDTH = 100
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


def generate_analyze(result: AnalysisResult) -> None:
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


def _analyze_with_diagnostics(paths: list[Path]) -> AnalysisResult:
    result = analyze_sessions(paths, agent_filter=None)
    invocations = [inv for s in result.sessions for inv in s.invocations]
    mcp_calls = [c for s in result.sessions for c in s.mcp_tool_calls]
    messages = [m for s in result.sessions for m in s.messages]
    if invocations:
        result.diagnostics = run_diagnostics(
            invocations,
            mcp_tool_calls=mcp_calls,
            parent_messages=messages,
        )
    return result


def generate_diff(paths: list[Path]) -> None:
    """Render an ``agentfluent diff`` screenshot from two real snapshots
    of the same project, so the demo uses real new/resolved deltas
    without a hand-curated fixture.
    """
    if len(paths) < 2:
        print("not enough sessions for diff screenshot; skipping")
        return
    half = len(paths) // 2
    baseline = _analyze_with_diagnostics(paths[:half]).model_dump(mode="json")
    current = _analyze_with_diagnostics(paths).model_dump(mode="json")
    diff_result = compute_diff(baseline, current)
    console = make_console()
    format_diff_table(console, diff_result, top_n=5, verbose=False)
    save(console, "demo-diff.svg", "agentfluent diff baseline.json current.json")


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

    result = _analyze_with_diagnostics(paths)

    generate_analyze(result)
    if result.diagnostics is not None:
        generate_diagnostics(result.diagnostics)
        if result.diagnostics.delegation_suggestions:
            generate_subagents(result.diagnostics)
    generate_config_check()
    generate_diff(paths)

    return 0


if __name__ == "__main__":
    sys.exit(main())
