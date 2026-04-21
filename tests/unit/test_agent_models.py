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
        is_builtin=False,
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
            is_builtin=False,
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
            is_builtin=False,
            description="test",
            prompt="test",
            tool_use_id="toolu_01",
            total_tokens=1000,
            tool_uses=0,
            duration_ms=5000,
        )
        assert inv.tokens_per_tool_use is None
        assert inv.duration_per_tool_use is None

    def test_builtin_classification(self) -> None:
        inv = AgentInvocation(
            agent_type="Explore",
            is_builtin=True,
            description="Search code",
            prompt="Find files",
            tool_use_id="toolu_01",
        )
        assert inv.is_builtin is True

    def test_json_round_trip(self) -> None:
        inv = _full_invocation()
        restored = AgentInvocation.model_validate_json(inv.model_dump_json())
        assert restored == inv
