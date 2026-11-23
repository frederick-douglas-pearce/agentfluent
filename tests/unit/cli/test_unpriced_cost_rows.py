"""Unpriced figures render as "no price", partial rows as "(partial)", never $0.00 (#715).

Covers every renderer the issue names -- the ``analyze`` Cost by Model table,
the ``report`` Token Metrics table, the offload table under
``--show-negative-savings`` and the ``--verbose`` YAML preamble -- plus the
totals (``analyze`` Total cost row, ``--quiet`` line and per-session Cost
column; ``report`` Summary and Total row).

Fixtures carry cache tokens where it matters: ``unpriced_tokens`` counts all
four token components, so a renderer dividing by input (or input + output)
alone would misread a cache-heavy partly-unpriced figure as wholly unpriced.
"""

from __future__ import annotations

from functools import partial
from pathlib import Path
from typing import Any

import pytest
from rich.console import Console

from agentfluent.analytics.agent_metrics import AgentMetrics
from agentfluent.analytics.pipeline import AnalysisResult, SessionAnalysis
from agentfluent.analytics.tokens import NO_PRICE as ANALYTICS_NO_PRICE
from agentfluent.analytics.tokens import ModelTokenBreakdown, TokenMetrics
from agentfluent.analytics.tools import ToolMetrics
from agentfluent.cli.commands import analyze as analyze_cmd
from agentfluent.cli.commands.report_renderers import render_summary, render_token_metrics
from agentfluent.cli.formatters.helpers import NO_PRICE, format_priced_cost
from agentfluent.cli.formatters.table import _format_offload_candidates, format_analysis_table
from agentfluent.diagnostics.models import DiagnosticsResult
from tests.unit.cli.conftest import render_section
from tests.unit.cli.test_offload_candidates_formatting import _candidate

_UNPRICED = "claude-future-9"
_UNPRICED_2 = "claude-future-8"


class TestFormatPricedCost:
    def test_priced_row_renders_the_cost(self) -> None:
        assert format_priced_cost(1.5, total_tokens=100, unpriced_tokens=0) == "$1.50"

    def test_fully_unpriced_row_renders_no_price(self) -> None:
        assert format_priced_cost(0.0, total_tokens=100, unpriced_tokens=100) == NO_PRICE

    def test_partly_unpriced_row_is_marked_partial(self) -> None:
        assert format_priced_cost(1.5, total_tokens=100, unpriced_tokens=40) == "$1.50 (partial)"

    def test_unknown_unpriced_count_renders_as_before(self) -> None:
        # Pre-v0.12.0 envelope: no field, so "unpriced" cannot be claimed.
        assert format_priced_cost(0.0, total_tokens=100, unpriced_tokens=None) == "$0.0000"

    def test_zero_token_row_renders_the_cost(self) -> None:
        assert format_priced_cost(0.0, total_tokens=0, unpriced_tokens=0) == "$0.0000"


# ---- analyze --------------------------------------------------------------


def _breakdown(
    model: str,
    *,
    cost: float,
    unpriced: int,
    origin: str = "parent",
    cache_read: int = 0,
) -> ModelTokenBreakdown:
    return ModelTokenBreakdown(
        model=model,
        input_tokens=600,
        output_tokens=400,
        cache_read_input_tokens=cache_read,
        cost=cost,
        origin=origin,  # type: ignore[arg-type]
        unpriced_tokens=unpriced,
    )


def _token_metrics(rows: list[ModelTokenBreakdown]) -> TokenMetrics:
    return TokenMetrics(
        input_tokens=sum(r.input_tokens for r in rows),
        output_tokens=sum(r.output_tokens for r in rows),
        cache_read_input_tokens=sum(r.cache_read_input_tokens for r in rows),
        total_cost=sum(r.cost for r in rows),
        by_model=rows,
    )


def _analyze_text(rows: list[ModelTokenBreakdown]) -> str:
    console = Console(record=True, width=160, force_terminal=False)
    format_analysis_table(
        console, AnalysisResult(token_metrics=_token_metrics(rows)), verbose=True,
    )
    return console.export_text()


# 600 input + 400 output + 9,000 cache read = 10,000 tokens, half unpriced.
_CACHE_HEAVY = {"cost": 1.0, "unpriced": 5000, "cache_read": 9000}


def _line(text: str, needle: str) -> str:
    return next(line for line in text.splitlines() if needle in line)


def _table_row(text: str, needle: str) -> str:
    """The first Rich table row (a line containing ``│``) that mentions ``needle``.

    Used for Cost by Model rows (by model id) and Per-Session Breakdown rows (by
    session file name). Requiring ``│`` skips the coverage footer, which also
    names unpriced models.
    """
    return next(line for line in text.splitlines() if needle in line and "│" in line)


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

    def test_cache_heavy_partly_unpriced_row_is_partial(self) -> None:
        text = _analyze_text([_breakdown("claude-opus-4-7", **_CACHE_HEAVY)])
        row = _table_row(text, "claude-opus-4-7")
        assert "$1.00 (partial)" in row
        assert NO_PRICE not in row

    def test_cache_heavy_partly_unpriced_total_is_partial(self) -> None:
        text = _analyze_text([_breakdown("claude-opus-4-7", **_CACHE_HEAVY)])
        row = _line(text, "Total cost (API rate)")
        assert "$1.00 (partial)" in row
        assert NO_PRICE not in row

    def test_total_over_several_wholly_unpriced_rows_says_no_price(self) -> None:
        text = _analyze_text([
            _breakdown(_UNPRICED, cost=0.0, unpriced=1000),
            _breakdown(_UNPRICED_2, cost=0.0, unpriced=1000),
        ])
        row = _line(text, "Total cost (API rate)")
        assert NO_PRICE in row
        assert "(partial)" not in row

    def test_fully_priced_total_is_unmarked(self) -> None:
        text = _analyze_text([_breakdown("claude-opus-4-7", cost=2.0, unpriced=0)])
        row = _line(text, "Total cost (API rate)")
        assert "$2.00" in row
        assert "(partial)" not in row


def _session(name: str, rows: list[ModelTokenBreakdown]) -> SessionAnalysis:
    return SessionAnalysis(
        session_path=Path(name),
        token_metrics=_token_metrics(rows),
        tool_metrics=ToolMetrics(),
        agent_metrics=AgentMetrics(),
    )


class TestAnalyzePerSessionCost:
    def _text(self, sessions: list[SessionAnalysis]) -> str:
        console = Console(record=True, width=160, force_terminal=False)
        format_analysis_table(console, AnalysisResult(sessions=sessions), verbose=True)
        text = console.export_text()
        assert "Per-Session Breakdown" in text
        return text

    def test_unpriced_session_says_no_price(self) -> None:
        text = self._text([
            _session("cheap.jsonl", [_breakdown("claude-opus-4-7", cost=2.0, unpriced=0)]),
            _session("unpriced.jsonl", [
                _breakdown(_UNPRICED, cost=0.0, unpriced=1000),
                _breakdown(_UNPRICED_2, cost=0.0, unpriced=1000),
            ]),
        ])
        row = _table_row(text, "unpriced.jsonl")
        assert NO_PRICE in row
        assert "$0" not in row
        assert "$2.00" in _table_row(text, "cheap.jsonl")

    def test_cache_heavy_partly_unpriced_session_is_partial(self) -> None:
        text = self._text([
            _session("partial.jsonl", [_breakdown("claude-opus-4-7", **_CACHE_HEAVY)]),
            _session("cheap.jsonl", [_breakdown("claude-opus-4-7", cost=2.0, unpriced=0)]),
        ])
        row = _table_row(text, "partial.jsonl")
        assert "$1.00 (partial)" in row
        assert NO_PRICE not in row
        assert "(partial)" not in _table_row(text, "cheap.jsonl")


class TestAnalyzeQuietLine:
    def _quiet(self, monkeypatch: pytest.MonkeyPatch, rows: list[ModelTokenBreakdown]) -> str:
        console = Console(record=True, width=200, force_terminal=False)
        monkeypatch.setattr(analyze_cmd, "console", console)
        analyze_cmd._print_quiet(AnalysisResult(token_metrics=_token_metrics(rows)), "demo")
        return console.export_text()

    def test_wholly_unpriced_says_no_price(self, monkeypatch: pytest.MonkeyPatch) -> None:
        out = self._quiet(monkeypatch, [
            _breakdown(_UNPRICED, cost=0.0, unpriced=1000),
            _breakdown(_UNPRICED_2, cost=0.0, unpriced=1000),
        ])
        assert f"Project demo: {NO_PRICE} cost," in out
        assert "$0" not in out

    def test_cache_heavy_partly_unpriced_is_partial(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        out = self._quiet(monkeypatch, [_breakdown("claude-opus-4-7", **_CACHE_HEAVY)])
        assert "Project demo: $1.00 (partial) cost," in out

    def test_priced_is_unmarked(self, monkeypatch: pytest.MonkeyPatch) -> None:
        out = self._quiet(monkeypatch, [_breakdown("claude-opus-4-7", cost=2.0, unpriced=0)])
        assert "Project demo: $2.00 cost," in out


# ---- report ---------------------------------------------------------------


def _row(
    model: str,
    *,
    cost: float,
    unpriced: int | None,
    origin: str = "parent",
    cache_read: int = 0,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "model": model,
        "input_tokens": 600,
        "output_tokens": 400,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": cache_read,
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
            "input_tokens": sum(r["input_tokens"] for r in rows),
            "output_tokens": sum(r["output_tokens"] for r in rows),
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": sum(r["cache_read_input_tokens"] for r in rows),
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

    def test_cache_heavy_partly_unpriced_row_is_partial(self) -> None:
        out = render_token_metrics(_report_data([_row("claude-opus-4-7", **_CACHE_HEAVY)]))
        row = _line(out, "claude-opus-4-7")
        assert "$1.00 (partial)" in row
        assert NO_PRICE not in row

    def test_cache_heavy_partly_unpriced_total_is_partial(self) -> None:
        out = render_token_metrics(_report_data([_row("claude-opus-4-7", **_CACHE_HEAVY)]))
        assert "**$1.00 (partial)**" in _line(out, "**Total**")

    def test_total_over_several_wholly_unpriced_rows_says_no_price(self) -> None:
        out = render_token_metrics(_report_data([
            _row(_UNPRICED, cost=0.0, unpriced=1000),
            _row(_UNPRICED_2, cost=0.0, unpriced=1000),
        ]))
        row = _line(out, "**Total**")
        assert f"**{NO_PRICE}**" in row
        assert "(partial)" not in row

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

    def test_cache_heavy_partly_unpriced_total_is_partial(self) -> None:
        out = render_summary(_report_data([_row("claude-opus-4-7", **_CACHE_HEAVY)]))
        line = _line(out, "Total cost (API rate)")
        assert "$1.00 (partial)" in line
        assert NO_PRICE not in line

    def test_total_over_several_wholly_unpriced_rows_says_no_price(self) -> None:
        out = render_summary(_report_data([
            _row(_UNPRICED, cost=0.0, unpriced=1000),
            _row(_UNPRICED_2, cost=0.0, unpriced=1000),
        ]))
        line = _line(out, "Total cost (API rate)")
        assert NO_PRICE in line
        assert "(partial)" not in line

    def test_priced_total_is_unmarked(self) -> None:
        out = render_summary(_report_data([_row("claude-opus-4-7", cost=2.0, unpriced=0)]))
        line = _line(out, "Total cost (API rate)")
        assert "$2.00" in line
        assert "(partial)" not in line

    def test_mixed_legacy_rows_render_the_total_as_before(self) -> None:
        # One row carries the count, one predates it: the total's unpriced
        # share is unknown, so no marker is claimed either way (#723, D2).
        out = render_summary(_report_data([
            _row(_UNPRICED, cost=0.0, unpriced=1000),
            _row("claude-opus-4-7", cost=2.0, unpriced=None),
        ]))
        line = _line(out, "Total cost (API rate)")
        assert "$2.00" in line
        assert "(partial)" not in line
        assert NO_PRICE not in line


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
    def test_cli_and_diagnostics_share_one_no_price_token(self) -> None:
        # The YAML preamble (diagnostics) and the tables (cli) both render
        # analytics.tokens.NO_PRICE; cli re-exports it rather than redefining it.
        assert NO_PRICE is ANALYTICS_NO_PRICE

    def test_unpriced_candidate_preamble_says_no_price(self) -> None:
        draft = _unpriced_candidate().yaml_draft
        assert f"# Parent-thread cost: {ANALYTICS_NO_PRICE} (50,000 tokens)" in draft
        assert f"# Estimated savings: {ANALYTICS_NO_PRICE}" in draft
        assert "$0.0000" not in draft

    def test_unpriced_preamble_also_renders_in_verbose_table_output(self) -> None:
        text = _render_offload(
            DiagnosticsResult(offload_candidates=[_unpriced_candidate()]), verbose=True,
        )
        assert f"# Estimated savings: {NO_PRICE}" in text
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
