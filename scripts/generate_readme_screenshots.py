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

No screenshot may carry a real user path: the diff snapshots are home-scrubbed
(``_scrub_home``) before rendering, and ``save()`` refuses to write any
screenshot whose rendered text still contains the home dir or a home-root
prefix (``/home/``, ``/Users/``, ``-home-``, ``-Users-``).
"""

from __future__ import annotations

import io
import re
import sys
from pathlib import Path
from typing import Any

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
    # keeps the internal buffer that ``save`` checks and exports.
    return Console(
        record=True, width=WIDTH, force_terminal=True, file=io.StringIO(),
    )


# Any home-root prefix followed by a name, in path or Claude Code project-slug
# form. Deliberately broader than the current user's home so a truncated
# (``/home/fdpe…``) or wrapped path still trips the guard; a refusal on some
# other ``/home/...`` path is an accepted false positive (fail loud).
_HOME_ROOT_PATTERN = re.compile(r"(/home/|/Users/|-home-|-Users-)\w")


def _home_dir() -> str:
    """The current home dir without a trailing slash; ``""`` when it is the
    filesystem root (or empty), where there is no prefix worth scrubbing."""
    return str(Path.home()).rstrip("/")


def _scrub_home(obj: Any) -> Any:
    """Return a copy of a JSON-shaped ``obj`` with the home-dir prefix
    replaced by ``~`` in every string value (dict keys are left as-is; none
    carries a path today). The match ends at a path boundary, so home
    ``/home/al`` leaves ``/home/alice`` alone. Recommendation messages cite
    agent definition paths (``Agent 'x' is defined in /home/...``, see #753);
    scrubbing the data before rendering keeps the real path out of the
    screenshot and lets Rich compute column truncation on the scrubbed text."""
    if isinstance(obj, str):
        home = _home_dir()
        if not home:
            return obj
        return re.sub(re.escape(home) + r"(?=/|$|[^\w.-])", "~", obj)
    if isinstance(obj, dict):
        return {key: _scrub_home(value) for key, value in obj.items()}
    if isinstance(obj, list):
        return [_scrub_home(item) for item in obj]
    return obj


def _leaks(text: str) -> list[str]:
    """What in ``text`` would leak a real user path: the exact home dir, or
    any home-root prefix (``_HOME_ROOT_PATTERN``)."""
    found = []
    home = _home_dir()
    if home and home in text:
        found.append(home)
    found.extend(match.group(0) for match in _HOME_ROOT_PATTERN.finditer(text))
    return found


def save(console: Console, filename: str, title: str) -> None:
    """Write the console's recording to ``images/<filename>`` — but only after
    checking its rendered plain text for a home-dir path. The check runs on the
    text a reader sees (no SVG escaping, no style segments) and before any
    write, so a refused screenshot leaves the committed file untouched."""
    out = IMAGES_DIR / filename
    leaked = _leaks(console.export_text(clear=False))
    if leaked:
        raise SystemExit(
            f"refusing to write {out.relative_to(REPO_ROOT)}: rendered text "
            f"contains a home-dir path ({', '.join(sorted(set(leaked)))}); "
            "scrub it before rendering",
        )
    out.write_text(console.export_svg(title=title), encoding="utf-8")
    print(f"wrote {out.relative_to(REPO_ROOT)}")


def _trimmed_diagnostics(diag: DiagnosticsResult) -> DiagnosticsResult:
    """Return a copy of ``diag`` with signal / recommendation lists
    truncated to keep the diagnostics screenshot within a reasonable
    vertical footprint.

    Signals are deduplicated by ``signal_type`` first (one row per
    distinct type, in extraction order), then any remaining slots are
    filled with additional rows of already-seen types. This guarantees
    the screenshot showcases the breadth of signals AgentFluent
    detects (cost + speed + quality axes) instead of N rows of whichever
    type happened to lead the extraction order on this dataset.
    """
    seen_types: set[str] = set()
    primary: list = []
    extras: list = []
    for sig in diag.signals:
        if sig.signal_type not in seen_types:
            seen_types.add(sig.signal_type)
            primary.append(sig)
        else:
            extras.append(sig)
    trimmed_signals = (primary + extras)[:MAX_SIGNALS]

    return diag.model_copy(
        update={
            "signals": trimmed_signals,
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
    save(
        console,
        "demo-analyze.svg",
        "agentfluent analyze --project agentfluent --no-diagnostics",
    )


def generate_diagnostics(diag: DiagnosticsResult) -> None:
    console = make_console()
    _format_diagnostics_table(console, _trimmed_diagnostics(diag), verbose=False)
    save(
        console,
        "demo-diagnostics.svg",
        "agentfluent analyze --project agentfluent",
    )


def generate_subagents(diag: DiagnosticsResult) -> None:
    console = make_console()
    _format_delegation_suggestions(
        console, _subagents_only_diagnostics(diag), verbose=True,
    )
    save(
        console,
        "demo-subagents.svg",
        "agentfluent analyze --project agentfluent --verbose",
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
    baseline = _scrub_home(
        _analyze_with_diagnostics(paths[:half]).model_dump(mode="json"),
    )
    current = _scrub_home(_analyze_with_diagnostics(paths).model_dump(mode="json"))
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
