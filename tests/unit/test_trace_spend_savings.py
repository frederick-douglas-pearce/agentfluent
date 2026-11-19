"""Dollar savings sourced from trace spend, not the ``total_tokens`` proxy (#675)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentfluent.agents.models import AgentInvocation
from agentfluent.analytics.agent_metrics import AgentMetrics
from agentfluent.analytics.pipeline import SessionAnalysis
from agentfluent.analytics.pricing import (
    MODEL_HAIKU,
    MODEL_OPUS,
    MODEL_SONNET,
    compute_cost,
    get_pricing,
)
from agentfluent.analytics.tokens import (
    TokenMetrics,
    price_trace,
    price_usage,
    trace_spend,
)
from agentfluent.analytics.tools import ToolMetrics
from agentfluent.config.models import AgentConfig, Scope, Severity
from agentfluent.core.session import SessionMessage, Usage
from agentfluent.diagnostics._complexity import (
    AgentStats,
    aggregate_cluster_stats,
    classify_complexity,
    simple_branch_inputs,
)
from agentfluent.diagnostics.correlator import correlate as _correlate_pairs
from agentfluent.diagnostics.model_routing import (
    PRICED_INVOCATION_COUNT_KEY,
    SAVINGS_USD_KEY,
    aggregate_agent_stats,
    estimate_model_savings,
    estimate_usage_savings,
    extract_model_routing_signals,
    extract_sdk_main_session_signals,
    savings_scope_phrase,
)
from agentfluent.diagnostics.models import (
    DiagnosticRecommendation,
    DiagnosticSignal,
    SignalType,
)
from agentfluent.diagnostics.pipeline import _append_mismatch_phrase
from agentfluent.diagnostics.signals import _extract_duration_outliers
from agentfluent.traces.models import SubagentTrace

# Cache-heavy, output-light: the shape a 50/50 split of a context-size proxy
# misprices most.
_SPEND = Usage(
    input_tokens=1_000,
    output_tokens=2_000,
    cache_creation_input_tokens=30_000,
    cache_read_input_tokens=400_000,
    cache_creation_5m_input_tokens=20_000,
    cache_creation_1h_input_tokens=10_000,
)


def correlate(
    signals: list[DiagnosticSignal], configs: dict[str, AgentConfig] | None = None,
) -> list[DiagnosticRecommendation]:
    return [rec for _, rec in _correlate_pairs(signals, configs, None)]


def _manual_cost(usage: Usage, model: str) -> float:
    pricing = get_pricing(model)
    assert pricing is not None
    return compute_cost(
        pricing,
        usage.input_tokens,
        usage.output_tokens,
        usage.cache_creation_5m_input_tokens,
        usage.cache_read_input_tokens,
        cache_creation_1h_tokens=usage.cache_creation_1h_input_tokens,
    )


def _trace(model: str | None, usage: Usage = _SPEND, agent_id: str = "a1") -> SubagentTrace:
    return SubagentTrace(
        agent_id=agent_id,
        agent_type="pm",
        delegation_prompt="",
        model=model,
        usage=usage,
    )


def _inv(
    *,
    trace: SubagentTrace | None = None,
    total_tokens: int | None = 500,
    tool_uses: int | None = 2,
    agent_type: str = "pm",
    n: int = 0,
) -> AgentInvocation:
    return AgentInvocation(
        agent_type=agent_type,
        description="d",
        prompt="p",
        tool_use_id=f"toolu_{n}",
        total_tokens=total_tokens,
        tool_uses=tool_uses,
        trace=trace,
    )


def _config(model: str) -> AgentConfig:
    return AgentConfig(
        name="pm",
        file_path=Path("/home/user/.claude/agents/pm.md"),
        scope=Scope.USER,
        model=model,
    )


class TestPriceUsage:
    def test_prices_every_usage_component_including_cache_split(self) -> None:
        assert price_usage(_SPEND, MODEL_OPUS, timestamp=None) == pytest.approx(
            _manual_cost(_SPEND, MODEL_OPUS),
        )

    def test_unpriced_model_is_none(self) -> None:
        assert price_usage(_SPEND, "claude-mystery-model-9", timestamp=None) is None

    def test_price_trace_is_price_usage_at_the_trace_model(self) -> None:
        trace = _trace(MODEL_SONNET)
        assert price_trace(trace) == pytest.approx(
            price_usage(_SPEND, MODEL_SONNET, timestamp=None),
        )

    def test_trace_spend_is_none_without_a_spend_trace(self) -> None:
        assert trace_spend(None) is None
        assert trace_spend(_trace(None)) is None
        assert trace_spend(_trace("<synthetic>")) is None
        assert trace_spend(_trace(MODEL_OPUS)) == (_SPEND, MODEL_OPUS)


class TestEstimateModelSavings:
    def test_linked_invocations_priced_at_their_own_trace_models(self) -> None:
        small = Usage(input_tokens=100, output_tokens=50)
        invs = [
            _inv(trace=_trace(MODEL_OPUS), n=0),
            _inv(trace=_trace(MODEL_SONNET, usage=small, agent_id="a2"), n=1),
        ]
        estimate = estimate_model_savings(invs, MODEL_HAIKU)
        assert estimate is not None
        current = _manual_cost(_SPEND, MODEL_OPUS) + _manual_cost(small, MODEL_SONNET)
        alt = _manual_cost(_SPEND, MODEL_HAIKU) + _manual_cost(small, MODEL_HAIKU)
        assert estimate.current_cost_usd == pytest.approx(current)
        assert estimate.savings_usd == pytest.approx(current - alt)
        assert estimate.priced_invocation_count == 2

    def test_not_the_old_fifty_fifty_proxy_figure(self) -> None:
        inv = _inv(trace=_trace(MODEL_OPUS), total_tokens=_SPEND.total_tokens)
        estimate = estimate_model_savings([inv], MODEL_HAIKU)
        assert estimate is not None
        half = _SPEND.total_tokens // 2
        opus, haiku = get_pricing(MODEL_OPUS), get_pricing(MODEL_HAIKU)
        assert opus is not None and haiku is not None
        proxy = compute_cost(opus, half, half) - compute_cost(haiku, half, half)
        assert estimate.savings_usd != pytest.approx(proxy)

    def test_unlinked_invocations_yield_no_dollar_figure(self) -> None:
        invs = [_inv(total_tokens=500_000, n=i) for i in range(5)]
        assert estimate_model_savings(invs, MODEL_HAIKU) is None

    def test_mixed_population_prices_only_the_covered_subset(self) -> None:
        invs = [_inv(trace=_trace(MODEL_OPUS), n=0)] + [
            _inv(total_tokens=500_000, n=i) for i in range(1, 4)
        ]
        estimate = estimate_model_savings(invs, MODEL_HAIKU)
        assert estimate is not None
        assert estimate.priced_invocation_count == 1
        assert estimate.current_cost_usd == pytest.approx(_manual_cost(_SPEND, MODEL_OPUS))

    def test_unpriced_trace_model_contributes_nothing(self) -> None:
        invs = [_inv(trace=_trace("claude-mystery-model-9"))]
        assert estimate_model_savings(invs, MODEL_HAIKU) is None

    def test_unpriced_alternative_yields_none(self) -> None:
        invs = [_inv(trace=_trace(MODEL_OPUS))]
        assert estimate_model_savings(invs, "claude-mystery-model-9") is None

    def test_savings_never_negative(self) -> None:
        estimate = estimate_usage_savings([(_SPEND, MODEL_HAIKU)], MODEL_OPUS)
        assert estimate is not None
        assert estimate.savings_usd == 0.0


def _simple_group(*, linked: int, total: int) -> list[AgentInvocation]:
    return [
        _inv(trace=_trace(MODEL_OPUS, agent_id=f"a{i}") if i < linked else None, n=i)
        for i in range(total)
    ]


class TestMismatchSignalSavings:
    def test_detail_carries_trace_priced_savings_and_coverage(self) -> None:
        signals = extract_model_routing_signals(
            _simple_group(linked=2, total=5), {"pm": _config(MODEL_OPUS)},
        )
        assert len(signals) == 1
        detail = signals[0].detail
        assert detail[PRICED_INVOCATION_COUNT_KEY] == 2
        assert detail["invocation_count"] == 5
        expected = 2 * (_manual_cost(_SPEND, MODEL_OPUS) - _manual_cost(_SPEND, MODEL_HAIKU))
        assert detail[SAVINGS_USD_KEY] == pytest.approx(expected)

    def test_unlinked_group_still_fires_without_a_dollar_figure(self) -> None:
        signals = extract_model_routing_signals(
            _simple_group(linked=0, total=5), {"pm": _config(MODEL_OPUS)},
        )
        assert len(signals) == 1
        assert signals[0].detail[SAVINGS_USD_KEY] is None
        assert signals[0].detail["current_cost_usd"] is None
        assert signals[0].detail[PRICED_INVOCATION_COUNT_KEY] == 0

    def test_firing_does_not_depend_on_trace_coverage(self) -> None:
        # AC6: re-sourcing the dollar figure never changes whether a signal fires.
        fired = [
            len(extract_model_routing_signals(
                _simple_group(linked=k, total=5), {"pm": _config(MODEL_OPUS)},
            ))
            for k in range(6)
        ]
        assert fired == [1] * 6


class TestSavingsScopeSentence:
    def test_phrase(self) -> None:
        assert savings_scope_phrase(3, 10) == "3 of 10 invocations"
        assert savings_scope_phrase(10, 10) == "10 invocations"

    def _mismatch(self, linked: int) -> DiagnosticSignal:
        signals = extract_model_routing_signals(
            _simple_group(linked=linked, total=5), {"pm": _config(MODEL_OPUS)},
        )
        assert len(signals) == 1
        return signals[0]

    def test_correlator_names_the_covered_count(self) -> None:
        recs = correlate([self._mismatch(2)], {"pm": _config(MODEL_OPUS)})
        assert any("across 2 of 5 invocations" in r.action for r in recs)

    def test_dedup_note_names_the_covered_count(self) -> None:
        note = _append_mismatch_phrase("suppressed", self._mismatch(2))
        assert "across 2 of 5 invocations" in note

    def test_fully_covered_group_reads_n_invocations(self) -> None:
        signal = self._mismatch(5)
        note = _append_mismatch_phrase("suppressed", signal)
        assert "across 5 invocations" in note
        assert " of 5" not in note

    def test_detail_without_priced_count_reads_as_fully_covered(self) -> None:
        signal = self._mismatch(2)
        del signal.detail[PRICED_INVOCATION_COUNT_KEY]
        assert "across 5 invocations" in _append_mismatch_phrase("suppressed", signal)


def _turn(model: str | None, usage: Usage | None) -> SessionMessage:
    return SessionMessage(type="assistant", model=model, entrypoint="sdk-py", usage=usage)


class TestMainSessionSavings:
    def test_prices_counted_turns_at_their_own_models(self) -> None:
        light = Usage(input_tokens=20, output_tokens=30)
        other = Usage(input_tokens=40, output_tokens=10, cache_read_input_tokens=5_000)
        messages = [
            _turn(MODEL_OPUS, light),
            _turn(MODEL_OPUS, light),
            _turn(MODEL_OPUS, light),
            _turn(MODEL_SONNET, other),
            _turn("<synthetic>", Usage(input_tokens=999_999)),
            _turn(MODEL_OPUS, None),
        ]
        session = SessionAnalysis(
            session_path=Path("main.jsonl"),
            token_metrics=TokenMetrics(),
            tool_metrics=ToolMetrics(),
            agent_metrics=AgentMetrics(),
            session_kind="sdk",
            messages=messages,
        )
        signals = extract_sdk_main_session_signals([session])
        assert len(signals) == 1
        detail = signals[0].detail
        current = 3 * _manual_cost(light, MODEL_OPUS) + _manual_cost(other, MODEL_SONNET)
        alt = 3 * _manual_cost(light, MODEL_HAIKU) + _manual_cost(other, MODEL_HAIKU)
        assert detail["current_cost_usd"] == pytest.approx(current)
        assert detail[SAVINGS_USD_KEY] == pytest.approx(max(0.0, current - alt))
        assert detail[PRICED_INVOCATION_COUNT_KEY] == 4


def _duration_signal(spend: Usage | None, trace_model: str | None) -> DiagnosticSignal:
    return DiagnosticSignal(
        signal_type=SignalType.DURATION_OUTLIER,
        severity=Severity.WARNING,
        agent_type="pm",
        message="slow",
        detail={"current_model": trace_model, "total_tokens": 500_000},
        spend_usage=spend,
    )


class TestDurationOutlierSavings:
    def test_priced_from_trace_spend_at_the_trace_model(self) -> None:
        recs = correlate([_duration_signal(_SPEND, MODEL_OPUS)], {"pm": _config(MODEL_OPUS)})
        savings = _manual_cost(_SPEND, MODEL_OPUS) - _manual_cost(_SPEND, MODEL_SONNET)
        assert f"estimated savings: ${savings:.2f} for this invocation" in recs[0].action

    def test_current_side_is_the_trace_model_not_the_config_model(self) -> None:
        # Config declares Opus, so the target is Sonnet; the trace actually ran
        # Haiku. Priced at the config model this would show Opus → Sonnet
        # savings; priced at what was spent (Haiku → Sonnet) it saves nothing.
        recs = correlate([_duration_signal(_SPEND, MODEL_HAIKU)], {"pm": _config(MODEL_OPUS)})
        assert len(recs) == 1
        assert "claude-sonnet" in recs[0].action
        assert "estimated savings" not in recs[0].action

    def test_no_trace_spend_no_dollar_figure_even_with_total_tokens(self) -> None:
        recs = correlate([_duration_signal(None, MODEL_OPUS)], {"pm": _config(MODEL_OPUS)})
        assert len(recs) == 1
        assert "claude-sonnet" in recs[0].action
        assert "$" not in recs[0].action

    def test_spend_usage_is_not_serialized(self) -> None:
        signal = _duration_signal(_SPEND, MODEL_OPUS)
        assert "spend_usage" not in signal.model_dump()
        assert "spend_usage" not in json.loads(signal.model_dump_json())
        reloaded = DiagnosticSignal.model_validate_json(signal.model_dump_json())
        assert reloaded.spend_usage is None

    def test_extractor_attaches_spend_only_for_spend_traces(self) -> None:
        # Trace-linked (duration_reliable) invocations; the last is the outlier.
        # Its trace is <synthetic> in the second run: no spend, no usage attached.
        durations = [1_000, 1_100, 1_200, 1_300, 1_400, 1_500, 90_000]

        def run(outlier_model: str) -> list[DiagnosticSignal]:
            invs = []
            for i, ms in enumerate(durations):
                model = outlier_model if i == len(durations) - 1 else MODEL_SONNET
                inv = _inv(trace=_trace(model, agent_id=f"a{i}"), tool_uses=1, n=i)
                inv.duration_ms = ms
                invs.append(inv)
            return _extract_duration_outliers(invs)

        signals = run(MODEL_OPUS)
        assert len(signals) == 1
        assert signals[0].spend_usage == _SPEND
        assert signals[0].detail["current_model"] == MODEL_OPUS

        synthetic = run("<synthetic>")
        assert len(synthetic) == 1
        assert synthetic[0].spend_usage is None


class TestSimpleBranchCoverage:
    def test_one_rollup_among_many_traces_is_not_simple(self) -> None:
        # One rollup invocation carries tokens; ten trace-linked ones carry only
        # tool calls. Before #675 its 500 tokens paired with everyone's tool count.
        invs = [_inv(total_tokens=500, tool_uses=2, n=0)] + [
            _inv(total_tokens=None, tool_uses=2, n=i) for i in range(1, 11)
        ]
        stats = aggregate_agent_stats(invs, {"pm": _config(MODEL_OPUS)})["pm"]
        assert classify_complexity(stats) == "moderate"
        assert extract_model_routing_signals(invs, {"pm": _config(MODEL_OPUS)}) == []

    def test_three_matched_invocations_reach_simple(self) -> None:
        invs = [_inv(total_tokens=500, tool_uses=2, n=i) for i in range(3)] + [
            _inv(total_tokens=None, tool_uses=2, n=i) for i in range(3, 10)
        ]
        stats = aggregate_agent_stats(invs, None)["pm"]
        assert classify_complexity(stats) == "simple"

    def test_matched_inputs_average_only_invocations_with_both_values(self) -> None:
        invs = [
            _inv(total_tokens=600, tool_uses=1, n=0),
            _inv(total_tokens=900, tool_uses=3, n=1),
            _inv(total_tokens=1_200, tool_uses=5, n=2),
            _inv(total_tokens=None, tool_uses=40, n=3),
            _inv(total_tokens=99_000, tool_uses=None, n=4),
        ]
        inputs = simple_branch_inputs(invs)
        assert inputs.mean_tool_calls == pytest.approx(3.0)
        assert inputs.mean_tokens == pytest.approx(900.0)

    def test_complex_branch_still_reads_group_wide_means(self) -> None:
        # AC6: trace-only invocations with many tool calls still make the group
        # complex, even though the matched subset alone would look simple.
        invs = [_inv(total_tokens=500, tool_uses=2, n=i) for i in range(3)] + [
            _inv(total_tokens=None, tool_uses=40, n=i) for i in range(3, 6)
        ]
        stats = aggregate_agent_stats(invs, None)["pm"]
        assert stats.mean_tool_calls == pytest.approx(21.0)
        assert classify_complexity(stats) == "complex"

    def test_cluster_stats_use_the_same_matched_inputs(self) -> None:
        invs = [_inv(total_tokens=500, tool_uses=2, n=0)] + [
            _inv(total_tokens=None, tool_uses=2, n=i) for i in range(1, 11)
        ]
        stats = aggregate_cluster_stats(invs)
        assert stats.simple_inputs == simple_branch_inputs(invs)
        assert classify_complexity(stats) == "moderate"

    def test_stats_without_simple_inputs_read_their_own_means(self) -> None:
        stats = AgentStats(
            agent_type="SDK main [x]",
            invocation_count=5,
            mean_tool_calls=1.0,
            mean_tokens=100.0,
            error_rate=0.0,
            has_write_tools=False,
            current_model=None,
        )
        assert classify_complexity(stats) == "simple"
