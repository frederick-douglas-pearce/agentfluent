"""Rendering tests for the ``Token Metrics by Model`` table in
``agentfluent diff`` output (#343).

Same model can appear twice in the ``by_model`` list when ``(model,
origin)`` differs (e.g., ``claude-opus-4-7`` for both ``parent`` and
``subagent``). The renderer must surface ``origin`` as a column so the
two rows look distinct instead of like a duplicate-row bug.
"""

from __future__ import annotations

import io

from rich.console import Console

from agentfluent.cli.formatters.diff_table import _render_by_model, _render_token_metrics
from agentfluent.diff.models import DiffResult, ModelTokenDelta, TokenMetricsDelta


def _render(rows: list[ModelTokenDelta]) -> str:
    buf = io.StringIO()
    console = Console(
        file=buf,
        width=200,
        force_terminal=False,
        color_system=None,
        legacy_windows=False,
    )
    _render_by_model(console, rows)
    return buf.getvalue()


def test_table_includes_origin_column() -> None:
    rows = [
        ModelTokenDelta(
            model="claude-opus-4-7",
            origin="parent",
            baseline_cost=10.0,
            current_cost=12.0,
            cost_delta=2.0,
        ),
    ]
    out = _render(rows)
    assert "Origin" in out
    assert "parent" in out


def test_parent_and_subagent_rows_render_distinctly() -> None:
    rows = [
        ModelTokenDelta(
            model="claude-opus-4-7",
            origin="parent",
            baseline_cost=346.22,
            current_cost=319.88,
            cost_delta=-26.34,
            total_tokens_delta=-28_019_807,
        ),
        ModelTokenDelta(
            model="claude-opus-4-7",
            origin="subagent",
            baseline_cost=24.38,
            current_cost=29.24,
            cost_delta=4.86,
            total_tokens_delta=6_446_681,
        ),
    ]
    out = _render(rows)
    # Both rows present with the same model name but different origins.
    assert out.count("claude-opus-4-7") == 2
    assert "parent" in out
    assert "subagent" in out
    # The two distinct cost figures appear, so the rows aren't merged.
    assert "$346.22" in out
    assert "$24.38" in out


# ---- unpriced cost cells (#723) ---------------------------------------------

_UNPRICED = "claude-future-9"


def _unpriced_row(
    *,
    baseline_cost: float,
    current_cost: float,
    baseline_unpriced: int | None,
    current_unpriced: int | None,
    model: str = "claude-opus-4-7",
    tokens: int = 1000,
) -> ModelTokenDelta:
    return ModelTokenDelta(
        model=model,
        baseline_total_tokens=tokens,
        current_total_tokens=tokens,
        baseline_cost=baseline_cost,
        current_cost=current_cost,
        cost_delta=current_cost - baseline_cost,
        baseline_unpriced_tokens=baseline_unpriced,
        current_unpriced_tokens=current_unpriced,
    )


def _row_line(out: str, model: str) -> str:
    return next(line for line in out.splitlines() if model in line)


def test_priced_row_renders_plain_costs_and_signed_delta() -> None:
    line = _row_line(_render([_unpriced_row(
        baseline_cost=1.0, current_cost=3.0, baseline_unpriced=0, current_unpriced=0,
    )]), "claude-opus-4-7")
    assert "$1.00" in line
    assert "$3.00" in line
    assert "+$2.00" in line
    assert "—" not in line
    assert "no price" not in line


def test_fully_unpriced_row_says_no_price_and_has_no_delta() -> None:
    line = _row_line(_render([_unpriced_row(
        model=_UNPRICED, baseline_cost=0.0, current_cost=0.0,
        baseline_unpriced=1000, current_unpriced=1000,
    )]), _UNPRICED)
    assert line.count("no price") == 2
    assert "$0" not in line
    assert "—" in line


def test_partially_unpriced_row_is_marked_and_has_no_delta() -> None:
    # Pin-bump case: baseline partly unpriced, current fully priced. A signed
    # delta would read the pricing gap as a +$2.00 cost increase.
    line = _row_line(_render([_unpriced_row(
        baseline_cost=1.0, current_cost=3.0, baseline_unpriced=400, current_unpriced=0,
    )]), "claude-opus-4-7")
    assert "$1.00 (partial)" in line
    assert "$3.00" in line
    assert "+$2.00" not in line
    assert "—" in line


def test_legacy_row_renders_as_before() -> None:
    line = _row_line(_render([_unpriced_row(
        baseline_cost=0.0, current_cost=2.0, baseline_unpriced=None, current_unpriced=None,
    )]), "claude-opus-4-7")
    assert "$0.00" in line
    assert "+$2.00" in line
    assert "(partial)" not in line
    assert "no price" not in line


def _render_totals(by_model: list[ModelTokenDelta], *, baseline: float, current: float) -> str:
    tokens = sum(row.baseline_total_tokens for row in by_model)
    result = DiffResult(token_metrics=TokenMetricsDelta(
        baseline_total_tokens=tokens,
        current_total_tokens=tokens,
        baseline_total_cost=baseline,
        current_total_cost=current,
        total_cost_delta=current - baseline,
        by_model=by_model,
    ))
    buf = io.StringIO()
    console = Console(
        file=buf, width=200, force_terminal=False, color_system=None, legacy_windows=False,
    )
    _render_token_metrics(console, result)
    return next(line for line in buf.getvalue().splitlines() if "Total cost" in line)


def test_total_cost_row_priced_is_plain() -> None:
    line = _render_totals([_unpriced_row(
        baseline_cost=1.0, current_cost=3.0, baseline_unpriced=0, current_unpriced=0,
    )], baseline=1.0, current=3.0)
    assert "+$2.00" in line
    assert "(partial)" not in line


def test_total_cost_row_partly_unpriced_is_marked_with_no_delta() -> None:
    line = _render_totals([
        _unpriced_row(baseline_cost=1.0, current_cost=3.0, baseline_unpriced=0, current_unpriced=0),
        _unpriced_row(
            model=_UNPRICED, baseline_cost=0.0, current_cost=0.0,
            baseline_unpriced=1000, current_unpriced=0,
        ),
    ], baseline=1.0, current=3.0)
    assert "$1.00 (partial)" in line
    assert "+$2.00" not in line
    assert "—" in line


def test_total_cost_row_fully_unpriced_says_no_price() -> None:
    line = _render_totals([_unpriced_row(
        model=_UNPRICED, baseline_cost=0.0, current_cost=0.0,
        baseline_unpriced=1000, current_unpriced=1000,
    )], baseline=0.0, current=0.0)
    assert line.count("no price") == 2
    assert "—" in line


def test_total_cost_row_legacy_renders_as_before() -> None:
    line = _render_totals([_unpriced_row(
        baseline_cost=0.0, current_cost=2.0, baseline_unpriced=None, current_unpriced=0,
    )], baseline=0.0, current=2.0)
    assert "$0.00" in line
    assert "+$2.00" in line
