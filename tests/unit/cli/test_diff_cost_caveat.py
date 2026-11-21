"""Cost caveat slot in ``diff`` table output (#543)."""

from __future__ import annotations

import io

from rich.console import Console

from agentfluent.analytics.cost_model import CostModelStamp
from agentfluent.cli.formatters.diff_table import (
    _cost_caveat_lines,
    _render_token_metrics,
)
from agentfluent.diff.models import DiffResult


def _stamp(pin: str | None = "0.2.3", method: int | None = 1) -> CostModelStamp:
    return CostModelStamp(genai_prices_version=pin, cost_method_version=method)


def _result(
    baseline: CostModelStamp | None, current: CostModelStamp | None,
) -> DiffResult:
    return DiffResult(baseline_cost_model=baseline, current_cost_model=current)


class TestCostCaveatLines:
    def test_silent_on_match(self) -> None:
        assert _cost_caveat_lines(_result(_stamp(), _stamp())) == []

    def test_pricing_table_mismatch_names_both_pins(self) -> None:
        lines = _cost_caveat_lines(_result(_stamp(pin="0.1.4"), _stamp()))
        assert len(lines) == 2
        assert "[yellow]" in lines[0]
        assert "genai-prices 0.1.4 → 0.2.3" in lines[1]
        assert "cost method" not in lines[1]
        assert "not agent behavior" in lines[1]

    def test_cost_method_mismatch_names_method_only(self) -> None:
        lines = _cost_caveat_lines(_result(_stamp(method=1), _stamp(method=2)))
        assert "cost method 1 → 2" in lines[1]
        assert "genai-prices" not in lines[1]

    def test_both_components_named_when_both_change(self) -> None:
        lines = _cost_caveat_lines(
            _result(_stamp(pin="0.1.4", method=1), _stamp(method=2)),
        )
        assert "genai-prices 0.1.4 → 0.2.3; cost method 1 → 2" in lines[1]

    def test_unknown_baseline_names_the_side(self) -> None:
        lines = _cost_caveat_lines(_result(None, _stamp()))
        assert "cost model unknown" in lines[1]
        assert "baseline snapshot has no readable cost-model stamp" in lines[1]

    def test_unknown_current_names_the_side(self) -> None:
        lines = _cost_caveat_lines(_result(_stamp(), None))
        assert "current snapshot has no readable cost-model stamp" in lines[1]

    def test_both_unstamped(self) -> None:
        lines = _cost_caveat_lines(_result(None, None))
        assert "neither snapshot has a readable cost-model stamp" in lines[1]

    def test_incomplete_stamp(self) -> None:
        lines = _cost_caveat_lines(_result(_stamp(pin=None), _stamp()))
        assert "stamp is incomplete" in lines[1]


class TestRenderedPlacement:
    def test_caveat_printed_above_token_table(self) -> None:
        buf = io.StringIO()
        console = Console(file=buf, width=200, force_terminal=False, no_color=True)
        _render_token_metrics(console, _result(_stamp(method=1), _stamp(method=2)))
        out = buf.getvalue()
        assert out.index("Cost deltas below may not reflect agent behavior") < out.index(
            "Token Metrics",
        )

    def test_no_caveat_on_match(self) -> None:
        buf = io.StringIO()
        console = Console(file=buf, width=200, force_terminal=False, no_color=True)
        _render_token_metrics(console, _result(_stamp(), _stamp()))
        assert "Cost deltas below" not in buf.getvalue()


class TestMarkupSafety:
    def test_envelope_version_strings_are_escaped(self) -> None:
        """The pin comes from a user-supplied envelope file; Rich markup in it
        must render literally, not as styling."""
        buf = io.StringIO()
        console = Console(file=buf, width=300, force_terminal=False, no_color=True)
        _render_token_metrics(
            console, _result(_stamp(pin="[bold]0.1.4[/bold]"), _stamp()),
        )
        assert "genai-prices [bold]0.1.4[/bold] → 0.2.3" in buf.getvalue()
