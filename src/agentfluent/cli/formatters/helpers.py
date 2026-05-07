"""Shared formatting utilities for CLI output."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from agentfluent.config.models import Severity

if TYPE_CHECKING:
    from agentfluent.config.models import ConfigScore

SEVERITY_COLORS: dict[Severity, str] = {
    Severity.CRITICAL: "red",
    Severity.WARNING: "yellow",
    Severity.INFO: "cyan",
}

AXIS_COLORS: dict[str, str] = {
    "cost": "yellow",
    "speed": "cyan",
    "quality": "magenta",
}
"""Color map for axis attribution labels (#273). Keys mirror the bare
strings used by ``AggregatedRecommendation.primary_axis`` and
``axis_scores``. Unknown axes fall back to ``white`` via
:func:`axis_label`."""

GLOBAL_AGENT_LABEL = "(global)"
"""Display string for cross-cutting findings whose ``agent_type`` is
``None`` (e.g., MCP audit). JSON output keeps ``null``; tables substitute
this label."""

CONFIDENCE_COLORS: dict[str, str] = {
    "high": "green",
    "medium": "yellow",
    "low": "red",
}


def severity_cell(severity: Severity) -> str:
    """Rich-markup cell for a ``Severity`` value."""
    color = SEVERITY_COLORS[severity]
    return f"[{color}]{severity.value}[/{color}]"


def axis_label(axis: str) -> str:
    """Rich-markup ``[axis]`` prefix for recommendation rows (#273).

    Centralizes the markup so CLI table, top-N summary, and diff output
    stay visually consistent. Unknown axes render in white so a future
    axis added without updating ``AXIS_COLORS`` still appears (just
    uncolored) instead of crashing.

    The literal opening ``[`` is escaped via ``\\[`` so Rich emits it
    as plain text instead of consuming ``[axis]`` as an unknown style
    tag (which would silently drop the label entirely from the rendered
    output). The closing ``]`` is fine as-is — Rich only treats it
    specially when it closes an open tag.
    """
    color = AXIS_COLORS.get(axis, "white")
    return f"[{color}]\\[{axis}][/{color}]"


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
