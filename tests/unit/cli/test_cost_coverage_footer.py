"""The cost-coverage footer in ``analyze`` and ``report`` (#676 AC3, #688)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from rich.console import Console

from agentfluent.analytics.pipeline import analyze_sessions
from agentfluent.cli.commands.report_renderers import render_token_metrics
from agentfluent.cli.formatters.coverage import (
    INCOMPLETE_HEADLINE,
    UNKNOWN_HEADLINE,
    cost_coverage_footer,
)
from agentfluent.cli.formatters.table import format_analysis_table
from tests.unit.test_trace_coverage import _UNPRICED_MODEL, _build_session

_SDK_MAIN = Path(__file__).parent.parent.parent / "fixtures" / "sdk_session" / "sdk-main-1.jsonl"


def _coverage(
    *,
    tokens: int = 1_000,
    unpriced: dict[str, int] | None = None,
    invocations: tuple[int, int, int, int] = (3, 3, 3, 3),
    diagnostics: dict[str, int] | None = None,
) -> dict[str, Any]:
    unpriced = unpriced or {}
    total, linked, spend, priced = invocations
    return {
        "pricing": {
            "tokens": tokens,
            "unpriced_tokens": sum(unpriced.values()),
            "unpriced_models": unpriced,
        },
        "invocations": {"total": total, "linked": linked, "spend": spend, "priced": priced},
        "diagnostics": diagnostics,
    }


class TestFooterText:
    def test_full_coverage_is_one_concise_line(self) -> None:
        footer = cost_coverage_footer(_coverage(tokens=1_500), None)
        assert footer.status == "full"
        assert footer.clauses == []
        assert footer.headline == (
            "Cost coverage: full. All 1,500 tokens (100%) priced; "
            "3 of 3 agent invocations linked and trace-priced."
        )

    def test_full_with_no_invocations_does_not_claim_agent_coverage(self) -> None:
        footer = cost_coverage_footer(_coverage(invocations=(0, 0, 0, 0)), None)
        assert footer.status == "full"
        assert footer.headline.endswith("priced; no agent invocations.")

    def test_trace_without_model_response_is_not_a_gap(self) -> None:
        footer = cost_coverage_footer(_coverage(invocations=(3, 3, 2, 2)), None)
        assert footer.status == "full"
        assert "3 of 3 agent invocations linked, 2 trace-priced" in footer.headline
        assert "1 with no model response" in footer.headline

    def test_unpriced_tokens_name_every_model(self) -> None:
        footer = cost_coverage_footer(
            _coverage(tokens=10_000, unpriced={"claude-a": 1_200, "claude-b": 300}), None,
        )
        assert footer.status == "incomplete"
        assert footer.headline == INCOMPLETE_HEADLINE
        assert footer.clauses == [
            "Pricing: 1,500 of 10,000 tokens (15.0%) have no price and count as $0. "
            "Unpriced tokens by model: claude-a (1,200), claude-b (300)."
        ]

    def test_zero_pricing_coverage(self) -> None:
        footer = cost_coverage_footer(
            _coverage(tokens=500, unpriced={"claude-opus-5-5": 500}), None,
        )
        assert footer.status == "incomplete"
        assert footer.clauses[0].startswith("Pricing: 500 of 500 tokens (100%) have no price")

    def test_tiny_gap_never_rounds_to_zero_percent(self) -> None:
        footer = cost_coverage_footer(
            _coverage(tokens=1_000_000, unpriced={"claude-a": 10}), None,
        )
        assert "(0.1%)" in footer.clauses[0]

    def test_near_total_gap_never_rounds_to_hundred_percent(self) -> None:
        footer = cost_coverage_footer(
            _coverage(tokens=1_000_000, unpriced={"claude-a": 999_990}), None,
        )
        assert "(99.9%)" in footer.clauses[0]

    def test_unparseable_trace_files_are_a_gap(self) -> None:
        """D1: their spend is in no total, so the footer must not say "full"."""
        footer = cost_coverage_footer(_coverage(), {"unparseable": {"files": 2}})
        assert footer.status == "incomplete"
        assert footer.clauses == [
            "Subagent traces: 2 trace files could not be parsed; "
            "their spend is unknown and not in any total."
        ]
        assert footer.attribution == "Subagent traces: 2 unparseable (spend unknown)."

    def test_folded_cohorts_alone_are_not_a_gap(self) -> None:
        footer = cost_coverage_footer(
            _coverage(), {"nested": {"files": 2, "tokens": 5}, "session_spawned": {"files": 1}},
        )
        assert footer.status == "full"
        assert footer.attribution is not None

    def test_partial_link_coverage(self) -> None:
        footer = cost_coverage_footer(_coverage(invocations=(1_347, 1_332, 1_327, 1_327)), None)
        assert footer.status == "incomplete"
        assert footer.clauses == [
            "Agent cost: per-agent cost covers 1,327 of 1,347 agent invocations; "
            "15 with no linked trace (spend unknown); "
            "5 whose trace has no model response (no spend)."
        ]

    def test_partial_spend_pricing(self) -> None:
        footer = cost_coverage_footer(_coverage(invocations=(4, 4, 4, 1)), None)
        assert footer.clauses == [
            "Agent cost: per-agent cost covers 1 of 4 agent invocations; "
            "3 on an unpriced model."
        ]

    def test_diagnostics_gaps(self) -> None:
        footer = cost_coverage_footer(
            _coverage(
                diagnostics={"savings_figures": 3, "partial_figures": 2, "unpriced_figures": 1},
            ),
            None,
        )
        assert footer.status == "incomplete"
        assert footer.clauses == [
            'Diagnostics: 2 savings figures cover only some of their invocations '
            '(see the "k of N" note on each); 1 estimate unavailable '
            "(no priced trace spend for the invocations behind it)."
        ]

    def test_complete_diagnostics_are_not_a_gap(self) -> None:
        footer = cost_coverage_footer(
            _coverage(
                diagnostics={"savings_figures": 3, "partial_figures": 0, "unpriced_figures": 0},
            ),
            None,
        )
        assert footer.status == "full"

    def test_text_follows_the_inputs(self) -> None:
        """AC4: every number comes from the block, none is a constant."""
        small = cost_coverage_footer(_coverage(invocations=(5, 4, 4, 4)), None)
        large = cost_coverage_footer(_coverage(invocations=(50, 41, 41, 41)), None)
        assert "covers 4 of 5" in small.clauses[0]
        assert "covers 41 of 50" in large.clauses[0]
        assert "9 with no linked trace" in large.clauses[0]

    def test_missing_or_null_block_is_unknown_never_full(self) -> None:
        for block in (None, {}):
            footer = cost_coverage_footer(block, None)
            assert footer.status == "unknown"
            assert footer.headline == UNKNOWN_HEADLINE

    def test_attribution_line_rides_along(self) -> None:
        trace = {"nested": {"files": 2, "tokens": 10}}
        footer = cost_coverage_footer(_coverage(), trace)
        assert footer.attribution is not None
        assert footer.attribution.startswith("Subagent traces: 2 nested")


class TestRendered:
    def _analyze_text(self, paths: list[Path]) -> str:
        console = Console(record=True, width=400)
        format_analysis_table(console, analyze_sessions(paths))
        return console.export_text()

    def test_analyze_shows_gaps(self, tmp_path: Path) -> None:
        text = self._analyze_text([_build_session(tmp_path)])
        assert INCOMPLETE_HEADLINE in text
        assert _UNPRICED_MODEL in text
        assert "1 with no linked trace (spend unknown)" in text

    def test_analyze_full_coverage(self) -> None:
        text = self._analyze_text([_SDK_MAIN])
        assert "Cost coverage: full." in text
        assert "INCOMPLETE" not in text

    def test_report_matches_analyze(self, tmp_path: Path) -> None:
        result = analyze_sessions([_build_session(tmp_path)])
        data = json.loads(result.model_dump_json())
        footer = cost_coverage_footer(data["cost_coverage"], data["trace_coverage"])
        report = render_token_metrics(data)
        console = Console(record=True, width=400)
        format_analysis_table(console, result)
        analyze = console.export_text()
        assert f"**{footer.headline}**" in report
        for clause in footer.clauses:
            assert f"- {clause}" in report
            assert clause in analyze
        assert footer.attribution is not None
        assert footer.attribution in report and footer.attribution in analyze

    def test_report_full_coverage_is_a_plain_line_matching_analyze(self) -> None:
        """D6: the Markdown full branch renders the same headline, not bolded."""
        result = analyze_sessions([_SDK_MAIN])
        data = json.loads(result.model_dump_json())
        footer = cost_coverage_footer(data["cost_coverage"], data["trace_coverage"])
        assert footer.status == "full"
        report = render_token_metrics(data)
        assert footer.headline.startswith("Cost coverage: full.")
        assert f"\n{footer.headline}\n" in report
        assert f"**{footer.headline}**" not in report
        console = Console(record=True, width=400)
        format_analysis_table(console, result)
        assert footer.headline in console.export_text()

    def test_report_of_legacy_envelope_reads_unknown(self, tmp_path: Path) -> None:
        data = json.loads(analyze_sessions([_build_session(tmp_path)]).model_dump_json())
        del data["cost_coverage"]
        report = render_token_metrics(data)
        assert f"**{UNKNOWN_HEADLINE}**" in report
        assert "Cost coverage: full" not in report
