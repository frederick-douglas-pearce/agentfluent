"""Axis attribution rendering on aggregated recommendations.

Locks the ``[axis]`` prefix on the aggregated Recommendations table,
the top-N priority summary, and the verbose-mode per-row breakdown
line. Also confirms that verbose mode renders the aggregated table
with breakdown lines instead of the legacy raw unaggregated table.
"""

from __future__ import annotations

from agentfluent.config.models import Severity
from agentfluent.diagnostics.models import (
    DiagnosticRecommendation,
    DiagnosticsResult,
    SignalType,
)
from tests.unit.cli._recommendation_helpers import make_agg, render_section


class TestAxisPrefixOnTopN:
    def test_quality_axis_appears_in_summary(self) -> None:
        diag = DiagnosticsResult(
            aggregated_recommendations=[
                make_agg(agent_type="explore", primary_axis="quality"),
            ],
        )
        text = render_section(diag, top_n=5)
        assert "[quality]" in text

    def test_cost_axis_appears_in_summary(self) -> None:
        diag = DiagnosticsResult(
            aggregated_recommendations=[
                make_agg(agent_type="pm", primary_axis="cost"),
            ],
        )
        text = render_section(diag, top_n=5)
        assert "[cost]" in text

    def test_speed_axis_appears_in_summary(self) -> None:
        diag = DiagnosticsResult(
            aggregated_recommendations=[
                make_agg(agent_type="architect", primary_axis="speed"),
            ],
        )
        text = render_section(diag, top_n=5)
        assert "[speed]" in text


class TestAxisPrefixOnAggregatedTable:
    """Axis label is prepended to the message cell, not added as a
    new column."""

    def test_axis_appears_in_message_cell(self) -> None:
        diag = DiagnosticsResult(
            aggregated_recommendations=[
                make_agg(
                    agent_type="explore",
                    representative_message="Tighten prompt.",
                    primary_axis="quality",
                ),
            ],
        )
        text = render_section(diag, top_n=5)
        assert "[quality]" in text
        assert "Tighten prompt" in text

    def test_each_row_carries_its_own_axis_prefix(self) -> None:
        diag = DiagnosticsResult(
            aggregated_recommendations=[
                make_agg(agent_type="explore", primary_axis="quality"),
                make_agg(agent_type="pm", primary_axis="cost"),
                make_agg(agent_type="architect", primary_axis="speed"),
            ],
        )
        text = render_section(diag, top_n=0)
        assert "[quality]" in text
        assert "[cost]" in text
        assert "[speed]" in text


class TestVerboseRendersAggregatedNotRaw:
    def test_verbose_drops_raw_recommendations_table(self) -> None:
        # ``observation`` / ``action`` are the discriminating columns of
        # the legacy raw table; they must not appear in verbose output.
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
            aggregated_recommendations=[make_agg(agent_type="pm")],
        )
        text = render_section(diag, top_n=5, verbose=True)
        assert "raw observation text" not in text
        assert "raw action text" not in text
        assert "Add fallback guidance" in text

    def test_verbose_renders_priority_breakdown_line(self) -> None:
        diag = DiagnosticsResult(
            aggregated_recommendations=[
                make_agg(
                    agent_type="pm",
                    priority_score=312.4,
                    axis_scores={"cost": 12.4, "speed": 0.0, "quality": 300.0},
                    primary_axis="quality",
                ),
            ],
        )
        text = render_section(diag, top_n=5, verbose=True)
        assert "Priority breakdown" in text
        assert "Priority: 312.4" in text
        assert "cost: 12.4" in text
        assert "speed: 0.0" in text
        assert "quality: 300.0" in text

    def test_non_verbose_omits_priority_breakdown_line(self) -> None:
        diag = DiagnosticsResult(
            aggregated_recommendations=[
                make_agg(
                    agent_type="pm",
                    priority_score=312.4,
                    axis_scores={"cost": 12.4, "speed": 0.0, "quality": 300.0},
                ),
            ],
        )
        text = render_section(diag, top_n=5, verbose=False)
        assert "Priority breakdown" not in text
        assert "Priority: 312.4" not in text

    def test_verbose_still_shows_aggregated_recommendations_header(self) -> None:
        diag = DiagnosticsResult(
            aggregated_recommendations=[make_agg(agent_type="pm")],
        )
        text = render_section(diag, top_n=5, verbose=True)
        assert "Recommendations" in text
