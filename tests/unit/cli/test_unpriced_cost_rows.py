"""Unpriced figures render as "no price", partial rows as "(partial)", never $0.00 (#715).

Covers every renderer the issue names -- the ``analyze`` Cost by Model table,
the ``report`` Token Metrics table, the offload table under
``--show-negative-savings`` and the ``--verbose`` YAML preamble -- plus the
totals (``analyze`` Total cost row, ``report`` Summary and Total row).
"""

from __future__ import annotations

from functools import partial
from typing import Any

from rich.console import Console

from agentfluent.analytics.pipeline import AnalysisResult
from agentfluent.analytics.tokens import ModelTokenBreakdown, TokenMetrics
from agentfluent.cli.commands.report_renderers import render_summary, render_token_metrics
from agentfluent.cli.formatters.helpers import NO_PRICE, format_row_cost
from agentfluent.cli.formatters.table import _format_offload_candidates, format_analysis_table
from agentfluent.diagnostics.models import DiagnosticsResult
from tests.unit.cli.conftest import render_section
from tests.unit.cli.test_offload_candidates_formatting import _candidate

_UNPRICED = "claude-future-9"


class TestFormatRowCost:
    def test_priced_row_renders_the_cost(self) -> None:
        assert format_row_cost(1.5, total_tokens=100, unpriced_tokens=0) == "$1.50"

    def test_fully_unpriced_row_renders_no_price(self) -> None:
        assert format_row_cost(0.0, total_tokens=100, unpriced_tokens=100) == NO_PRICE

    def test_partly_unpriced_row_is_marked_partial(self) -> None:
        assert format_row_cost(1.5, total_tokens=100, unpriced_tokens=40) == "$1.50 (partial)"

    def test_unknown_unpriced_count_renders_as_before(self) -> None:
        # Pre-v0.12.0 envelope: no field, so "unpriced" cannot be claimed.
        assert format_row_cost(0.0, total_tokens=100, unpriced_tokens=None) == "$0.0000"

    def test_zero_token_row_renders_the_cost(self) -> None:
        assert format_row_cost(0.0, total_tokens=0, unpriced_tokens=0) == "$0.0000"


# ---- analyze --------------------------------------------------------------


def _breakdown(
    model: str, *, cost: float, unpriced: int, origin: str = "parent",
) -> ModelTokenBreakdown:
    return ModelTokenBreakdown(
        model=model,
        input_tokens=600,
        output_tokens=400,
        cost=cost,
        origin=origin,  # type: ignore[arg-type]
        unpriced_tokens=unpriced,
    )


def _analyze_text(rows: list[ModelTokenBreakdown]) -> str:
    tm = TokenMetrics(
        input_tokens=600 * len(rows),
        output_tokens=400 * len(rows),
        total_cost=sum(r.cost for r in rows),
        by_model=rows,
    )
    console = Console(record=True, width=160, force_terminal=False)
    format_analysis_table(console, AnalysisResult(token_metrics=tm), verbose=True)
    return console.export_text()


def _line(text: str, needle: str) -> str:
    return next(line for line in text.splitlines() if needle in line)


def _table_row(text: str, model: str) -> str:
    """The Cost by Model row for ``model`` (the footer also names unpriced models)."""
    return next(line for line in text.splitlines() if model in line and "│" in line)


class TestAnalyzeCostByModel:
    def test_unpriced_row_says_no_price(self) -> None:
        text = _analyze_text([
            _breakdown("claude-opus-4-7", cost=2.0, unpriced=0),
            _breakdown(_UNPRICED, cost=0.0, unpriced=1000),
        ])
        row = _table_row(text, _UNPRICED)
        assert NO_PRICE in row
        assert "$0" not in row
        assert "$2.00" in _table_row(text, "claude-opus-4-7")

    def test_partly_unpriced_row_is_partial(self) -> None:
        text = _analyze_text([
            _breakdown("claude-opus-4-7", cost=1.25, unpriced=300),
            _breakdown("claude-sonnet-4-6", cost=0.5, unpriced=0),
        ])
        assert "$1.25 (partial)" in _table_row(text, "claude-opus-4-7")
        assert "(partial)" not in _table_row(text, "claude-sonnet-4-6")

    def test_total_cost_row_all_unpriced_says_no_price(self) -> None:
        text = _analyze_text([_breakdown(_UNPRICED, cost=0.0, unpriced=1000)])
        row = _line(text, "Total cost (API rate)")
        assert NO_PRICE in row
        assert "$0" not in row

    def test_total_cost_row_partly_unpriced_is_partial(self) -> None:
        text = _analyze_text([
            _breakdown("claude-opus-4-7", cost=2.0, unpriced=0),
            _breakdown(_UNPRICED, cost=0.0, unpriced=1000),
        ])
        assert "$2.00 (partial)" in _line(text, "Total cost (API rate)")

    def test_fully_priced_total_is_unmarked(self) -> None:
        text = _analyze_text([_breakdown("claude-opus-4-7", cost=2.0, unpriced=0)])
        row = _line(text, "Total cost (API rate)")
        assert "$2.00" in row
        assert "(partial)" not in row


# ---- report ---------------------------------------------------------------


def _row(
    model: str, *, cost: float, unpriced: int | None, origin: str = "parent",
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "model": model,
        "input_tokens": 600,
        "output_tokens": 400,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "cost": cost,
        "origin": origin,
    }
    if unpriced is not None:
        row["unpriced_tokens"] = unpriced
    return row


def _report_data(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "project_name": "demo",
        "session_count": 1,
        "window": None,
        "token_metrics": {
            "input_tokens": 600 * len(rows),
            "output_tokens": 400 * len(rows),
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "total_cost": sum(r["cost"] for r in rows),
            "by_model": rows,
        },
    }


class TestReportTokenMetrics:
    def test_unpriced_row_says_no_price(self) -> None:
        out = render_token_metrics(_report_data([
            _row("claude-opus-4-7", cost=2.0, unpriced=0),
            _row(_UNPRICED, cost=0.0, unpriced=1000),
        ]))
        row = _line(out, _UNPRICED)
        assert NO_PRICE in row
        assert "$0" not in row
        assert "$2.00" in _line(out, "claude-opus-4-7")

    def test_partly_unpriced_row_is_partial(self) -> None:
        out = render_token_metrics(_report_data([_row("claude-opus-4-7", cost=1.25, unpriced=300)]))
        assert "$1.25 (partial)" in _line(out, "claude-opus-4-7")

    def test_total_row_all_unpriced_says_no_price(self) -> None:
        out = render_token_metrics(_report_data([_row(_UNPRICED, cost=0.0, unpriced=1000)]))
        assert f"**{NO_PRICE}**" in _line(out, "**Total**")

    def test_total_row_partly_unpriced_is_partial(self) -> None:
        out = render_token_metrics(_report_data([
            _row("claude-opus-4-7", cost=2.0, unpriced=0),
            _row(_UNPRICED, cost=0.0, unpriced=1000),
        ]))
        assert "**$2.00 (partial)**" in _line(out, "**Total**")

    def test_legacy_rows_without_the_field_render_as_before(self) -> None:
        out = render_token_metrics(_report_data([_row("claude-opus-4-7", cost=0.0, unpriced=None)]))
        assert NO_PRICE not in out
        assert "(partial)" not in out
        assert "$0.0000" in _line(out, "claude-opus-4-7")
        assert "**$0.0000**" in _line(out, "**Total**")

    def test_one_legacy_row_makes_the_total_unknown(self) -> None:
        out = render_token_metrics(_report_data([
            _row("claude-opus-4-7", cost=2.0, unpriced=None),
            _row(_UNPRICED, cost=0.0, unpriced=1000),
        ]))
        assert "**$2.00**" in _line(out, "**Total**")


class TestReportSummary:
    def test_all_unpriced_total_says_no_price(self) -> None:
        out = render_summary(_report_data([_row(_UNPRICED, cost=0.0, unpriced=1000)]))
        line = _line(out, "Total cost (API rate)")
        assert NO_PRICE in line
        assert "$0" not in line

    def test_partly_unpriced_total_is_partial(self) -> None:
        out = render_summary(_report_data([
            _row("claude-opus-4-7", cost=2.0, unpriced=0),
            _row(_UNPRICED, cost=0.0, unpriced=1000),
        ]))
        assert "$2.00 (partial)" in _line(out, "Total cost (API rate)")

    def test_priced_total_is_unmarked(self) -> None:
        out = render_summary(_report_data([_row("claude-opus-4-7", cost=2.0, unpriced=0)]))
        line = _line(out, "Total cost (API rate)")
        assert "$2.00" in line
        assert "(partial)" not in line


# ---- offload table + --verbose YAML -----------------------------------------

_render_offload = partial(render_section, _format_offload_candidates, show_negative_savings=True)


def _unpriced_candidate() -> Any:
    return _candidate(
        name="future-runner",
        estimated_savings_usd=0.0,
        estimated_parent_cost_usd=0.0,
        estimated_parent_tokens=50_000,
        cost_priced=False,
    )


class TestOffloadTable:
    def test_unpriced_candidate_says_no_price(self) -> None:
        text = _render_offload(DiagnosticsResult(offload_candidates=[_unpriced_candidate()]))
        row = _line(text, "future-runner")
        assert NO_PRICE in row
        assert "$0" not in row

    def test_priced_zero_saving_still_renders_dollars(self) -> None:
        cand = _candidate(name="even-runner", estimated_savings_usd=0.0, cost_priced=True)
        text = _render_offload(DiagnosticsResult(offload_candidates=[cand]))
        row = _line(text, "even-runner")
        assert "$0.00" in row
        assert NO_PRICE not in row

    def test_legacy_candidate_renders_as_before(self) -> None:
        cand = _candidate(name="old-runner", estimated_savings_usd=0.0, cost_priced=None)
        text = _render_offload(DiagnosticsResult(offload_candidates=[cand]))
        assert "$0.00" in _line(text, "old-runner")


class TestOffloadYamlPreamble:
    def test_unpriced_candidate_preamble_says_no_price(self) -> None:
        draft = _unpriced_candidate().yaml_draft
        assert "# Parent-thread cost: no price (50,000 tokens)" in draft
        assert "# Estimated savings: no price" in draft
        assert "$0.0000" not in draft

    def test_unpriced_preamble_also_renders_in_verbose_table_output(self) -> None:
        text = _render_offload(
            DiagnosticsResult(offload_candidates=[_unpriced_candidate()]), verbose=True,
        )
        assert "# Estimated savings: no price" in text
        assert "$0.0000" not in text

    def test_priced_candidate_preamble_keeps_dollars(self) -> None:
        draft = _candidate(estimated_savings_usd=0.42, estimated_parent_cost_usd=1.25).yaml_draft
        assert "# Parent-thread cost: $1.2500 (50,000 tokens)" in draft
        assert "# Estimated savings: $0.4200" in draft
        assert NO_PRICE not in draft

    def test_legacy_candidate_preamble_renders_as_before(self) -> None:
        draft = _candidate(
            estimated_savings_usd=0.0, estimated_parent_cost_usd=0.0, cost_priced=None,
        ).yaml_draft
        assert "# Estimated savings: $0.0000" in draft
        assert NO_PRICE not in draft
