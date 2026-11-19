"""The one-line ``trace_coverage`` disclosure in ``analyze`` and ``report`` (#648 AC5)."""

from __future__ import annotations

import json
from pathlib import Path

from rich.console import Console

from agentfluent.analytics.pipeline import analyze_sessions
from agentfluent.analytics.trace_coverage import CohortCoverage, TraceCoverage
from agentfluent.cli.commands.report_renderers import render_token_metrics
from agentfluent.cli.formatters.helpers import trace_coverage_line
from agentfluent.cli.formatters.table import format_analysis_table

_FIXTURES = Path(__file__).parent.parent / "fixtures"
_NESTED_MAIN = _FIXTURES / "nested_session" / "nested-session-1.jsonl"
_SDK_MAIN = _FIXTURES / "sdk_session" / "sdk-main-1.jsonl"


def _line(**cohorts: CohortCoverage) -> str | None:
    return trace_coverage_line(TraceCoverage(**cohorts).model_dump())


class TestCoverageLine:
    def test_full_coverage_renders_nothing(self) -> None:
        assert _line(linked=CohortCoverage(files=4, tokens=10, folded=True)) is None

    def test_absent_coverage_renders_nothing(self) -> None:
        """A pre-#648 envelope has no ``trace_coverage`` key at all."""
        assert trace_coverage_line(None) is None
        assert trace_coverage_line({}) is None

    def test_every_non_linked_cohort_is_named(self) -> None:
        line = _line(
            nested=CohortCoverage(files=3, tokens=1234, folded=True),
            session_spawned=CohortCoverage(files=2, tokens=56, folded=True),
            filtered=CohortCoverage(files=5),
            unparseable=CohortCoverage(files=1),
        )
        assert line == (
            "Subagent traces: 3 nested (1,234 tokens) and 2 session-spawned (56 tokens) "
            "included in totals, not in per-agent rows; 5 excluded by --agent; "
            "1 unparseable (spend unknown)."
        )

    def test_only_a_disclosed_cohort(self) -> None:
        assert _line(unparseable=CohortCoverage(files=2)) == (
            "Subagent traces: 2 unparseable (spend unknown)."
        )


class TestRenderedOutput:
    def test_analyze_table_shows_the_line_when_nested_folds(self) -> None:
        console = Console(record=True, width=200)
        format_analysis_table(console, analyze_sessions([_NESTED_MAIN]))
        assert "Subagent traces: 1 nested" in console.export_text()

    def test_analyze_table_omits_the_line_at_full_coverage(self) -> None:
        console = Console(record=True, width=200)
        format_analysis_table(console, analyze_sessions([_SDK_MAIN]))
        assert "Subagent traces:" not in console.export_text()

    def test_report_renders_it_from_rehydrated_json(self) -> None:
        data = json.loads(analyze_sessions([_NESTED_MAIN]).model_dump_json())
        assert "Subagent traces: 1 nested" in render_token_metrics(data)

    def test_report_omits_it_at_full_coverage(self) -> None:
        data = json.loads(analyze_sessions([_SDK_MAIN]).model_dump_json())
        assert "Subagent traces:" not in render_token_metrics(data)
