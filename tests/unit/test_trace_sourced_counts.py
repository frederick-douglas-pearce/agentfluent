"""Trace-sourced tool-use count and duration (#690).

A trace-linked invocation takes ``tool_uses`` and ``duration_ms`` from its trace,
set together at attach time; an invocation with no trace keeps the parent
``toolUseResult`` rollup. Aggregate ratios divide coverage-matched pairs, and the
complexity tier needs token evidence before it calls a group "simple".
"""

from __future__ import annotations

from pathlib import Path

from rich.console import Console

from agentfluent.agents.models import AgentInvocation
from agentfluent.analytics.agent_metrics import AgentMetrics, compute_agent_metrics
from agentfluent.analytics.pipeline import AnalysisResult, SessionAnalysis, _merge_agent_metrics
from agentfluent.analytics.tokens import TokenMetrics
from agentfluent.analytics.tools import ToolMetrics
from agentfluent.cli.formatters.helpers import format_agent_duration_cell
from agentfluent.cli.formatters.table import format_analysis_table
from agentfluent.diagnostics._complexity import aggregate_cluster_stats
from agentfluent.diagnostics.model_routing import aggregate_agent_stats, classify_complexity
from agentfluent.traces.linker import link_traces
from agentfluent.traces.models import SubagentToolCall, SubagentTrace

_COVERAGE_FIELDS = (
    "total_tool_uses",
    "invocations_with_turns",
    "total_duration_ms",
    "total_tool_uses_with_turns",
    "total_tokens_with_tool_uses",
    "total_tool_uses_with_tokens",
    "total_duration_ms_with_tool_uses",
    "total_tool_uses_with_duration",
    "total_active_duration_ms",
    "total_wallclock_ms_trace_linked",
    "active_duration_invocation_count",
    "total_model_turns",
)
_RATIO_FIELDS = (
    "avg_tool_calls_per_turn",
    "avg_tokens_per_tool_use",
    "avg_duration_per_tool_use",
    "avg_turns_per_invocation",
)


def _calls(n: int) -> list[SubagentToolCall]:
    return [
        SubagentToolCall(tool_name="Read", input_summary="", result_summary="")
        for _ in range(n)
    ]


def _trace(
    agent_id: str,
    *,
    tool_calls: int,
    duration_ms: int | None,
    idle_gap_ms: int | None = None,
    model_turns: int = 4,
) -> SubagentTrace:
    return SubagentTrace(
        agent_id=agent_id,
        delegation_prompt="x",
        tool_calls=_calls(tool_calls),
        duration_ms=duration_ms,
        idle_gap_ms=idle_gap_ms,
        model_turns=model_turns,
    )


def _invocation(
    agent_id: str,
    *,
    agent_type: str = "pm",
    total_tokens: int | None = None,
    tool_uses: int | None = None,
    duration_ms: int | None = None,
) -> AgentInvocation:
    return AgentInvocation(
        agent_type=agent_type,
        description=agent_id,
        prompt="run",
        tool_use_id=f"toolu_{agent_id}",
        agent_id=agent_id,
        total_tokens=total_tokens,
        tool_uses=tool_uses,
        duration_ms=duration_ms,
    )


def _link(
    invocations: list[AgentInvocation], traces: list[SubagentTrace],
) -> list[AgentInvocation]:
    """Attach traces through the production linker."""
    by_id = {t.agent_id: t for t in traces}
    return link_traces(invocations, by_id.get)


def _rollupless_linked() -> list[AgentInvocation]:
    """Two trace-linked invocations with no rollup: 6 + 2 calls over 4 + 4 turns."""
    return _link(
        [_invocation("a"), _invocation("b")],
        [
            _trace("a", tool_calls=6, duration_ms=60_000, idle_gap_ms=30_000),
            _trace("b", tool_calls=2, duration_ms=20_000, idle_gap_ms=None),
        ],
    )


def _mixed() -> list[AgentInvocation]:
    """One trace-linked invocation that also carries a rollup, plus one
    rollup-only invocation with no trace."""
    linked = _link(
        [_invocation("a", total_tokens=4_000, tool_uses=9, duration_ms=99_000)],
        [_trace("a", tool_calls=4, duration_ms=40_000, idle_gap_ms=10_000)],
    )
    rollup_only = _invocation("z", total_tokens=6_000, tool_uses=3, duration_ms=30_000)
    return [*linked, rollup_only]


class TestAttachTrace:
    """AC1: one source per invocation, set at attach time."""

    def test_trace_overwrites_rollup_count_and_duration_together(self) -> None:
        [inv] = _link(
            [_invocation("a", tool_uses=9, duration_ms=99_000)],
            [_trace("a", tool_calls=4, duration_ms=40_000)],
        )
        assert inv.tool_uses == 4
        assert inv.duration_ms == 40_000

    def test_rollupless_invocation_gains_trace_values(self) -> None:
        [inv] = _link([_invocation("a")], [_trace("a", tool_calls=0, duration_ms=5_000)])
        assert inv.tool_uses == 0
        assert inv.duration_ms == 5_000

    def test_trace_without_duration_never_falls_back_to_rollup(self) -> None:
        [inv] = _link(
            [_invocation("a", tool_uses=9, duration_ms=99_000)],
            [_trace("a", tool_calls=4, duration_ms=None)],
        )
        assert inv.tool_uses == 4
        assert inv.duration_ms is None

    def test_unlinked_invocation_keeps_rollup(self) -> None:
        """AC6: no trace -> today's values and the unreliable marker."""
        [inv] = _link([_invocation("a", tool_uses=9, duration_ms=99_000)], [])
        assert inv.trace is None
        assert inv.tool_uses == 9
        assert inv.duration_ms == 99_000
        assert inv.duration_reliable is False


class TestCoverageMatchedRatios:
    def test_avg_tool_calls_per_turn_on_rollupless_traces(self) -> None:
        """AC2: Σ trace tool calls / Σ model_turns, and > 0."""
        pm = compute_agent_metrics(_rollupless_linked()).by_agent_type["pm"]
        assert pm.total_tool_uses_with_turns == 8
        assert pm.total_model_turns == 8
        assert pm.avg_tool_calls_per_turn == 1.0

    def test_avg_tool_calls_per_turn_excludes_traceless_tool_counts(self) -> None:
        """AC2: a rollup-only invocation has tool uses but no turns, so its
        count must not reach the per-turn numerator."""
        pm = compute_agent_metrics(_mixed()).by_agent_type["pm"]
        assert pm.total_tool_uses == 7
        assert pm.total_tool_uses_with_turns == 4
        assert pm.avg_tool_calls_per_turn == 4 / 4

    def test_avg_tokens_per_tool_use_only_over_invocations_with_both(self) -> None:
        """AC4: rollup-less trace-linked tool calls must not dilute a
        rollup-only token numerator."""
        # A partial rollup: tokens without a tool count. It must reach the
        # all-invocation token sum but not the per-tool-use numerator.
        tokens_only = _invocation("t", total_tokens=5_000)
        invocations = [*_rollupless_linked(), *_mixed(), tokens_only]
        pm = compute_agent_metrics(invocations).by_agent_type["pm"]
        assert pm.total_tool_uses == 15
        assert pm.total_tokens == 15_000
        assert pm.total_tokens_with_tool_uses == 10_000
        assert pm.total_tool_uses_with_tokens == 7
        assert pm.avg_tokens_per_tool_use == 10_000 / 7

    def test_avg_duration_per_tool_use_only_over_invocations_with_both(self) -> None:
        invocations = [
            *_link(
                [_invocation("a"), _invocation("b")],
                [
                    _trace("a", tool_calls=4, duration_ms=40_000),
                    _trace("b", tool_calls=6, duration_ms=None),
                ],
            ),
            # A partial rollup: duration without a tool count.
            _invocation("d", duration_ms=50_000),
        ]
        pm = compute_agent_metrics(invocations).by_agent_type["pm"]
        assert pm.total_duration_ms == 90_000
        assert pm.total_tool_uses == 10
        assert pm.total_tool_uses_with_duration == 4
        assert pm.total_duration_ms_with_tool_uses == 40_000
        assert pm.avg_duration_per_tool_use == 10_000


class TestActiveDurationCoverage:
    """AC3 and the <2-paired-calls fallback."""

    def test_rollupless_traces_contribute_active_and_wall_from_the_trace(self) -> None:
        pm = compute_agent_metrics(_rollupless_linked()).by_agent_type["pm"]
        assert pm.active_duration_invocation_count == 2
        assert pm.total_wallclock_ms_trace_linked == 80_000
        # a: 60s - 30s idle; b: idle unknown -> its trace span stands in.
        assert pm.total_active_duration_ms == 30_000 + 20_000

    def test_wall_is_trace_span_not_rollup(self) -> None:
        pm = compute_agent_metrics(_mixed()).by_agent_type["pm"]
        assert pm.active_duration_invocation_count == 1
        assert pm.total_wallclock_ms_trace_linked == 40_000
        assert pm.total_active_duration_ms == 30_000

    def test_trace_without_duration_is_not_covered(self) -> None:
        invocations = _link([_invocation("a")], [_trace("a", tool_calls=3, duration_ms=None)])
        pm = compute_agent_metrics(invocations).by_agent_type["pm"]
        assert pm.active_duration_invocation_count == 0


class TestMergeEquivalence:
    """merge(compute(A), compute(B)) == compute(A + B) for every field the
    merge has to carry (#690 I3)."""

    @staticmethod
    def _uneven() -> list[AgentInvocation]:
        """Every merged count/sum field takes a distinct value here
        (``test_uneven_fixture_separates_the_pairs`` pins it), so a merge that
        adds one field into another cannot match the single compute."""
        linked = _link(
            [_invocation("u", total_tokens=2_000), _invocation("v")],
            [
                _trace("u", tool_calls=5, duration_ms=None),
                _trace("v", tool_calls=1, duration_ms=None, model_turns=6),
            ],
        )
        partial_rollups = [
            _invocation("w", duration_ms=50_000),
            _invocation("t", total_tokens=1_000),
        ]
        return [*_mixed(), *linked, *partial_rollups]

    def test_uneven_fixture_separates_the_pairs(self) -> None:
        pm = compute_agent_metrics(self._uneven()).by_agent_type["pm"]
        assert pm.total_tool_uses_with_tokens == 12
        assert pm.total_tool_uses_with_duration == 7
        assert pm.total_tokens_with_tool_uses == 12_000
        assert pm.total_duration_ms_with_tool_uses == 70_000
        values = [getattr(pm, name) for name in (*_COVERAGE_FIELDS, "total_tokens")]
        assert len(set(values)) == len(values), dict(
            zip((*_COVERAGE_FIELDS, "total_tokens"), values, strict=True)
        )

    def test_merge_matches_single_compute(self) -> None:
        part_a = _rollupless_linked()
        part_b = self._uneven()
        for parts in ([part_a, part_b], [part_b, part_a], [part_b, part_b]):
            merged = _merge_agent_metrics(
                [compute_agent_metrics(p) for p in parts],
            ).by_agent_type["pm"]
            whole = compute_agent_metrics([i for p in parts for i in p]).by_agent_type["pm"]
            for name in (*_COVERAGE_FIELDS, *_RATIO_FIELDS):
                assert getattr(merged, name) == getattr(whole, name), name

    def test_first_seen_branch_carries_new_fields(self) -> None:
        single = compute_agent_metrics(_mixed())
        merged = _merge_agent_metrics([single]).by_agent_type["pm"]
        for name in (*_COVERAGE_FIELDS, *_RATIO_FIELDS):
            assert getattr(merged, name) == getattr(single.by_agent_type["pm"], name), name


class TestComplexityNeedsTokenEvidence:
    """B1: trace-sourced tool calls without rollup tokens must not read as
    "simple" just because ``mean_tokens`` defaults to 0.0."""

    def test_agent_type_with_tools_but_no_tokens_is_moderate(self) -> None:
        invocations = _link([_invocation("a")], [_trace("a", tool_calls=2, duration_ms=1_000)])
        stats = aggregate_agent_stats(invocations, None)["pm"]
        assert stats.mean_tool_calls == 2
        assert stats.mean_tokens == 0.0
        assert classify_complexity(stats) == "moderate"

    def test_cluster_with_tools_but_no_tokens_is_moderate(self) -> None:
        invocations = _link([_invocation("a")], [_trace("a", tool_calls=2, duration_ms=1_000)])
        stats = aggregate_cluster_stats(invocations)
        assert stats.mean_tool_calls == 2
        assert stats.mean_tokens == 0.0
        assert classify_complexity(stats) == "moderate"

    def test_light_work_with_token_evidence_is_still_simple(self) -> None:
        invocations = _link(
            [_invocation("a", total_tokens=1_000)],
            [_trace("a", tool_calls=2, duration_ms=1_000)],
        )
        stats = aggregate_agent_stats(invocations, None)["pm"]
        assert classify_complexity(stats) == "simple"


def _result(invocations: list[AgentInvocation]) -> AnalysisResult:
    am: AgentMetrics = compute_agent_metrics(invocations)
    session = SessionAnalysis(
        session_path=Path("session-1.jsonl"),
        token_metrics=TokenMetrics(),
        tool_metrics=ToolMetrics(),
        agent_metrics=am,
        invocations=invocations,
    )
    return AnalysisResult(sessions=[session], agent_metrics=am)


def _render(invocations: list[AgentInvocation]) -> str:
    console = Console(record=True, width=200, force_terminal=False)
    format_analysis_table(console, _result(invocations), verbose=True)
    return console.export_text()


def _duration_cell(invocations: list[AgentInvocation]) -> tuple[str, bool]:
    pm = compute_agent_metrics(invocations).by_agent_type["pm"]
    cell = format_agent_duration_cell(
        total_duration_ms=pm.total_duration_ms,
        invocation_count=pm.invocation_count,
        total_active_duration_ms=pm.total_active_duration_ms,
        total_wallclock_ms_trace_linked=pm.total_wallclock_ms_trace_linked,
        active_duration_invocation_count=pm.active_duration_invocation_count,
    )
    return cell.text, cell.partial


class TestCliRendering:
    """AC5 / AC6."""

    def test_rollupless_traces_render_duration_without_dagger(self) -> None:
        text, partial = _duration_cell(_rollupless_linked())
        assert text != "-"
        assert partial is False
        assert "†" not in text

    def test_verbose_table_renders_trace_tool_uses_and_duration(self) -> None:
        output = _render(_rollupless_linked())
        row_a = next(line for line in output.splitlines() if " a " in f" {line} " and "pm" in line)
        assert " 6 " in row_a
        assert "30.0s (60.0s wall)" in row_a
        row_b = next(line for line in output.splitlines() if " b " in f" {line} " and "pm" in line)
        assert " 2 " in row_b
        assert "20.0s" in row_b
        assert "*" not in row_a + row_b

    def test_zero_tool_calls_render_as_zero(self) -> None:
        invocations = _link([_invocation("a")], [_trace("a", tool_calls=0, duration_ms=5_000)])
        row = next(
            line for line in _render(invocations).splitlines() if "5.0s" in line and " a " in line
        )
        assert " 0 " in row

    def test_dagger_only_when_an_invocation_lacks_a_trace(self) -> None:
        _, partial_all_linked = _duration_cell(_rollupless_linked())
        _, partial_mixed = _duration_cell(_mixed())
        assert partial_all_linked is False
        assert partial_mixed is True

    def test_dagger_when_a_linked_trace_has_no_measurable_span(self) -> None:
        """AC5 as amended: "†" marks invocations without a measurable trace
        span, which includes a linked trace whose span is unknown."""
        invocations = _link(
            [_invocation("a"), _invocation("b")],
            [
                _trace("a", tool_calls=2, duration_ms=10_000),
                _trace("b", tool_calls=1, duration_ms=None),
            ],
        )
        text, partial = _duration_cell(invocations)
        assert partial is True
        assert text.endswith("†")
        assert "measurable trace span" in _render(invocations)

    def test_traceless_invocation_keeps_unreliable_marker(self) -> None:
        output = _render([_invocation("z", total_tokens=6_000, tool_uses=3, duration_ms=30_000)])
        assert "~30.0s*" in output
