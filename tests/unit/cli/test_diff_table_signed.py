"""Color-direction tests for ``_signed*`` helpers in diff_table.

Covers #341: cache_efficiency is a higher-is-better metric, so a positive
delta must render green (good) and a negative delta red (bad), opposite
of cost/token deltas.
"""

from __future__ import annotations

from agentfluent.cli.formatters.diff_table import (
    _signed,
    _signed_cost,
    _signed_float,
    _signed_int,
)


class TestLowerIsBetterDefault:
    def test_positive_delta_renders_red(self) -> None:
        assert _signed(5, "5") == "[red]+5[/red]"

    def test_negative_delta_renders_green(self) -> None:
        assert _signed(-5, "5") == "[green]-5[/green]"

    def test_zero_delta_uncolored(self) -> None:
        assert _signed(0, "0") == "0"

    def test_signed_int_inherits_default(self) -> None:
        assert _signed_int(1234) == "[red]+1,234[/red]"
        assert _signed_int(-1234) == "[green]-1,234[/green]"

    def test_signed_cost_inherits_default(self) -> None:
        assert "[red]+" in _signed_cost(2.50)
        assert "[green]-" in _signed_cost(-2.50)


class TestHigherIsBetterInversion:
    def test_positive_delta_renders_green(self) -> None:
        assert _signed(0.3, "0.3", higher_is_better=True) == "[green]+0.3[/green]"

    def test_negative_delta_renders_red(self) -> None:
        assert _signed(-0.3, "0.3", higher_is_better=True) == "[red]-0.3[/red]"

    def test_zero_delta_still_uncolored(self) -> None:
        assert _signed(0, "0", higher_is_better=True) == "0"

    def test_signed_float_with_suffix(self) -> None:
        rendered = _signed_float(
            0.3, precision=1, suffix="%", higher_is_better=True,
        )
        assert rendered == "[green]+0.3%[/green]"

    def test_signed_float_negative_with_suffix(self) -> None:
        rendered = _signed_float(
            -1.5, precision=1, suffix="%", higher_is_better=True,
        )
        assert rendered == "[red]-1.5%[/red]"
