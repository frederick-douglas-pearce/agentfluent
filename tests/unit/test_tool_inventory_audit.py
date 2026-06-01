"""Tests for the tool-inventory audit (#372).

Covers ``audit_tool_inventory``: the declared-count threshold, the
utilization-ratio gate, union of observed tools across an agent type's
invocations, and the two suppression rules (wildcard ``*`` declared
list, and invoked-but-no-``toolStats`` "observed unknown"). The signal
detail surface here is what the correlator's ``ToolInventoryOversizedRule``
relies on.
"""

from __future__ import annotations

from pathlib import Path

from agentfluent.agents.models import AgentInvocation
from agentfluent.config.models import AgentConfig, Scope, Severity
from agentfluent.diagnostics.agent_audit import audit_tool_inventory
from agentfluent.diagnostics.models import SignalType


def _config(name: str, *, tool_count: int, wildcard: bool = False) -> AgentConfig:
    tools = ["*"] if wildcard else [f"Tool{i}" for i in range(tool_count)]
    return AgentConfig(
        name=name,
        file_path=Path(f"/home/u/.claude/agents/{name}.md"),
        scope=Scope.USER,
        tools=tools,
    )


def _invocation(
    agent_type: str,
    *,
    tool_stats: dict[str, int] | None = None,
    suffix: str = "",
) -> AgentInvocation:
    return AgentInvocation(
        tool_use_id=f"toolu_{agent_type}{suffix}",
        agent_type=agent_type,
        description="...",
        prompt="...",
        tool_stats=tool_stats,
    )


def _stats(n: int) -> dict[str, int]:
    """toolStats with ``n`` distinct observed tool names."""
    return {f"Tool{i}": 1 for i in range(n)}


class TestAuditToolInventory:
    def test_oversized_low_utilization_fires(self) -> None:
        # 35 declared, 8 observed unique -> 23% utilization -> fires.
        configs = [_config("researcher", tool_count=35)]
        invocations = [_invocation("researcher", tool_stats=_stats(8))]

        signals = audit_tool_inventory(
            invocations, configs, sessions_analyzed=10,
        )

        assert len(signals) == 1
        sig = signals[0]
        assert sig.signal_type == SignalType.TOOL_INVENTORY_OVERSIZED
        assert sig.severity == Severity.INFO
        assert sig.agent_type == "researcher"
        assert sig.detail["declared_count"] == 35
        assert sig.detail["observed_count"] == 8
        assert sig.detail["utilization_ratio"] == 8 / 35
        assert sig.detail["sessions_analyzed"] == 10
        assert str(sig.detail["source_file"]).endswith("researcher.md")
        assert "35 tools" in sig.message
        assert "8 unique tools" in sig.message
        assert "23% utilization" in sig.message

    def test_oversized_high_utilization_does_not_fire(self) -> None:
        # 35 declared, 25 observed -> 71% utilization -> no signal.
        configs = [_config("researcher", tool_count=35)]
        invocations = [_invocation("researcher", tool_stats=_stats(25))]

        assert (
            audit_tool_inventory(invocations, configs, sessions_analyzed=10)
            == []
        )

    def test_below_count_threshold_does_not_fire(self) -> None:
        # 15 declared (<= 30), 3 observed -> below threshold -> no signal,
        # even though utilization is low.
        configs = [_config("small", tool_count=15)]
        invocations = [_invocation("small", tool_stats=_stats(3))]

        assert (
            audit_tool_inventory(invocations, configs, sessions_analyzed=10)
            == []
        )

    def test_zero_invocations_suppressed(self) -> None:
        # 35 declared but the agent is never invoked in the window: that's
        # the unused-agent audit's domain, not this one.
        configs = [_config("researcher", tool_count=35)]
        invocations = [_invocation("other", tool_stats=_stats(2))]

        assert (
            audit_tool_inventory(invocations, configs, sessions_analyzed=10)
            == []
        )

    def test_empty_invocations_returns_empty(self) -> None:
        configs = [_config("researcher", tool_count=35)]
        assert audit_tool_inventory([], configs, sessions_analyzed=0) == []

    def test_invoked_without_toolstats_suppressed(self) -> None:
        # Invoked, oversized, but no invocation reported toolStats:
        # observed diversity is unknown -> suppress rather than score 0%.
        configs = [_config("researcher", tool_count=35)]
        invocations = [_invocation("researcher", tool_stats=None)]

        assert (
            audit_tool_inventory(invocations, configs, sessions_analyzed=10)
            == []
        )

    def test_wildcard_declared_list_suppressed(self) -> None:
        # `Tools: *` leaves the denominator undefined -> skip.
        configs = [_config("everything", tool_count=0, wildcard=True)]
        invocations = [_invocation("everything", tool_stats=_stats(3))]

        assert (
            audit_tool_inventory(invocations, configs, sessions_analyzed=10)
            == []
        )

    def test_count_threshold_is_strict_greater_than(self) -> None:
        # Exactly 30 declared is NOT oversized (threshold is > 30).
        configs = [_config("boundary", tool_count=30)]
        invocations = [_invocation("boundary", tool_stats=_stats(1))]

        assert (
            audit_tool_inventory(invocations, configs, sessions_analyzed=10)
            == []
        )

    def test_ratio_at_threshold_does_not_fire(self) -> None:
        # Exactly 0.5 utilization (>= threshold) -> no signal.
        configs = [_config("half", tool_count=40)]
        invocations = [_invocation("half", tool_stats=_stats(20))]

        assert (
            audit_tool_inventory(invocations, configs, sessions_analyzed=10)
            == []
        )

    def test_observed_tools_unioned_across_invocations(self) -> None:
        # Two invocations of the same agent type each call disjoint tools;
        # the union determines utilization, not any single invocation.
        configs = [_config("researcher", tool_count=40)]
        invocations = [
            _invocation(
                "researcher",
                tool_stats={"Tool0": 1, "Tool1": 1, "Tool2": 1},
                suffix="a",
            ),
            _invocation(
                "researcher",
                tool_stats={"Tool2": 1, "Tool3": 1, "Tool4": 1},
                suffix="b",
            ),
        ]

        signals = audit_tool_inventory(
            invocations, configs, sessions_analyzed=10,
        )

        assert len(signals) == 1
        # Union of {0,1,2} and {2,3,4} = 5 distinct tools.
        assert signals[0].detail["observed_count"] == 5
        assert signals[0].detail["observed_tools"] == [
            "Tool0", "Tool1", "Tool2", "Tool3", "Tool4",
        ]

    def test_case_insensitive_agent_matching(self) -> None:
        # Frontmatter name casing differs from observed invocation casing.
        configs = [_config("Researcher", tool_count=35)]
        invocations = [_invocation("researcher", tool_stats=_stats(5))]

        signals = audit_tool_inventory(
            invocations, configs, sessions_analyzed=10,
        )
        assert len(signals) == 1
        assert signals[0].agent_type == "Researcher"

    def test_partial_toolstats_still_scores(self) -> None:
        # One invocation has toolStats, another doesn't; the known one
        # supplies observed diversity, so the signal still fires.
        configs = [_config("researcher", tool_count=35)]
        invocations = [
            _invocation("researcher", tool_stats=None, suffix="a"),
            _invocation("researcher", tool_stats=_stats(4), suffix="b"),
        ]

        signals = audit_tool_inventory(
            invocations, configs, sessions_analyzed=10,
        )
        assert len(signals) == 1
        assert signals[0].detail["observed_count"] == 4
