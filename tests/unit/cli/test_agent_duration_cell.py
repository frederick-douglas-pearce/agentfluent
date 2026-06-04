"""Tests for the shared agent-duration cell formatter (#480).

``format_agent_duration_cell`` renders an agent type's summary-table
duration as active (idle-subtracted) wall-clock per call, so an
interactive agent like ``pm`` -- whose raw wall-clock includes user-wait
time -- does not read as a duration problem. Both the analyze table and
the Markdown report share it.
"""

from agentfluent.cli.formatters.helpers import (
    DURATION_RATIO_HIGHLIGHT,
    format_agent_duration_cell,
)


def _cell(**kwargs: int):
    base = {
        "total_duration_ms": 0,
        "invocation_count": 0,
        "total_active_duration_ms": 0,
        "total_wallclock_ms_trace_linked": 0,
        "active_duration_invocation_count": 0,
    }
    base.update(kwargs)
    return format_agent_duration_cell(**base)


class TestNoData:
    def test_zero_duration_renders_dash(self) -> None:
        cell = _cell(total_duration_ms=0, invocation_count=4)
        assert cell.text == "-"
        assert not cell.highlight
        assert not cell.unreliable
        assert not cell.partial

    def test_zero_invocations_renders_dash(self) -> None:
        cell = _cell(total_duration_ms=1000, invocation_count=0)
        assert cell.text == "-"


class TestWallOnlyFallback:
    def test_no_trace_falls_back_to_wall_per_call_marked_unreliable(self) -> None:
        # 60s wall over 4 invocations, no trace anywhere -> ~15.0s* avg.
        cell = _cell(total_duration_ms=60000, invocation_count=4)
        assert cell.text == "~15.0s*"
        assert cell.unreliable
        assert not cell.highlight
        assert not cell.partial


class TestCombinedCell:
    def test_divergent_renders_active_then_wall(self) -> None:
        # 2.0s active / 15.0s wall per call over 4 trace-linked calls.
        cell = _cell(
            total_duration_ms=60000,
            invocation_count=4,
            total_active_duration_ms=8000,
            total_wallclock_ms_trace_linked=60000,
            active_duration_invocation_count=4,
        )
        assert cell.text == "2.0s (15.0s wall)"
        assert cell.highlight  # 7.5x >= 3x
        assert not cell.unreliable
        assert not cell.partial

    def test_near_equal_renders_bare_active_no_highlight(self) -> None:
        # Active ~= wall (no meaningful idle) -> single figure, no flag.
        cell = _cell(
            total_duration_ms=40000,
            invocation_count=4,
            total_active_duration_ms=40000,
            total_wallclock_ms_trace_linked=40000,
            active_duration_invocation_count=4,
        )
        assert cell.text == "10.0s"
        assert not cell.highlight

    def test_ratio_just_below_threshold_not_highlighted(self) -> None:
        # 2.5x divergence -> combined cell, but below the 3x highlight.
        cell = _cell(
            total_duration_ms=25000,
            invocation_count=2,
            total_active_duration_ms=10000,
            total_wallclock_ms_trace_linked=25000,
            active_duration_invocation_count=2,
        )
        assert cell.text == "5.0s (12.5s wall)"
        assert not cell.highlight

    def test_ratio_at_threshold_is_highlighted(self) -> None:
        # Exactly 3x -> highlighted (boundary is inclusive).
        cell = _cell(
            total_duration_ms=30000,
            invocation_count=2,
            total_active_duration_ms=10000,
            total_wallclock_ms_trace_linked=30000,
            active_duration_invocation_count=2,
        )
        ratio = 30000 / 10000
        assert ratio == DURATION_RATIO_HIGHLIGHT
        assert cell.highlight


class TestPartialCoverage:
    def test_partial_coverage_appends_dagger_and_flag(self) -> None:
        # 4 invocations, only 3 trace-linked.
        cell = _cell(
            total_duration_ms=80000,
            invocation_count=4,
            total_active_duration_ms=6000,
            total_wallclock_ms_trace_linked=45000,
            active_duration_invocation_count=3,
        )
        assert cell.partial
        assert cell.text.endswith("†")
        # 2.0s active / 15.0s wall over the 3 linked calls.
        assert "2.0s (15.0s wall)†" == cell.text

    def test_full_coverage_no_dagger(self) -> None:
        cell = _cell(
            total_duration_ms=60000,
            invocation_count=4,
            total_active_duration_ms=8000,
            total_wallclock_ms_trace_linked=60000,
            active_duration_invocation_count=4,
        )
        assert not cell.partial
        assert "†" not in cell.text
