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

from agentfluent.cli.formatters.diff_table import _render_by_model
from agentfluent.diff.models import ModelTokenDelta


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
