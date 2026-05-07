"""Shared helpers for tests that exercise the aggregated Recommendations
table and Top-N priority summary."""

from __future__ import annotations

from rich.console import Console

from agentfluent.cli.formatters.table import (
    _format_diagnostics_table,
    _format_top_recommendations,
)
from agentfluent.config.models import Severity
from agentfluent.diagnostics.models import (
    AggregatedRecommendation,
    DiagnosticsResult,
    SignalType,
)


def make_agg(
    *,
    agent_type: str = "pm",
    severity: Severity = Severity.WARNING,
    target: str = "prompt",
    count: int = 1,
    representative_message: str = "Add fallback guidance.",
    priority_score: float = 200.0,
    primary_axis: str = "cost",
    axis_scores: dict[str, float] | None = None,
    signal_types: list[SignalType] | None = None,
) -> AggregatedRecommendation:
    return AggregatedRecommendation(
        agent_type=agent_type,
        target=target,
        severity=severity,
        signal_types=signal_types or [SignalType.RETRY_LOOP],
        count=count,
        representative_message=representative_message,
        priority_score=priority_score,
        primary_axis=primary_axis,
        axis_scores=axis_scores or {"cost": 0.0, "speed": 0.0, "quality": 0.0},
    )


def render_section(
    diag: DiagnosticsResult,
    *,
    top_n: int = 5,
    verbose: bool = False,
    width: int = 160,
) -> str:
    console = Console(record=True, width=width, force_terminal=False)
    _format_diagnostics_table(console, diag, verbose=verbose, top_n=top_n)
    return console.export_text()


def render_top_only(diag: DiagnosticsResult, *, top_n: int, width: int = 120) -> str:
    console = Console(record=True, width=width, force_terminal=False)
    _format_top_recommendations(console, diag, top_n=top_n)
    return console.export_text()
