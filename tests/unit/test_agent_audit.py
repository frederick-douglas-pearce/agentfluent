"""Tests for the unused-agent audit (#346).

Covers ``audit_unused_agents``: configured-vs-observed comparison,
built-in exclusion (D033), empty-window suppression, case-insensitive
matching, and the signal detail surface that the correlator's
``UnusedAgentRule`` relies on.
"""

from __future__ import annotations

from pathlib import Path

from agentfluent.agents.models import AgentInvocation
from agentfluent.config.models import AgentConfig, Scope, Severity
from agentfluent.diagnostics.agent_audit import audit_unused_agents
from agentfluent.diagnostics.models import SignalType


def _config(name: str, *, description: str = "do a thing") -> AgentConfig:
    return AgentConfig(
        name=name,
        file_path=Path(f"/home/u/.claude/agents/{name}.md"),
        scope=Scope.USER,
        description=description,
    )


def _invocation(agent_type: str) -> AgentInvocation:
    return AgentInvocation(
        tool_use_id=f"toolu_{agent_type}",
        agent_type=agent_type,
        description="...",
        prompt="...",
    )


class TestAuditUnusedAgents:
    def test_flags_custom_agent_with_zero_invocations(self) -> None:
        configs = [_config("tester", description="Fix pytest failures")]
        invocations = [_invocation("pm")]

        signals = audit_unused_agents(
            invocations, configs, sessions_analyzed=10,
        )

        assert len(signals) == 1
        sig = signals[0]
        assert sig.signal_type == SignalType.UNUSED_AGENT
        assert sig.severity == Severity.INFO
        assert sig.agent_type == "tester"
        assert "tester" in sig.message
        assert "10 analyzed sessions" in sig.message
        assert sig.detail["agent_name"] == "tester"
        assert sig.detail["description"] == "Fix pytest failures"
        assert sig.detail["sessions_analyzed"] == 10
        # file_path is an absolute Path; source_file should be its string form.
        assert sig.detail["source_file"].endswith("tester.md")

    def test_invoked_agent_does_not_fire(self) -> None:
        configs = [_config("pm"), _config("architect")]
        invocations = [_invocation("pm"), _invocation("architect")]

        assert (
            audit_unused_agents(invocations, configs, sessions_analyzed=5)
            == []
        )

    def test_builtin_agent_excluded_per_d033(self) -> None:
        # `general-purpose` is in BUILTIN_AGENT_TYPES; even if a config
        # exists and is never invoked, no signal fires.
        configs = [_config("general-purpose"), _config("tester")]
        invocations = [_invocation("pm")]

        signals = audit_unused_agents(
            invocations, configs, sessions_analyzed=5,
        )
        agent_names = {s.agent_type for s in signals}
        assert agent_names == {"tester"}

    def test_case_insensitive_matching(self) -> None:
        # Config name casing differs from observed invocation casing —
        # the audit must lowercase-compare so frontmatter "PM" doesn't
        # falsely flag against an observed "pm" invocation.
        configs = [_config("PM")]
        invocations = [_invocation("pm")]

        assert (
            audit_unused_agents(invocations, configs, sessions_analyzed=3)
            == []
        )

    def test_empty_invocations_returns_empty(self) -> None:
        configs = [_config("tester"), _config("pm")]

        assert audit_unused_agents([], configs, sessions_analyzed=0) == []

    def test_empty_configs_returns_empty(self) -> None:
        invocations = [_invocation("pm")]
        assert (
            audit_unused_agents(invocations, [], sessions_analyzed=5)
            == []
        )

    def test_multiple_unused_agents_each_get_a_signal(self) -> None:
        configs = [
            _config("tester"),
            _config("reviewer"),
            _config("pm"),
        ]
        invocations = [_invocation("pm")]

        signals = audit_unused_agents(
            invocations, configs, sessions_analyzed=5,
        )
        agent_names = {s.agent_type for s in signals}
        assert agent_names == {"tester", "reviewer"}

    def test_missing_description_does_not_crash(self) -> None:
        # AgentConfig.description defaults to ""; verify the signal
        # still emits cleanly (the correlator handles the empty case
        # by omitting the "Current description: ..." clause).
        configs = [_config("tester", description="")]
        invocations = [_invocation("pm")]

        signals = audit_unused_agents(
            invocations, configs, sessions_analyzed=1,
        )
        assert len(signals) == 1
        assert signals[0].detail["description"] == ""
