"""Tests for the TOOL_ORCHESTRATION_CHAIN signal (#406, Tier A)."""

from __future__ import annotations

from pathlib import Path

from agentfluent.agents.models import AgentInvocation
from agentfluent.config.models import AgentConfig, Scope, Severity
from agentfluent.diagnostics.correlator import correlate
from agentfluent.diagnostics.models import SignalType
from agentfluent.diagnostics.tool_orchestration import (
    _LOW_CONFIDENCE_CAVEAT,
    ESTIMATED_TOKEN_SAVINGS_KEY,
    TOKEN_REDUCTION_FACTOR,
    extract_tool_orchestration_signals,
)


def _inv(
    agent_type: str = "researcher",
    total_tokens: int | None = 40_000,
    tool_uses: int | None = 15,
    tool_use_id: str = "tool_1",
) -> AgentInvocation:
    return AgentInvocation(
        agent_type=agent_type,
        description="test",
        prompt="do something",
        tool_use_id=tool_use_id,
        total_tokens=total_tokens,
        tool_uses=tool_uses,
    )


def _matching_invs(count: int, agent_type: str = "researcher") -> list[AgentInvocation]:
    # 15 calls / 40k tokens => 2,667 tokens/call: clears both thresholds.
    return [
        _inv(agent_type=agent_type, tool_use_id=f"tool_{i}") for i in range(count)
    ]


def _config(name: str = "researcher") -> AgentConfig:
    return AgentConfig(
        name=name,
        file_path=Path(f"/home/user/.claude/agents/{name}.md"),
        scope=Scope.USER,
    )


# --- per-invocation predicate boundaries -------------------------------


def test_high_count_high_ratio_fires() -> None:
    """15 tool calls, 40k tokens (2,667/call) across 3 invocations -> signal."""
    signals = extract_tool_orchestration_signals(_matching_invs(3))
    assert len(signals) == 1
    sig = signals[0]
    assert sig.signal_type == SignalType.TOOL_ORCHESTRATION_CHAIN
    assert sig.severity == Severity.INFO
    assert sig.agent_type == "researcher"


def test_low_per_call_overhead_does_not_fire() -> None:
    """15 tool calls but only 10k tokens (667/call) -> below ratio gate."""
    invs = [
        _inv(total_tokens=10_000, tool_uses=15, tool_use_id=f"t_{i}")
        for i in range(3)
    ]
    assert extract_tool_orchestration_signals(invs) == []


def test_below_tool_count_does_not_fire() -> None:
    """5 tool calls, 15k tokens (3,000/call) -> below tool-count gate."""
    invs = [
        _inv(total_tokens=15_000, tool_uses=5, tool_use_id=f"t_{i}")
        for i in range(3)
    ]
    assert extract_tool_orchestration_signals(invs) == []


# --- aggregate (min-invocation) gate -----------------------------------


def test_single_matching_invocation_does_not_fire() -> None:
    """A lone matching invocation is below the 3+ min-invocation gate."""
    assert extract_tool_orchestration_signals(_matching_invs(1)) == []


def test_two_matching_invocations_does_not_fire() -> None:
    """Two matching invocations still below the gate."""
    assert extract_tool_orchestration_signals(_matching_invs(2)) == []


def test_three_matching_invocations_aggregates_evidence() -> None:
    """3+ matching invocations -> one signal with summed evidence."""
    signals = extract_tool_orchestration_signals(_matching_invs(3))
    assert len(signals) == 1
    detail = signals[0].detail
    assert detail["invocation_count"] == 3
    assert detail["total_tool_calls"] == 45  # 3 x 15
    assert detail["total_tokens"] == 120_000  # 3 x 40k
    assert detail["mean_tokens_per_tool_call"] == 120_000 / 45
    assert detail[ESTIMATED_TOKEN_SAVINGS_KEY] == int(120_000 * TOKEN_REDUCTION_FACTOR)


def test_non_matching_invocations_excluded_from_aggregate() -> None:
    """Only matching invocations count toward the gate and the totals."""
    invs = [
        *_matching_invs(3),
        _inv(total_tokens=1_000, tool_uses=2, tool_use_id="small"),
    ]
    signals = extract_tool_orchestration_signals(invs)
    assert len(signals) == 1
    assert signals[0].detail["invocation_count"] == 3


def test_groups_by_agent_type_case_insensitively() -> None:
    """Case variants of the same agent type merge across the gate."""
    invs = [
        _inv(agent_type="Researcher", tool_use_id="a"),
        _inv(agent_type="researcher", tool_use_id="b"),
        _inv(agent_type="RESEARCHER", tool_use_id="c"),
    ]
    signals = extract_tool_orchestration_signals(invs)
    assert len(signals) == 1
    assert signals[0].detail["invocation_count"] == 3


def test_missing_metadata_does_not_match() -> None:
    """Invocations lacking tool_uses/total_tokens can't be classified."""
    invs = [
        _inv(total_tokens=None, tool_use_id="a"),
        _inv(tool_uses=None, tool_use_id="b"),
        _inv(tool_use_id="c"),
    ]
    # Only the third (fully populated) matches -> below the gate.
    assert extract_tool_orchestration_signals(invs) == []


def test_empty_input() -> None:
    assert extract_tool_orchestration_signals([]) == []


# --- correlator --------------------------------------------------------


def test_recommendation_includes_savings_and_citation() -> None:
    signals = extract_tool_orchestration_signals(_matching_invs(3))
    pairs = correlate(signals, {"researcher": _config()})
    assert len(pairs) == 1
    _signal, rec = pairs[0]
    assert rec.target == "tools"
    assert rec.severity == Severity.INFO
    assert "allowed_callers" in rec.action
    assert "code_execution_20250825" in rec.action
    assert "tokens" in rec.action  # estimated savings phrase
    assert "37%" in rec.reason


def test_recommendation_without_config_cites_article_url() -> None:
    signals = extract_tool_orchestration_signals(_matching_invs(3))
    pairs = correlate(signals, None)
    assert len(pairs) == 1
    _signal, rec = pairs[0]
    assert "anthropic.com/engineering/advanced-tool-use" in rec.action
    assert rec.config_file == ""


def test_signal_message_carries_low_confidence_caveat() -> None:
    """Every emitted signal flags itself low-confidence (#407: 0% dogfood precision)."""
    signals = extract_tool_orchestration_signals(_matching_invs(3))
    assert _LOW_CONFIDENCE_CAVEAT in signals[0].message


def test_recommendation_observation_carries_caveat() -> None:
    """The caveat propagates into the recommendation observation, so the fix
    text never asserts the orchestration finding as fact."""
    signals = extract_tool_orchestration_signals(_matching_invs(3))
    pairs = correlate(signals, {"researcher": _config()})
    _signal, rec = pairs[0]
    assert _LOW_CONFIDENCE_CAVEAT in rec.observation


def test_builtin_agent_gets_builtin_action() -> None:
    """A built-in agent type routes to the built-in (non-editable) action."""
    signals = extract_tool_orchestration_signals(
        _matching_invs(3, agent_type="general-purpose"),
    )
    pairs = correlate(signals, None)
    assert len(pairs) == 1
    _signal, rec = pairs[0]
    assert rec.is_builtin is True
    assert "not user-editable" in rec.action
