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

CONFIDENCE_COLORS: dict[str, str] = {
    "high": "green",
    "medium": "yellow",
    "low": "red",
}


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
