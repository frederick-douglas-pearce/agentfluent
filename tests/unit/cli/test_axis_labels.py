"""Axis attribution rendering on recommendations (#273).

Locks the [axis] prefix on the aggregated Recommendations table, the
top-N priority summary, and the verbose-mode per-row breakdown line.
Also confirms the architect-mandated verbose change: the aggregated
table renders in verbose mode (the legacy raw unaggregated table no
longer fires), and the per-row priority breakdown line appears below it.
"""

from __future__ import annotations

from rich.console import Console

from agentfluent.cli.formatters.table import _format_diagnostics_table
from agentfluent.config.models import Severity
from agentfluent.diagnostics.models import (
    AggregatedRecommendation,
    DiagnosticRecommendation,
    DiagnosticsResult,
    SignalType,
)


def _agg(
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


def _render(
    diag: DiagnosticsResult,
    *,
    top_n: int = 5,
    verbose: bool = False,
) -> str:
    console = Console(record=True, width=160, force_terminal=False)
    _format_diagnostics_table(console, diag, verbose=verbose, top_n=top_n)
    return console.export_text()


class TestAxisPrefixOnTopN:
    """The Top-N priority fixes block prepends the colorized [axis]
    label per #273 / D020. We assert on plain-text output so the bracket
    form survives the Rich color stripping done by ``export_text``."""

    def test_quality_axis_appears_in_summary(self) -> None:
        diag = DiagnosticsResult(
            aggregated_recommendations=[
                _agg(agent_type="explore", primary_axis="quality"),
            ],
        )
        text = _render(diag, top_n=5)
        assert "[quality]" in text

    def test_cost_axis_appears_in_summary(self) -> None:
        diag = DiagnosticsResult(
            aggregated_recommendations=[
                _agg(agent_type="pm", primary_axis="cost"),
            ],
        )
        text = _render(diag, top_n=5)
        assert "[cost]" in text

    def test_speed_axis_appears_in_summary(self) -> None:
        diag = DiagnosticsResult(
            aggregated_recommendations=[
                _agg(agent_type="architect", primary_axis="speed"),
            ],
        )
        text = _render(diag, top_n=5)
        assert "[speed]" in text


class TestAxisPrefixOnAggregatedTable:
    """The aggregated Recommendations table prepends the [axis] label to
    the message cell — not as a new column. PRD-confirmed format."""

    def test_axis_appears_in_message_cell(self) -> None:
        diag = DiagnosticsResult(
            aggregated_recommendations=[
                _agg(
                    agent_type="explore",
                    representative_message="Tighten prompt.",
                    primary_axis="quality",
                ),
            ],
        )
        text = _render(diag, top_n=5)
        # Confirm both the prefix and the message body appear; the row
        # carries them together.
        assert "[quality]" in text
        assert "Tighten prompt" in text

    def test_each_row_carries_its_own_axis_prefix(self) -> None:
        diag = DiagnosticsResult(
            aggregated_recommendations=[
                _agg(agent_type="explore", primary_axis="quality"),
                _agg(agent_type="pm", primary_axis="cost"),
                _agg(agent_type="architect", primary_axis="speed"),
            ],
        )
        text = _render(diag, top_n=0)  # suppress the top-N summary
        assert "[quality]" in text
        assert "[cost]" in text
        assert "[speed]" in text


class TestVerboseRendersAggregatedNotRaw:
    """Architect-mandated change in #273: verbose now renders the
    aggregated table (with per-row breakdown lines below) instead of
    the legacy raw unaggregated table."""

    def test_verbose_drops_raw_recommendations_table(self) -> None:
        # The legacy "Observation" / "Action" columns were the
        # discriminating headers on the raw recommendations table. They
        # should no longer appear in verbose output.
        diag = DiagnosticsResult(
            recommendations=[
                DiagnosticRecommendation(
                    target="prompt",
                    severity=Severity.WARNING,
                    message="raw msg",
                    observation="raw observation text",
                    action="raw action text",
                    agent_type="pm",
                    signal_types=[SignalType.RETRY_LOOP],
                ),
            ],
            aggregated_recommendations=[_agg(agent_type="pm")],
        )
        text = _render(diag, top_n=5, verbose=True)
        # Specifically, the raw observation/action strings shouldn't
        # appear; the aggregated representative_message should.
        assert "raw observation text" not in text
        assert "raw action text" not in text
        assert "Add fallback guidance" in text

    def test_verbose_renders_priority_breakdown_line(self) -> None:
        diag = DiagnosticsResult(
            aggregated_recommendations=[
                _agg(
                    agent_type="pm",
                    priority_score=312.4,
                    axis_scores={"cost": 12.4, "speed": 0.0, "quality": 300.0},
                    primary_axis="quality",
                ),
            ],
        )
        text = _render(diag, top_n=5, verbose=True)
        assert "Priority breakdown" in text
        assert "Priority: 312.4" in text
        assert "cost: 12.4" in text
        assert "speed: 0.0" in text
        assert "quality: 300.0" in text

    def test_non_verbose_omits_priority_breakdown_line(self) -> None:
        diag = DiagnosticsResult(
            aggregated_recommendations=[
                _agg(
                    agent_type="pm",
                    priority_score=312.4,
                    axis_scores={"cost": 12.4, "speed": 0.0, "quality": 300.0},
                ),
            ],
        )
        text = _render(diag, top_n=5, verbose=False)
        assert "Priority breakdown" not in text
        assert "Priority: 312.4" not in text

    def test_verbose_still_shows_aggregated_recommendations_header(self) -> None:
        diag = DiagnosticsResult(
            aggregated_recommendations=[_agg(agent_type="pm")],
        )
        text = _render(diag, top_n=5, verbose=True)
        # The aggregated table title is "Recommendations" (same as
        # before — the change is what's underneath, not the heading).
        assert "Recommendations" in text
