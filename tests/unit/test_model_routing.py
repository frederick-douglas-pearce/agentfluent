"""Tests for model-routing diagnostics."""

from __future__ import annotations

from pathlib import Path

from agentfluent.agents.models import AgentInvocation
from agentfluent.config.models import AgentConfig, Scope, Severity
from agentfluent.diagnostics.delegation import (
    MODEL_HAIKU,
    MODEL_OPUS,
    MODEL_SONNET,
)
from agentfluent.diagnostics.model_routing import (
    MODEL_TIER_MAP,
    AgentStats,
    _compute_error_rate,
    _compute_savings,
    aggregate_agent_stats,
    classify_complexity,
    extract_model_routing_signals,
)
from agentfluent.diagnostics.models import SignalType
from agentfluent.traces.models import (
    SubagentToolCall,
    SubagentTrace,
)


def _inv(
    agent_type: str = "pm",
    total_tokens: int | None = 1000,
    tool_uses: int | None = 3,
    output_text: str = "",
    trace: SubagentTrace | None = None,
) -> AgentInvocation:
    return AgentInvocation(
        agent_type=agent_type,
        is_builtin=False,
        description="test",
        prompt="do something",
        tool_use_id=f"tool_{agent_type}",
        total_tokens=total_tokens,
        tool_uses=tool_uses,
        output_text=output_text,
        trace=trace,
    )


def _config(
    name: str = "pm",
    model: str | None = MODEL_SONNET,
) -> AgentConfig:
    return AgentConfig(
        name=name,
        file_path=Path(f"/home/user/.claude/agents/{name}.md"),
        scope=Scope.USER,
        model=model,
    )


def _trace_with_tools_and_errors(
    tools: list[str],
    error_count: int = 0,
) -> SubagentTrace:
    calls = [
        SubagentToolCall(
            tool_name=t,
            input_summary="x",
            result_summary="err" if i < error_count else "ok",
            is_error=i < error_count,
        )
        for i, t in enumerate(tools)
    ]
    return SubagentTrace(
        agent_id="agent-x",
        agent_type="general-purpose",
        delegation_prompt="",
        tool_calls=calls,
        total_errors=error_count,
    )


def _stats(
    agent_type: str = "pm",
    invocation_count: int = 5,
    mean_tool_calls: float = 3.0,
    mean_tokens: float = 1000.0,
    error_rate: float = 0.0,
    has_write_tools: bool = False,
    current_model: str | None = MODEL_SONNET,
) -> AgentStats:
    return AgentStats(
        agent_type=agent_type,
        invocation_count=invocation_count,
        mean_tool_calls=mean_tool_calls,
        mean_tokens=mean_tokens,
        error_rate=error_rate,
        has_write_tools=has_write_tools,
        current_model=current_model,
    )


class TestAggregateAgentStats:
    def test_groups_by_lowercase_agent_type(self) -> None:
        invs = [_inv(agent_type="PM"), _inv(agent_type="pm")]
        result = aggregate_agent_stats(invs, configs={"pm": _config()})
        assert "pm" in result
        assert result["pm"].invocation_count == 2

    def test_null_tokens_tool_uses_handled(self) -> None:
        invs = [_inv(total_tokens=None, tool_uses=None)]
        result = aggregate_agent_stats(invs, configs=None)
        assert result["pm"].mean_tokens == 0.0
        assert result["pm"].mean_tool_calls == 0.0

    def test_error_rate_from_trace_when_linked(self) -> None:
        trace = _trace_with_tools_and_errors(["Bash", "Bash"], error_count=1)
        invs = [_inv(trace=trace)]
        result = aggregate_agent_stats(invs, configs=None)
        assert result["pm"].error_rate == 0.5

    def test_error_rate_from_output_text_fallback(self) -> None:
        invs = [_inv(tool_uses=4, output_text="operation failed")]
        result = aggregate_agent_stats(invs, configs=None)
        # ERROR_REGEX matches "failed" once → 1/4 = 0.25.
        assert result["pm"].error_rate > 0

    def test_current_model_from_config(self) -> None:
        invs = [_inv(agent_type="pm")]
        configs = {"pm": _config(model=MODEL_HAIKU)}
        result = aggregate_agent_stats(invs, configs)
        assert result["pm"].current_model == MODEL_HAIKU

    def test_current_model_none_when_no_config(self) -> None:
        invs = [_inv(agent_type="pm")]
        result = aggregate_agent_stats(invs, configs=None)
        assert result["pm"].current_model is None


class TestClassifyComplexity:
    def test_simple_case(self) -> None:
        assert classify_complexity(
            _stats(mean_tool_calls=2.0, mean_tokens=500.0),
        ) == "simple"

    def test_complex_by_write_tools(self) -> None:
        assert classify_complexity(_stats(has_write_tools=True)) == "complex"

    def test_complex_by_tool_count(self) -> None:
        assert classify_complexity(_stats(mean_tool_calls=15.0)) == "complex"

    def test_complex_by_tokens(self) -> None:
        assert classify_complexity(_stats(mean_tokens=6000.0)) == "complex"

    def test_complex_by_error_rate(self) -> None:
        assert classify_complexity(_stats(error_rate=0.3)) == "complex"

    def test_moderate_fallback(self) -> None:
        # Above simple thresholds, below all complex thresholds.
        assert classify_complexity(
            _stats(mean_tool_calls=6.0, mean_tokens=3000.0),
        ) == "moderate"


class TestDetectMismatch:
    def test_overspec_sonnet_for_simple_task(self) -> None:
        invs = [_inv(agent_type="pm", tool_uses=2, total_tokens=500) for _ in range(5)]
        configs = {"pm": _config(model=MODEL_SONNET)}
        signals = extract_model_routing_signals(invs, configs)
        assert len(signals) == 1
        assert signals[0].signal_type == SignalType.MODEL_MISMATCH
        assert signals[0].detail["mismatch_type"] == "overspec"
        assert signals[0].detail["recommended_model"] == MODEL_HAIKU

    def test_overspec_opus_for_simple_task(self) -> None:
        invs = [_inv(agent_type="pm", tool_uses=2, total_tokens=500) for _ in range(5)]
        configs = {"pm": _config(model=MODEL_OPUS)}
        signals = extract_model_routing_signals(invs, configs)
        assert len(signals) == 1
        assert signals[0].detail["mismatch_type"] == "overspec"

    def test_underspec_requires_both_complex_and_high_error(self) -> None:
        # Complex (high tool calls) + high error → underspec emitted.
        trace = _trace_with_tools_and_errors(["Read"] * 5, error_count=3)
        invs = [
            _inv(agent_type="pm", tool_uses=12, total_tokens=1000, trace=trace)
            for _ in range(5)
        ]
        configs = {"pm": _config(model=MODEL_HAIKU)}
        signals = extract_model_routing_signals(invs, configs)
        assert len(signals) == 1
        assert signals[0].detail["mismatch_type"] == "underspec"
        assert signals[0].detail["recommended_model"] == MODEL_SONNET

    def test_underspec_skipped_without_high_error_rate(self) -> None:
        # Complex but error rate low — quietly accepted.
        invs = [_inv(agent_type="pm", tool_uses=12) for _ in range(5)]
        configs = {"pm": _config(model=MODEL_HAIKU)}
        signals = extract_model_routing_signals(invs, configs)
        assert signals == []

    def test_no_mismatch_matching_tier(self) -> None:
        invs = [_inv(agent_type="pm", tool_uses=2, total_tokens=500) for _ in range(5)]
        configs = {"pm": _config(model=MODEL_HAIKU)}
        signals = extract_model_routing_signals(invs, configs)
        assert signals == []

    def test_skips_low_invocation_count(self) -> None:
        invs = [_inv(agent_type="pm", tool_uses=2, total_tokens=500) for _ in range(2)]
        configs = {"pm": _config(model=MODEL_SONNET)}
        signals = extract_model_routing_signals(invs, configs)
        assert signals == []

    def test_skips_when_no_declared_model(self) -> None:
        invs = [_inv(agent_type="pm", tool_uses=2, total_tokens=500) for _ in range(5)]
        configs = {"pm": _config(model=None)}
        signals = extract_model_routing_signals(invs, configs)
        assert signals == []

    def test_skips_when_no_config(self) -> None:
        invs = [_inv(agent_type="pm", tool_uses=2, total_tokens=500) for _ in range(5)]
        signals = extract_model_routing_signals(invs, configs=None)
        assert signals == []


class TestCostSavings:
    def test_both_pricings_available_savings_computed(self) -> None:
        stats = _stats(
            mean_tokens=1000.0, invocation_count=10,
            current_model=MODEL_OPUS,
        )
        savings, current_cost = _compute_savings(stats, MODEL_HAIKU)
        assert savings is not None and savings > 0
        assert current_cost is not None and current_cost > 0

    def test_unknown_current_model_returns_none(self) -> None:
        stats = _stats(current_model="claude-mystery-model-9")
        savings, current_cost = _compute_savings(stats, MODEL_HAIKU)
        assert savings is None
        assert current_cost is None

    def test_unknown_alt_model_returns_none(self) -> None:
        stats = _stats(current_model=MODEL_SONNET)
        savings, current_cost = _compute_savings(stats, "claude-mystery-model-9")
        assert savings is None
        assert current_cost is None

    def test_none_current_model_returns_none(self) -> None:
        stats = _stats(current_model=None)
        savings, current_cost = _compute_savings(stats, MODEL_HAIKU)
        assert savings is None
        assert current_cost is None


class TestExtractModelRoutingSignals:
    def test_empty_invocations(self) -> None:
        assert extract_model_routing_signals([], configs=None) == []

    def test_mixed_agents_emit_per_type(self) -> None:
        pm_invs = [_inv(agent_type="pm", tool_uses=2, total_tokens=500) for _ in range(5)]
        architect_invs = [
            _inv(agent_type="architect", tool_uses=2, total_tokens=500)
            for _ in range(5)
        ]
        configs = {
            "pm": _config(name="pm", model=MODEL_OPUS),
            "architect": _config(name="architect", model=MODEL_HAIKU),
        }
        signals = extract_model_routing_signals(pm_invs + architect_invs, configs)
        # pm is overspec'd (Opus on simple); architect matches.
        types_by_agent = {s.agent_type: s.detail["mismatch_type"] for s in signals}
        assert types_by_agent == {"pm": "overspec"}


class TestSignalDetailContract:
    """Contract test for #113: MODEL_MISMATCH signals must carry every
    field the composite-merge step will pivot on. Breaking any of these
    keys is a breaking change for the cross-epic merge."""

    def test_signal_carries_all_required_detail_keys(self) -> None:
        invs = [_inv(agent_type="pm", tool_uses=2, total_tokens=500) for _ in range(5)]
        configs = {"pm": _config(model=MODEL_OPUS)}
        signals = extract_model_routing_signals(invs, configs)
        assert len(signals) == 1
        detail = signals[0].detail
        required = {
            "mismatch_type",
            "current_model",
            "recommended_model",
            "complexity_tier",
            "invocation_count",
            "mean_tool_calls",
            "mean_tokens",
            "error_rate",
            "estimated_savings_usd",
            "current_cost_usd",
        }
        assert required.issubset(set(detail.keys()))


class TestHelpers:
    def test_error_rate_zero_for_invocation_without_data(self) -> None:
        inv = _inv(tool_uses=0, output_text="")
        assert _compute_error_rate(inv) == 0.0

    def test_model_tier_map_covers_all_canonical_models(self) -> None:
        assert MODEL_TIER_MAP[MODEL_HAIKU] == "simple"
        assert MODEL_TIER_MAP[MODEL_SONNET] == "moderate"
        assert MODEL_TIER_MAP[MODEL_OPUS] == "complex"

    def test_severity_is_warning(self) -> None:
        invs = [_inv(agent_type="pm", tool_uses=2, total_tokens=500) for _ in range(5)]
        configs = {"pm": _config(model=MODEL_OPUS)}
        signals = extract_model_routing_signals(invs, configs)
        assert signals[0].severity == Severity.WARNING
