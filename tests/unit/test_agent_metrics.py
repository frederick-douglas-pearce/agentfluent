"""Tests for per-agent execution metrics."""

from datetime import UTC, datetime
from typing import Any

import pytest

from agentfluent.agents.models import AgentInvocation
from agentfluent.analytics import agent_metrics as agent_metrics_module
from agentfluent.analytics.agent_metrics import compute_agent_metrics
from agentfluent.analytics.pricing import SYNTHETIC_MODELS
from agentfluent.core.session import Usage
from agentfluent.traces.models import SubagentTrace

HAIKU = "claude-haiku-4-5-20251001"  # $1 in / $5 out / $0.10 cache-read per MTok
OPUS = "claude-opus-4-6"  # $5 in / $25 out / $0.50 cache-read per MTok
UNPRICED = "claude-nonexistent-9"  # get_pricing -> None


def _traced(
    agent_type: str = "pm",
    model: str | None = HAIKU,
    *,
    input_tokens: int = 100_000,
    output_tokens: int = 20_000,
    cache_read: int = 1_000_000,
    model_turns: int = 5,
    total_tokens: int | None = None,
) -> AgentInvocation:
    """An invocation with a linked trace carrying real per-turn usage (#646).

    With the defaults: haiku -> $0.30, opus -> $1.50; spend 1,120,000 tokens.
    ``total_tokens`` is the rollup proxy and defaults to absent, as on most
    current invocations (D062(4)).
    """
    inv = _invocation(agent_type=agent_type, total_tokens=total_tokens)
    inv.trace = SubagentTrace(
        agent_id="a",
        delegation_prompt="x",
        model=model,
        model_turns=model_turns,
        usage=Usage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_input_tokens=cache_read,
        ),
    )
    return inv


def _invocation(
    agent_type: str = "pm",
    total_tokens: int | None = 10000,
    tool_uses: int | None = 5,
    duration_ms: int | None = 30000,
    model_turns: int | None = None,
) -> AgentInvocation:
    inv = AgentInvocation(
        agent_type=agent_type,
        description="test",
        prompt="do something",
        tool_use_id="toolu_01",
        total_tokens=total_tokens,
        tool_uses=tool_uses,
        duration_ms=duration_ms,
    )
    # ``model_turns`` is derived from a linked trace; attach a minimal
    # one carrying the requested count. ``None`` leaves the invocation
    # trace-less (the honest-gap case from #466).
    if model_turns is not None:
        inv.trace = SubagentTrace(
            agent_id="a", delegation_prompt="x", model_turns=model_turns,
        )
    return inv


class TestComputeAgentMetrics:
    def test_single_invocation(self) -> None:
        invocations = [_invocation()]
        metrics = compute_agent_metrics(invocations)
        assert metrics.total_invocations == 1
        assert metrics.total_agent_tokens == 10000
        assert len(metrics.by_agent_type) == 1
        pm = metrics.by_agent_type["pm"]
        assert pm.invocation_count == 1
        assert pm.total_tokens == 10000
        assert pm.total_tool_uses == 5
        assert pm.total_duration_ms == 30000

    def test_multiple_same_type(self) -> None:
        invocations = [
            _invocation(total_tokens=10000, tool_uses=5, duration_ms=30000),
            _invocation(total_tokens=20000, tool_uses=10, duration_ms=60000),
        ]
        metrics = compute_agent_metrics(invocations)
        assert metrics.total_invocations == 2
        pm = metrics.by_agent_type["pm"]
        assert pm.invocation_count == 2
        assert pm.total_tokens == 30000
        assert pm.total_tool_uses == 15

    def test_multiple_types(self) -> None:
        invocations = [
            _invocation(agent_type="pm"),
            _invocation(agent_type="Explore"),
        ]
        metrics = compute_agent_metrics(invocations)
        assert len(metrics.by_agent_type) == 2
        assert metrics.builtin_invocations == 1
        assert metrics.custom_invocations == 1

    def test_empty_invocations(self) -> None:
        metrics = compute_agent_metrics([])
        assert metrics.total_invocations == 0
        assert metrics.total_agent_tokens == 0
        assert metrics.by_agent_type == {}

    def test_missing_metadata_counted(self) -> None:
        invocations = [
            _invocation(total_tokens=None, tool_uses=None, duration_ms=None),
        ]
        metrics = compute_agent_metrics(invocations)
        assert metrics.total_invocations == 1
        pm = metrics.by_agent_type["pm"]
        assert pm.invocation_count == 1
        assert pm.total_tokens == 0
        assert pm.avg_tokens_per_tool_use is None

    def test_mixed_metadata_presence(self) -> None:
        invocations = [
            _invocation(total_tokens=10000, tool_uses=5, duration_ms=30000),
            _invocation(total_tokens=None, tool_uses=None, duration_ms=None),
        ]
        metrics = compute_agent_metrics(invocations)
        pm = metrics.by_agent_type["pm"]
        assert pm.invocation_count == 2
        assert pm.total_tokens == 10000
        assert pm.total_tool_uses == 5


class TestAverages:
    def test_tokens_per_tool_use(self) -> None:
        invocations = [_invocation(total_tokens=10000, tool_uses=5)]
        metrics = compute_agent_metrics(invocations)
        pm = metrics.by_agent_type["pm"]
        assert pm.avg_tokens_per_tool_use == 2000.0

    def test_duration_per_tool_use(self) -> None:
        invocations = [_invocation(duration_ms=30000, tool_uses=5)]
        metrics = compute_agent_metrics(invocations)
        pm = metrics.by_agent_type["pm"]
        assert pm.avg_duration_per_tool_use == 6000.0

    def test_avg_tokens_per_invocation(self) -> None:
        invocations = [
            _invocation(total_tokens=10000),
            _invocation(total_tokens=20000),
        ]
        metrics = compute_agent_metrics(invocations)
        pm = metrics.by_agent_type["pm"]
        assert pm.avg_tokens_per_invocation == 15000.0

    def test_avg_duration_per_invocation(self) -> None:
        invocations = [
            _invocation(duration_ms=30000),
            _invocation(duration_ms=60000),
        ]
        metrics = compute_agent_metrics(invocations)
        pm = metrics.by_agent_type["pm"]
        assert pm.avg_duration_per_invocation == 45000.0

    def test_zero_tool_uses_no_averages(self) -> None:
        invocations = [_invocation(total_tokens=10000, tool_uses=0)]
        metrics = compute_agent_metrics(invocations)
        pm = metrics.by_agent_type["pm"]
        assert pm.avg_tokens_per_tool_use is None


def _trace_linked_invocation(
    agent_type: str = "pm",
    duration_ms: int = 60000,
    idle_gap_ms: int = 56000,
    tool_uses: int = 5,
) -> AgentInvocation:
    """Invocation whose linked trace yields an active duration
    (``duration_ms - idle_gap_ms``). ``inv.active_duration_ms`` reads
    the trace; ``inv.duration_ms`` is the parent-side wall-clock summed
    into ``total_wallclock_ms_trace_linked``."""
    inv = AgentInvocation(
        agent_type=agent_type,
        description="test",
        prompt="do something",
        tool_use_id="toolu_active",
        tool_uses=tool_uses,
        duration_ms=duration_ms,
    )
    inv.trace = SubagentTrace(
        agent_id="a",
        delegation_prompt="x",
        duration_ms=duration_ms,
        idle_gap_ms=idle_gap_ms,
    )
    return inv


class TestActiveDuration:
    """#480: active-duration aggregates on AgentTypeMetrics."""

    def test_trace_linked_invocations_accumulate_active_totals(self) -> None:
        # Two trace-linked pm invocations: 4s active / 60s wall each.
        invocations = [
            _trace_linked_invocation(duration_ms=60000, idle_gap_ms=56000),
            _trace_linked_invocation(duration_ms=60000, idle_gap_ms=56000),
        ]
        pm = compute_agent_metrics(invocations).by_agent_type["pm"]
        assert pm.total_active_duration_ms == 8000
        assert pm.total_wallclock_ms_trace_linked == 120000
        assert pm.active_duration_invocation_count == 2

    def test_traceless_invocations_excluded_from_active_totals(self) -> None:
        # One trace-linked, one trace-less: only the linked one counts
        # toward active aggregates, but both count toward total_duration.
        invocations = [
            _trace_linked_invocation(duration_ms=60000, idle_gap_ms=56000),
            _invocation(duration_ms=30000),  # no trace
        ]
        pm = compute_agent_metrics(invocations).by_agent_type["pm"]
        assert pm.active_duration_invocation_count == 1
        assert pm.total_active_duration_ms == 4000
        assert pm.total_wallclock_ms_trace_linked == 60000
        # total_duration_ms spans all invocations (the misleading number).
        assert pm.total_duration_ms == 90000

    def test_active_per_invocation_properties(self) -> None:
        invocations = [
            _trace_linked_invocation(duration_ms=60000, idle_gap_ms=56000),
            _trace_linked_invocation(duration_ms=60000, idle_gap_ms=56000),
        ]
        pm = compute_agent_metrics(invocations).by_agent_type["pm"]
        assert pm.avg_active_duration_per_invocation == 4000.0
        assert pm.avg_wallclock_per_trace_linked_invocation == 60000.0
        assert pm.wallclock_active_ratio == 15.0

    def test_ratio_none_without_active_data(self) -> None:
        pm = compute_agent_metrics([_invocation(duration_ms=30000)]).by_agent_type["pm"]
        assert pm.active_duration_invocation_count == 0
        assert pm.avg_active_duration_per_invocation is None
        assert pm.wallclock_active_ratio is None

    def test_active_fields_serialize_into_json(self) -> None:
        # Stored fields (not properties) so they survive model_dump into
        # the JSON envelope (AC#2).
        import dataclasses

        pm = compute_agent_metrics(
            [_trace_linked_invocation()],
        ).by_agent_type["pm"]
        dumped = dataclasses.asdict(pm)
        assert dumped["total_active_duration_ms"] == 4000
        assert dumped["total_wallclock_ms_trace_linked"] == 60000
        assert dumped["active_duration_invocation_count"] == 1


class TestAgentTokenPercentage:
    """#646 AC5: the numerator is trace spend, never the rollup proxy."""

    def test_percentage_from_trace_spend(self) -> None:
        invocations = [_traced(total_tokens=5_000)]
        metrics = compute_agent_metrics(invocations, session_total_tokens=2_240_000)
        assert metrics.total_agent_spend_tokens == 1_120_000
        assert metrics.total_agent_tokens == 5_000  # proxy unchanged
        assert metrics.agent_token_percentage == 50.0

    def test_rollup_without_trace_contributes_nothing(self) -> None:
        invocations = [_invocation(total_tokens=30000)]
        metrics = compute_agent_metrics(invocations, session_total_tokens=100000)
        assert metrics.total_agent_tokens == 30000
        assert metrics.total_agent_spend_tokens == 0
        assert metrics.agent_token_percentage == 0.0

    def test_zero_session_tokens(self) -> None:
        metrics = compute_agent_metrics([_traced()], session_total_tokens=0)
        assert metrics.agent_token_percentage == 0.0

    def test_no_agent_tokens(self) -> None:
        invocations = [_invocation(total_tokens=None)]
        metrics = compute_agent_metrics(invocations, session_total_tokens=100000)
        assert metrics.agent_token_percentage == 0.0


class TestBuiltinVsCustom:
    def test_all_builtin(self) -> None:
        invocations = [
            _invocation(agent_type="Explore"),
            _invocation(agent_type="Plan"),
        ]
        metrics = compute_agent_metrics(invocations)
        assert metrics.builtin_invocations == 2
        assert metrics.custom_invocations == 0

    def test_all_custom(self) -> None:
        invocations = [
            _invocation(agent_type="pm"),
            _invocation(agent_type="reviewer"),
        ]
        metrics = compute_agent_metrics(invocations)
        assert metrics.builtin_invocations == 0
        assert metrics.custom_invocations == 2

    def test_case_insensitive_grouping(self) -> None:
        invocations = [
            _invocation(agent_type="Explore"),
            _invocation(agent_type="explore"),
        ]
        metrics = compute_agent_metrics(invocations)
        assert len(metrics.by_agent_type) == 1
        assert metrics.by_agent_type["explore"].invocation_count == 2


class TestPerAgentCost:
    """#646: cost prices each linked trace at its own model's rate (no blended rate)."""

    def test_trace_missing_adds_no_spend_and_no_cost(self) -> None:
        invocations = [_invocation(total_tokens=10000)]
        pm = compute_agent_metrics(invocations, session_total_tokens=10000).by_agent_type["pm"]
        assert pm.estimated_total_cost_usd == 0.0
        assert pm.spend_tokens == 0
        assert pm.spend_invocation_count == 0
        assert pm.priced_invocation_count == 0
        assert pm.estimated_avg_cost_per_invocation_usd is None
        assert pm.avg_spend_tokens_per_invocation is None

    def test_each_agent_priced_at_its_trace_model_rate(self) -> None:
        # The blended-rate defect: a haiku agent in an opus-heavy session was
        # priced at the session's average rate. Now each trace prices itself.
        invocations = [
            _traced(agent_type="summarizer", model=HAIKU),
            _traced(agent_type="architect", model=OPUS),
        ]
        metrics = compute_agent_metrics(invocations, session_total_tokens=10_000_000)
        assert metrics.by_agent_type["summarizer"].estimated_total_cost_usd == pytest.approx(0.30)
        assert metrics.by_agent_type["architect"].estimated_total_cost_usd == pytest.approx(1.50)

    def test_timestamp_forwarded_to_price_trace(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        seen: list[Any] = []

        def fake_price_trace(trace: SubagentTrace, *, timestamp: Any = None) -> float:
            seen.append(timestamp)
            return 1.0

        monkeypatch.setattr(agent_metrics_module, "price_trace", fake_price_trace)
        when = datetime(2026, 1, 2, tzinfo=UTC)
        compute_agent_metrics([_traced()], timestamp=when)
        assert seen == [when]

    def test_cost_averages_divide_by_priced_coverage_only(self) -> None:
        # Two priced haiku traces ($0.30 each), one unpriced-model trace, one
        # trace-missing invocation: the unpriced trace counts as spend (tokens)
        # but must not dilute the cost averages (#646 AC6).
        invocations = [
            _traced(model=HAIKU, model_turns=5),
            _traced(model=HAIKU, model_turns=5),
            _traced(model=UNPRICED, model_turns=10),
            _invocation(total_tokens=10000),
        ]
        pm = compute_agent_metrics(invocations).by_agent_type["pm"]
        assert pm.invocation_count == 4
        assert pm.spend_invocation_count == 3
        assert pm.priced_invocation_count == 2
        assert pm.priced_model_turns == 10
        assert pm.spend_tokens == 3 * 1_120_000
        assert pm.estimated_total_cost_usd == pytest.approx(0.60)
        assert pm.estimated_avg_cost_per_invocation_usd == pytest.approx(0.30)
        assert pm.estimated_avg_cost_per_turn_usd == pytest.approx(0.06)
        assert pm.avg_spend_tokens_per_invocation == pytest.approx(1_120_000)

    @pytest.mark.parametrize("model", [None, next(iter(SYNTHETIC_MODELS))])
    def test_non_spend_trace_excluded(self, model: str | None) -> None:
        pm = compute_agent_metrics([_traced(model=model)]).by_agent_type["pm"]
        assert pm.spend_tokens == 0
        assert pm.spend_invocation_count == 0
        assert pm.estimated_total_cost_usd == 0.0

    def test_total_tokens_unchanged_by_trace(self) -> None:
        # AC3: the rollup proxy stays byte-identical when a trace is linked.
        pm = compute_agent_metrics([_traced(total_tokens=78_006)]).by_agent_type["pm"]
        assert pm.total_tokens == 78_006
        assert pm.spend_tokens == 1_120_000

    def test_no_blended_rate_remains(self) -> None:
        import inspect

        source = inspect.getsource(agent_metrics_module)
        assert "blended_rate" not in source
        assert "get_pricing" not in source
        assert "compute_cost" not in source


class TestModelTurnRollup:
    """#467: per-agent-type turn totals and derived efficiency ratios."""

    def test_total_and_avg_skips_trace_missing(self) -> None:
        # turns [4, 6, None]: the trace-missing invocation is counted
        # toward invocation_count but excluded from the turn average.
        invocations = [
            _invocation(model_turns=4),
            _invocation(model_turns=6),
            _invocation(model_turns=None),
        ]
        pm = compute_agent_metrics(invocations).by_agent_type["pm"]
        assert pm.invocation_count == 3
        assert pm.total_model_turns == 10
        assert pm.invocations_with_turns == 2
        assert pm.avg_turns_per_invocation == 5.0

    def test_all_turns_missing_yields_none(self) -> None:
        invocations = [_invocation(model_turns=None), _invocation(model_turns=None)]
        pm = compute_agent_metrics(invocations).by_agent_type["pm"]
        assert pm.total_model_turns == 0
        assert pm.invocations_with_turns == 0
        assert pm.avg_turns_per_invocation is None
        assert pm.avg_tool_calls_per_turn is None
        assert pm.avg_tokens_per_turn is None
        assert pm.estimated_avg_cost_per_turn_usd is None

    def test_tool_calls_per_turn(self) -> None:
        # 20 tool calls across 5 turns -> 4.0 calls/turn.
        invocations = [_invocation(tool_uses=20, model_turns=5)]
        pm = compute_agent_metrics(invocations).by_agent_type["pm"]
        assert pm.avg_tool_calls_per_turn == 4.0

    def test_tokens_and_cost_per_turn(self) -> None:
        # Spend 1,120,000 tokens / 5 turns -> 224,000 tokens/turn; haiku trace
        # $0.30 / 5 turns -> $0.06/turn. Both over the trace (#646 AC6).
        invocations = [_traced(model_turns=5, total_tokens=10000)]
        pm = compute_agent_metrics(invocations).by_agent_type["pm"]
        assert pm.avg_tokens_per_turn == 224_000.0
        assert pm.estimated_avg_cost_per_turn_usd == pytest.approx(0.06)

    def test_zero_turn_trace_guards_per_turn_ratios(self) -> None:
        # Trace exists but has 0 assistant messages: invocations_with_turns
        # counts it (turn data IS present), avg_turns_per_invocation is a
        # legitimate 0.0, but per-turn ratios are None (no division by 0).
        invocations = [_invocation(total_tokens=10000, tool_uses=5, model_turns=0)]
        pm = compute_agent_metrics(invocations).by_agent_type["pm"]
        assert pm.invocations_with_turns == 1
        assert pm.total_model_turns == 0
        assert pm.avg_turns_per_invocation == 0.0
        assert pm.avg_tool_calls_per_turn is None
        assert pm.avg_tokens_per_turn is None
        assert pm.estimated_avg_cost_per_turn_usd is None

    def test_aggregate_total_across_types(self) -> None:
        invocations = [
            _invocation(agent_type="pm", model_turns=4),
            _invocation(agent_type="pm", model_turns=6),
            _invocation(agent_type="Explore", model_turns=3),
        ]
        metrics = compute_agent_metrics(invocations)
        assert metrics.total_model_turns == 13
        assert metrics.by_agent_type["pm"].total_model_turns == 10
        assert metrics.by_agent_type["explore"].total_model_turns == 3
