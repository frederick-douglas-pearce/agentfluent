"""Tests for per-agent execution metrics."""

import pytest

from agentfluent.agents.models import AgentInvocation
from agentfluent.analytics.agent_metrics import compute_agent_metrics
from agentfluent.traces.models import SubagentTrace


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
    def test_percentage_of_session(self) -> None:
        invocations = [_invocation(total_tokens=30000)]
        metrics = compute_agent_metrics(invocations, session_total_tokens=100000)
        assert metrics.agent_token_percentage == 30.0

    def test_zero_session_tokens(self) -> None:
        invocations = [_invocation(total_tokens=30000)]
        metrics = compute_agent_metrics(invocations, session_total_tokens=0)
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
    """Cost is estimated via session-level blended per-token rate (#200)."""

    def test_zero_cost_when_session_cost_zero(self) -> None:
        invocations = [_invocation(total_tokens=10000)]
        metrics = compute_agent_metrics(invocations, session_total_tokens=10000)
        pm = metrics.by_agent_type["pm"]
        assert pm.estimated_total_cost_usd == 0.0
        assert pm.estimated_avg_cost_per_invocation_usd is None

    def test_blended_rate_proportional(self) -> None:
        # Session: 100k tokens, $1.00 → blended rate $0.00001/token.
        # Agent gets 30k tokens → $0.30; 1 invocation → avg $0.30/inv.
        invocations = [_invocation(total_tokens=30000)]
        metrics = compute_agent_metrics(
            invocations,
            session_total_tokens=100000,
            session_total_cost=1.0,
        )
        pm = metrics.by_agent_type["pm"]
        assert pm.estimated_total_cost_usd == pytest.approx(0.30)
        assert pm.estimated_avg_cost_per_invocation_usd == pytest.approx(0.30)

    def test_avg_cost_per_invocation(self) -> None:
        # Two invocations, total 50k tokens of session's 100k @ $1.00.
        invocations = [
            _invocation(total_tokens=20000),
            _invocation(total_tokens=30000),
        ]
        metrics = compute_agent_metrics(
            invocations,
            session_total_tokens=100000,
            session_total_cost=1.0,
        )
        pm = metrics.by_agent_type["pm"]
        assert pm.estimated_total_cost_usd == pytest.approx(0.50)
        assert pm.estimated_avg_cost_per_invocation_usd == pytest.approx(0.25)

    def test_zero_tokens_no_cost(self) -> None:
        invocations = [_invocation(total_tokens=None)]
        metrics = compute_agent_metrics(
            invocations,
            session_total_tokens=100000,
            session_total_cost=1.0,
        )
        pm = metrics.by_agent_type["pm"]
        assert pm.estimated_total_cost_usd == 0.0
        assert pm.estimated_avg_cost_per_invocation_usd is None

    def test_proportional_split_across_agent_types(self) -> None:
        # pm has 30k of 100k tokens, Explore has 70k → cost split 30/70.
        invocations = [
            _invocation(agent_type="pm", total_tokens=30000),
            _invocation(agent_type="Explore", total_tokens=70000),
        ]
        metrics = compute_agent_metrics(
            invocations,
            session_total_tokens=100000,
            session_total_cost=1.0,
        )
        pm = metrics.by_agent_type["pm"]
        explore = metrics.by_agent_type["explore"]
        assert pm.estimated_total_cost_usd == pytest.approx(0.30)
        assert explore.estimated_total_cost_usd == pytest.approx(0.70)
        # Cost preserves the input/output dollar total.
        assert pm.estimated_total_cost_usd + explore.estimated_total_cost_usd == pytest.approx(1.0)


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
        # 10k tokens / 5 turns -> 2000 tokens/turn. Session 10k @ $1.00
        # blended -> agent cost $1.00 / 5 turns -> $0.20/turn.
        invocations = [_invocation(total_tokens=10000, tool_uses=5, model_turns=5)]
        pm = compute_agent_metrics(
            invocations, session_total_tokens=10000, session_total_cost=1.0,
        ).by_agent_type["pm"]
        assert pm.avg_tokens_per_turn == 2000.0
        assert pm.estimated_avg_cost_per_turn_usd == pytest.approx(0.20)

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
