"""Tests for agent invocation models."""

from agentfluent.agents.models import BUILTIN_AGENT_TYPES, AgentInvocation, is_builtin_agent


class TestIsBuiltinAgent:
    def test_known_builtins(self) -> None:
        assert is_builtin_agent("Explore")
        assert is_builtin_agent("explore")
        assert is_builtin_agent("Plan")
        assert is_builtin_agent("general-purpose")

    def test_custom_agents(self) -> None:
        assert not is_builtin_agent("pm")
        assert not is_builtin_agent("my-custom-agent")
        assert not is_builtin_agent("unknown")

    def test_case_insensitive(self) -> None:
        assert is_builtin_agent("EXPLORE")
        assert is_builtin_agent("plan")
        assert is_builtin_agent("General-Purpose")

    def test_builtin_set_is_frozen(self) -> None:
        assert isinstance(BUILTIN_AGENT_TYPES, frozenset)


def _full_invocation() -> AgentInvocation:
    return AgentInvocation(
        agent_type="pm",
        description="Review backlog",
        prompt="Create issues",
        tool_use_id="toolu_01ABC",
        total_tokens=31621,
        tool_uses=14,
        duration_ms=122963,
        agent_id="agent-abc123",
        output_text="Created 5 issues.",
    )


class TestAgentInvocation:
    def test_with_full_metadata(self) -> None:
        inv = _full_invocation()
        assert inv.tokens_per_tool_use == 31621 / 14
        assert inv.duration_per_tool_use == 122963 / 14

    def test_without_metadata(self) -> None:
        inv = AgentInvocation(
            agent_type="pm",
            description="Review backlog",
            prompt="Create issues",
            tool_use_id="toolu_01ABC",
        )
        assert inv.tokens_per_tool_use is None
        assert inv.duration_per_tool_use is None
        assert inv.total_tokens is None
        assert inv.output_text == ""

    def test_zero_tool_uses(self) -> None:
        inv = AgentInvocation(
            agent_type="pm",
            description="test",
            prompt="test",
            tool_use_id="toolu_01",
            total_tokens=1000,
            tool_uses=0,
            duration_ms=5000,
        )
        assert inv.tokens_per_tool_use is None
        assert inv.duration_per_tool_use is None

    def test_builtin_classification_derived_from_agent_type(self) -> None:
        # Property is computed from agent_type via is_builtin_agent,
        # so callers can't pass a stale value — the set is the source
        # of truth.
        explore = AgentInvocation(
            agent_type="Explore", description="d", prompt="p", tool_use_id="t1",
        )
        custom = AgentInvocation(
            agent_type="pm", description="d", prompt="p", tool_use_id="t2",
        )
        assert explore.is_builtin is True
        assert custom.is_builtin is False

    def test_json_round_trip(self) -> None:
        inv = _full_invocation()
        restored = AgentInvocation.model_validate_json(inv.model_dump_json())
        assert restored == inv


class TestActiveDuration:
    """`active_duration_ms` and `active_duration_per_tool_use` (#230).

    Active duration delegates to a linked subagent trace; falls back
    to the raw duration when no trace is linked.
    """

    @staticmethod
    def _trace(active_ms: int | None) -> object:
        from agentfluent.traces.models import SubagentTrace

        return SubagentTrace(
            agent_id="abc",
            agent_type="pm",
            delegation_prompt="x",
            duration_ms=120_000,
            idle_gap_ms=120_000 - active_ms if active_ms is not None else None,
            active_duration_ms=active_ms,
        )

    def test_no_trace_falls_back_to_duration(self) -> None:
        inv = _full_invocation()  # No trace linked
        assert inv.active_duration_ms is None
        assert inv.active_duration_per_tool_use == inv.duration_per_tool_use

    def test_trace_with_active_duration(self) -> None:
        inv = _full_invocation()
        inv.trace = self._trace(active_ms=60_000)  # type: ignore[assignment]
        assert inv.active_duration_ms == 60_000
        assert inv.active_duration_per_tool_use == 60_000 / 14

    def test_trace_without_active_duration_falls_back(self) -> None:
        # Trace exists but couldn't compute active_duration_ms (e.g.,
        # too few paired tool calls). Fall back to raw duration.
        inv = _full_invocation()
        inv.trace = self._trace(active_ms=None)  # type: ignore[assignment]
        assert inv.active_duration_ms is None
        assert inv.active_duration_per_tool_use == inv.duration_per_tool_use

    def test_zero_tool_uses_returns_none(self) -> None:
        inv = AgentInvocation(
            agent_type="pm",
            description="x",
            prompt="x",
            tool_use_id="t",
            tool_uses=0,
            duration_ms=5000,
        )
        inv.trace = self._trace(active_ms=2000)  # type: ignore[assignment]
        assert inv.active_duration_per_tool_use is None
