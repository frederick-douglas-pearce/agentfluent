"""Tests for the Top-N priority-fixes summary block (#172).

Covers the `_format_top_recommendations` block that renders above the
full Recommendations table and the index column on the full table that
matches the summary numbers. Suppression rules (top_n=0, no aggregated
rows, verbose mode) are exercised via `_format_diagnostics_table`.
"""

from __future__ import annotations

from agentfluent.config.models import Severity
from agentfluent.diagnostics.models import (
    DiagnosticRecommendation,
    DiagnosticsResult,
    SignalType,
)
from tests.unit.cli._recommendation_helpers import (
    make_agg,
    render_section,
    render_top_only,
)


class TestTopRecommendationsBlock:
    def test_renders_block_with_n_entries(self) -> None:
        diag = DiagnosticsResult(
            aggregated_recommendations=[
                make_agg(agent_type=f"agent-{i}", priority_score=300.0 - i)
                for i in range(8)
            ],
        )
        text = render_top_only(diag, top_n=5)
        assert "Top 5 priority fixes" in text
        for i in range(5):
            assert f"agent-{i}" in text
        # 6th, 7th, 8th NOT in summary.
        assert "agent-5" not in text
        assert "agent-7" not in text

    def test_caps_at_available_aggs_when_fewer_than_n(self) -> None:
        # 3 recs but --top-n=5 → render block with all 3, header
        # reflects "Top 3" not "Top 5". Avoids the strict-reading UX
        # gap where users pass --top-n=5 and see no block.
        diag = DiagnosticsResult(
            aggregated_recommendations=[
                make_agg(agent_type=f"agent-{i}") for i in range(3)
            ],
        )
        text = render_top_only(diag, top_n=5)
        assert "Top 3 priority fixes" in text

    def test_top_n_zero_suppresses_block(self) -> None:
        diag = DiagnosticsResult(
            aggregated_recommendations=[make_agg(agent_type="pm")],
        )
        text = render_top_only(diag, top_n=0)
        assert text == ""

    def test_no_aggs_suppresses_block(self) -> None:
        diag = DiagnosticsResult(aggregated_recommendations=[])
        text = render_top_only(diag, top_n=5)
        assert text == ""

    def test_count_suffix_only_when_gt_one(self) -> None:
        diag = DiagnosticsResult(
            aggregated_recommendations=[
                make_agg(agent_type="single", count=1),
                make_agg(agent_type="multi", count=4),
            ],
        )
        text = render_top_only(diag, top_n=5)
        # No count suffix on count=1 rows (the "(1×)" would be noise).
        single_line = next(
            line for line in text.split("\n") if "single" in line
        )
        multi_line = next(
            line for line in text.split("\n") if "multi" in line
        )
        assert "(1×)" not in single_line
        assert "(4×)" in multi_line


class TestFullTableIndexColumn:
    """The full Recommendations table gets a leading `#` column whose
    numbers match the Top-N entries. Cross-reference is by index."""

    def test_index_column_present_when_aggs_exist(self) -> None:
        diag = DiagnosticsResult(
            aggregated_recommendations=[make_agg(agent_type="pm")],
        )
        text = render_section(diag, top_n=5, width=120)
        assert "#" in text
        rec_section_start = text.index("Recommendations")
        assert "1" in text[rec_section_start:]

    def test_index_numbers_match_top_n_entries(self) -> None:
        diag = DiagnosticsResult(
            aggregated_recommendations=[
                make_agg(agent_type="alpha", priority_score=300.0),
                make_agg(agent_type="beta", priority_score=200.0),
                make_agg(agent_type="gamma", priority_score=100.0),
            ],
        )
        text = render_section(diag, top_n=2, width=120)
        summary_alpha_idx = text.index("1. ")
        rec_section = text[text.index("Recommendations"):]
        assert "alpha" in rec_section
        # gamma is NOT in the summary (top_n=2) but IS in the full table.
        assert "gamma" not in text[:summary_alpha_idx]
        assert "gamma" in rec_section


class TestSuppressionInVerboseMode:
    """Verbose mode renders the aggregated Recommendations table plus a
    per-row priority breakdown line; the top-N summary is suppressed
    because the breakdown line conveys the same priority info at higher
    granularity."""

    def test_verbose_skips_top_block(self) -> None:
        diag = DiagnosticsResult(
            recommendations=[
                DiagnosticRecommendation(
                    target="prompt",
                    severity=Severity.WARNING,
                    message="raw",
                    observation="obs",
                    action="act",
                    agent_type="pm",
                    signal_types=[SignalType.RETRY_LOOP],
                ),
            ],
            aggregated_recommendations=[make_agg()],
        )
        text = render_section(diag, top_n=5, verbose=True, width=120)
        assert "Top 5 priority fixes" not in text
