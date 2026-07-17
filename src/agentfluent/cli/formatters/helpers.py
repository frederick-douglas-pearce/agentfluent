"""Shared formatting utilities for CLI output."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, NamedTuple

from rich.console import Console
from rich.markup import escape

from agentfluent.config.models import Severity
from agentfluent.diagnostics.models import Axis

if TYPE_CHECKING:
    from agentfluent.config.models import ConfigScore, EnvironmentWarning
    from agentfluent.core.session import SessionClass

SEVERITY_COLORS: dict[Severity, str] = {
    Severity.CRITICAL: "red",
    Severity.WARNING: "yellow",
    Severity.INFO: "cyan",
}

AXIS_COLORS: dict[Axis, str] = {
    Axis.COST: "yellow",
    Axis.SPEED: "cyan",
    Axis.QUALITY: "magenta",
}

GLOBAL_AGENT_LABEL = "(global)"
"""Display string for cross-cutting findings whose ``agent_type`` is
``None`` (e.g., MCP audit). JSON output keeps ``null``; tables substitute
this label."""

SESSION_KIND_LABELS: dict[SessionClass, str | None] = {
    "sdk": "SDK",
    "cli": "Claude Code",
    "unknown": None,
}
"""The single display vocabulary for ``SessionAnalysis.session_kind`` (#592).

One map because three surfaces render these kinds — the footer composition
line, the ``--session`` badge, and the verbose per-session ``Kind`` column —
and they must not drift apart.

``unknown`` maps to ``None`` rather than a string so "there is no label"
is a *type-level* fact every caller must handle: AC#3 forbids a misleading
"Claude Code" claim for an unclassified session. Callers render the absence
per surface (the badge omits itself; a table cell shows an em-dash)."""


SESSION_KIND_UNCLASSIFIED = "unclassified"
"""Bucket term for ``unknown`` sessions in the footer's *aggregate*
composition line (#592).

Distinct from :data:`SESSION_KIND_LABELS`, which maps ``unknown`` to ``None``:
a per-session *badge* must make no claim, but an aggregate must still account
for every session it counted. "unclassified" states the absence of a
classification without asserting a runtime — so the counts sum to
``session_count`` while honoring AC#3's ban on a misleading "Claude Code"
claim."""

SESSION_KIND_UNKNOWN_CELL = "—"
"""Table-cell rendering for an ``unknown`` session kind (#592), matching the
em-dash convention used elsewhere in the per-session tables."""


def session_kind_label(kind: SessionClass) -> str | None:
    """Display label for a session kind, or ``None`` when unlabelled (#592).

    ``None`` means *make no claim* (the ``unknown`` class) — never
    substitute a default; see :data:`SESSION_KIND_LABELS`.
    """
    return SESSION_KIND_LABELS.get(kind)

CONFIDENCE_COLORS: dict[str, str] = {
    "high": "green",
    "medium": "yellow",
    "low": "red",
}


def severity_cell(severity: Severity) -> str:
    """Rich-markup cell for a ``Severity`` value."""
    color = SEVERITY_COLORS[severity]
    return f"[{color}]{severity.value}[/{color}]"


def render_environment_warnings(
    console: Console, warnings: list[EnvironmentWarning],
) -> None:
    """Print environment warnings as a banner above the normal output.

    No-op when ``warnings`` is empty. Each warning renders on its own
    ``⚠``-prefixed line, colored by severity. The message is escaped so
    embedded paths/backticks can't be misread as Rich markup. Callers
    must not invoke this on the JSON path — banner text on stdout would
    corrupt the envelope (the warnings ride inside it instead).
    """
    for warning in warnings:
        color = SEVERITY_COLORS[warning.severity]
        console.print(f"[{color}]⚠ {escape(warning.message)}[/{color}]")


def axis_label(axis: Axis) -> str:
    """Rich-markup ``[axis]`` prefix for recommendation rows.

    The literal opening ``[`` is escaped via ``\\[`` so Rich emits it
    as plain text instead of consuming ``[<name>`` as an unknown style
    tag (which would silently drop the label from the rendered output).
    """
    color = AXIS_COLORS[axis]
    return f"[{color}]\\[{axis.value}][/{color}]"


def axis_shift_label(baseline: Axis, current: Axis) -> str:
    """Rich-markup ``[old → new]`` indicator for axis-shifted diff rows.

    Shares the ``\\[`` escape rule with :func:`axis_label`; both axes
    keep their respective colors so the shift is visually scannable.
    """
    baseline_color = AXIS_COLORS[baseline]
    current_color = AXIS_COLORS[current]
    return (
        f"\\[[{baseline_color}]{baseline.value}[/{baseline_color}] → "
        f"[{current_color}]{current.value}[/{current_color}]]"
    )


def format_cost(cost: float) -> str:
    """Format a dollar cost for display."""
    if cost < 0.01:
        return f"${cost:.4f}"
    return f"${cost:.2f}"


def format_tokens(tokens: int) -> str:
    """Format token count with comma separator."""
    return f"{tokens:,}"


def format_size(size_bytes: int) -> str:
    """Format bytes as human-readable size."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes / (1024 * 1024):.1f} MB"


def format_date(dt: datetime | None) -> str:
    """Format a datetime for display."""
    if dt is None:
        return "—"
    return dt.strftime("%Y-%m-%d %H:%M")


def score_color(score: int) -> str:
    """Return a Rich color based on score value."""
    if score >= 80:
        return "green"
    if score >= 50:
        return "yellow"
    return "red"


def average_score(scores: list[ConfigScore]) -> int:
    """Integer average of overall_score across agents; 0 for an empty list."""
    return sum(s.overall_score for s in scores) // len(scores) if scores else 0


def truncate(text: str, max_len: int) -> str:
    """Truncate with a trailing ellipsis when the text exceeds max_len."""
    return text if len(text) <= max_len else text[: max_len - 1] + "…"


# Wall/active ratio at or above which an agent type's duration cell is
# flagged as interactive-pattern (#480). The ``pm`` dogfood case sat
# around 6x (2,918s wall vs ~470s active per call); 3x cleanly separates
# genuinely interactive agents from the ms-level jitter between the
# parent-side wall clock and trace-derived active duration.
DURATION_RATIO_HIGHLIGHT = 3.0

# Minimum wall-over-active gap before the cell shows the combined
# "active (wall)" form rather than a bare active figure. Below this the
# two are effectively equal (the ~ms disagreement noted in table.py's
# verbose path) and a second number would be noise.
_DURATION_DIVERGENCE = 1.05


class DurationCell(NamedTuple):
    """Rendered agent-type duration cell plus the flags a caller needs to
    style it and decide which footnotes to print (#480).

    ``text`` is plain (no Rich markup) so both the analyze table and the
    Markdown report share it. ``highlight`` is set when the wall/active
    ratio crosses :data:`DURATION_RATIO_HIGHLIGHT`. ``unreliable`` marks
    the wall-only fallback (no invocation of this type had a linked
    trace). ``partial`` marks partial trace coverage (the active/wall
    averages reflect only the trace-linked subset)."""

    text: str
    highlight: bool
    unreliable: bool
    partial: bool


def format_agent_duration_cell(
    *,
    total_duration_ms: int,
    invocation_count: int,
    total_active_duration_ms: int,
    total_wallclock_ms_trace_linked: int,
    active_duration_invocation_count: int,
) -> DurationCell:
    """Format an agent type's summary-table duration as active (wall) per call.

    Renders the idle-subtracted active duration next to raw wall-clock so
    an interactive agent (whose wall-clock includes user-wait time) does
    not read as a duration problem -- the misread #480 exists to fix.

    Both per-call averages are taken over the *same* trace-linked subset
    (denominator ``active_duration_invocation_count``) so the wall/active
    ratio measures only idle subtraction, not trace-coverage skew. When no
    invocation of this type had a trace, falls back to a bare wall-only
    average over all invocations, marked unreliable.
    """
    if total_duration_ms <= 0 or invocation_count <= 0:
        return DurationCell("-", highlight=False, unreliable=False, partial=False)

    if active_duration_invocation_count <= 0:
        # No trace anywhere in this agent type: only parent-side
        # wall-clock is available, and it silently includes user-wait.
        avg_wall = total_duration_ms / invocation_count
        return DurationCell(
            f"~{avg_wall / 1000:.1f}s*",
            highlight=False,
            unreliable=True,
            partial=False,
        )

    active_avg = total_active_duration_ms / active_duration_invocation_count
    wall_avg = total_wallclock_ms_trace_linked / active_duration_invocation_count
    partial = active_duration_invocation_count < invocation_count

    if active_avg > 0 and wall_avg > active_avg * _DURATION_DIVERGENCE:
        text = f"{active_avg / 1000:.1f}s ({wall_avg / 1000:.1f}s wall)"
        ratio = wall_avg / active_avg
    else:
        text = f"{active_avg / 1000:.1f}s"
        ratio = 1.0
    if partial:
        text += "†"

    return DurationCell(
        text,
        highlight=ratio >= DURATION_RATIO_HIGHLIGHT,
        unreliable=False,
        partial=partial,
    )
