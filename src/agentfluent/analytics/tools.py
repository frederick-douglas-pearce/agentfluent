"""Tool pattern analytics for session analysis.

Counts tool call frequency, computes unique tool count, and measures
tool concentration across a session.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agentfluent.core.session import SessionMessage


@dataclass
class ToolMetrics:
    """Tool usage metrics for a session."""

    tool_frequency: dict[str, int] = field(default_factory=dict)
    """Tool call counts sorted by frequency (descending)."""

    unique_tool_count: int = 0
    """Number of distinct tools used."""

    total_tool_calls: int = 0
    """Total number of tool calls across all tools."""

    concentration: list[ConcentrationEntry] = field(default_factory=list)
    """Cumulative concentration: each entry shows how many top tools
    account for what percentage of total calls."""


@dataclass
class ConcentrationEntry:
    """A single point in the tool concentration curve."""

    top_n: int
    """Number of top tools."""

    call_count: int
    """Total calls from those top_n tools."""

    percentage: float
    """Percentage of total calls (0-100)."""


def compute_tool_metrics(messages: list[SessionMessage]) -> ToolMetrics:
    """Compute tool usage metrics from parsed session messages.

    Scans assistant messages for tool_use content blocks and aggregates
    call counts by tool name.

    Args:
        messages: Parsed (and deduplicated) session messages.

    Returns:
        ToolMetrics with frequency, unique count, and concentration data.
    """
    counts: dict[str, int] = {}

    for msg in messages:
        if msg.type != "assistant":
            continue
        for block in msg.tool_use_blocks:
            counts[block.name] = counts.get(block.name, 0) + 1

    if not counts:
        return ToolMetrics()

    # Sort by frequency descending, then alphabetically for ties
    sorted_tools = sorted(counts.items(), key=lambda x: (-x[1], x[0]))
    sorted_freq = dict(sorted_tools)

    total = sum(counts.values())

    # Build concentration curve
    concentration: list[ConcentrationEntry] = []
    cumulative = 0
    for i, (_name, count) in enumerate(sorted_tools, start=1):
        cumulative += count
        concentration.append(
            ConcentrationEntry(
                top_n=i,
                call_count=cumulative,
                percentage=round(cumulative / total * 100, 1),
            )
        )

    return ToolMetrics(
        tool_frequency=sorted_freq,
        unique_tool_count=len(counts),
        total_tool_calls=total,
        concentration=concentration,
    )
