"""Tests for per-agent execution metrics."""

from agentfluent.agents.models import AgentInvocation
from agentfluent.analytics.agent_metrics import compute_agent_metrics


def _invocation(
    agent_type: str = "pm",
    is_builtin: bool = False,
    total_tokens: int | None = 10000,
    tool_uses: int | None = 5,
    duration_ms: int | None = 30000,
) -> AgentInvocation:
    return AgentInvocation(
        agent_type=agent_type,
        is_builtin=is_builtin,
        description="test",
        prompt="do something",
        tool_use_id="toolu_01",
        total_tokens=total_tokens,
        tool_uses=tool_uses,
        duration_ms=duration_ms,
    )


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
            _invocation(agent_type="pm", is_builtin=False),
            _invocation(agent_type="Explore", is_builtin=True),
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
            _invocation(agent_type="Explore", is_builtin=True),
            _invocation(agent_type="Plan", is_builtin=True),
        ]
        metrics = compute_agent_metrics(invocations)
        assert metrics.builtin_invocations == 2
        assert metrics.custom_invocations == 0

    def test_all_custom(self) -> None:
        invocations = [
            _invocation(agent_type="pm", is_builtin=False),
            _invocation(agent_type="reviewer", is_builtin=False),
        ]
        metrics = compute_agent_metrics(invocations)
        assert metrics.builtin_invocations == 0
        assert metrics.custom_invocations == 2

    def test_case_insensitive_grouping(self) -> None:
        invocations = [
            _invocation(agent_type="Explore", is_builtin=True),
            _invocation(agent_type="explore", is_builtin=True),
        ]
        metrics = compute_agent_metrics(invocations)
        assert len(metrics.by_agent_type) == 1
        assert metrics.by_agent_type["explore"].invocation_count == 2
