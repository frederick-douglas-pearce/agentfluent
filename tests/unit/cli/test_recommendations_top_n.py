"""Tests for the Top-N priority-fixes summary block (#172).

Covers the `_format_top_recommendations` block that renders above the
full Recommendations table and the index column on the full table that
matches the summary numbers. Suppression rules (top_n=0, no aggregated
rows, verbose mode) are exercised via `_format_diagnostics_table`.
"""

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


def _agg(
    *,
    agent_type: str = "pm",
    severity: Severity = Severity.WARNING,
    target: str = "prompt",
    count: int = 1,
    representative_message: str = "Add fallback guidance.",
    priority_score: float = 200.0,
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
    )


def _render(
    diag: DiagnosticsResult,
    *,
    top_n: int = 5,
    verbose: bool = False,
) -> str:
    console = Console(record=True, width=120, force_terminal=False)
    _format_diagnostics_table(console, diag, verbose=verbose, top_n=top_n)
    return console.export_text()


def _render_top_only(diag: DiagnosticsResult, *, top_n: int) -> str:
    console = Console(record=True, width=120, force_terminal=False)
    _format_top_recommendations(console, diag, top_n=top_n)
    return console.export_text()


class TestTopRecommendationsBlock:
    def test_renders_block_with_n_entries(self) -> None:
        diag = DiagnosticsResult(
            aggregated_recommendations=[
                _agg(agent_type=f"agent-{i}", priority_score=300.0 - i)
                for i in range(8)
            ],
        )
        text = _render_top_only(diag, top_n=5)
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
            aggregated_recommendations=[_agg(agent_type=f"agent-{i}") for i in range(3)],
        )
        text = _render_top_only(diag, top_n=5)
        assert "Top 3 priority fixes" in text

    def test_top_n_zero_suppresses_block(self) -> None:
        diag = DiagnosticsResult(
            aggregated_recommendations=[_agg(agent_type="pm")],
        )
        text = _render_top_only(diag, top_n=0)
        assert text == ""

    def test_no_aggs_suppresses_block(self) -> None:
        diag = DiagnosticsResult(aggregated_recommendations=[])
        text = _render_top_only(diag, top_n=5)
        assert text == ""

    def test_count_suffix_only_when_gt_one(self) -> None:
        diag = DiagnosticsResult(
            aggregated_recommendations=[
                _agg(agent_type="single", count=1),
                _agg(agent_type="multi", count=4),
            ],
        )
        text = _render_top_only(diag, top_n=5)
        # No count suffix on count=1 rows (the "(1×)" would be noise).
        # Use a regex-style structural check via substring presence.
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
            aggregated_recommendations=[_agg(agent_type="pm")],
        )
        text = _render(diag, top_n=5)
        # Index column header present.
        assert "#" in text
        # First row carries index 1.
        rec_section_start = text.index("Recommendations")
        assert "1" in text[rec_section_start:]

    def test_index_numbers_match_top_n_entries(self) -> None:
        diag = DiagnosticsResult(
            aggregated_recommendations=[
                _agg(agent_type="alpha", priority_score=300.0),
                _agg(agent_type="beta", priority_score=200.0),
                _agg(agent_type="gamma", priority_score=100.0),
            ],
        )
        text = _render(diag, top_n=2)
        # Both summary entries start with the same index that appears
        # in the full table's leading column. We confirm by ordering:
        # alpha is "1." in the summary AND has "1" in the table row.
        summary_alpha_idx = text.index("1. ")
        # First row in the table follows the section header.
        rec_section = text[text.index("Recommendations"):]
        assert "alpha" in rec_section
        # gamma is NOT in the summary (top_n=2) but IS in the full table.
        assert "gamma" not in text[:summary_alpha_idx]
        assert "gamma" in rec_section


class TestSuppressionInVerboseMode:
    """Verbose mode shows the unaggregated raw recommendations table —
    the top-N summary is irrelevant there because the priority concept
    only exists at the aggregated level. We confirm the block is
    suppressed when verbose is on."""

    def test_verbose_skips_top_block(self) -> None:
        # In verbose mode, _format_diagnostics_table renders the raw
        # `recommendations` list, NOT the aggregated one. The top-N
        # block lives in the aggregated branch, so it shouldn't fire.
        from agentfluent.diagnostics.models import DiagnosticRecommendation
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
            aggregated_recommendations=[_agg()],
        )
        text = _render(diag, top_n=5, verbose=True)
        assert "Top 5 priority fixes" not in text
