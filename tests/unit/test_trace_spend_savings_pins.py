"""Mechanism pins for #675 (PR #708 code review round 1, guard-efficacy g1-g7 + i2).

Each test fails for a specific plausible broken implementation, named in its
docstring.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentfluent.agents.models import AgentInvocation
from agentfluent.analytics.agent_metrics import AgentMetrics
from agentfluent.analytics.pipeline import SessionAnalysis
from agentfluent.analytics.pricing import MODEL_HAIKU, MODEL_OPUS, MODEL_SONNET
from agentfluent.analytics.tokens import TokenMetrics, price_usage, trace_spend
from agentfluent.analytics.tools import ToolMetrics
from agentfluent.config.models import AgentConfig, Scope
from agentfluent.core.session import SessionMessage, Usage
from agentfluent.diagnostics._complexity import (
    classify_complexity,
    simple_branch_inputs,
)
from agentfluent.diagnostics.correlator import correlate as _correlate_pairs
from agentfluent.diagnostics.model_routing import (
    PRICED_INVOCATION_COUNT_KEY,
    SAVINGS_USD_KEY,
    aggregate_agent_stats,
    estimate_model_savings,
    extract_model_routing_signals,
    extract_sdk_main_session_signals,
)
from agentfluent.diagnostics.models import DiagnosticRecommendation, DiagnosticSignal
from agentfluent.diagnostics.signals import _extract_duration_outliers
from agentfluent.traces.models import SubagentTrace

_SPEND = Usage(
    input_tokens=1_000,
    output_tokens=2_000,
    cache_creation_input_tokens=30_000,
    cache_read_input_tokens=400_000,
    cache_creation_5m_input_tokens=20_000,
    cache_creation_1h_input_tokens=10_000,
)
# What #701's fork dedup leaves of _SPEND: far smaller, so pricing the per-file
# ``usage`` instead of ``spend_usage`` is unmistakable.
_DEDUPED = Usage(input_tokens=100, output_tokens=50)


def _cost(usage: Usage, model: str) -> float:
    cost = price_usage(usage, model, timestamp=None)
    assert cost is not None
    return cost


def _trace(model: str, *, agent_id: str = "a", deduped: bool = False) -> SubagentTrace:
    trace = SubagentTrace(
        agent_id=agent_id, agent_type="pm", delegation_prompt="", model=model, usage=_SPEND,
    )
    if deduped:
        trace.spend_usage = _DEDUPED
    return trace


def _inv(
    *,
    agent_type: str = "pm",
    trace: SubagentTrace | None = None,
    total_tokens: int | None = 500,
    tool_uses: int | None = 2,
    n: int = 0,
) -> AgentInvocation:
    return AgentInvocation(
        agent_type=agent_type,
        description="d",
        prompt="p",
        tool_use_id=f"toolu_{agent_type}_{n}",
        total_tokens=total_tokens,
        tool_uses=tool_uses,
        trace=trace,
    )


def _config(name: str, model: str) -> AgentConfig:
    return AgentConfig(
        name=name,
        file_path=Path(f"/home/user/.claude/agents/{name}.md"),
        scope=Scope.USER,
        model=model,
    )


def _group(
    *, linked: int, total: int, model: str, agent_type: str = "pm",
) -> list[AgentInvocation]:
    return [
        _inv(
            agent_type=agent_type,
            trace=_trace(model, agent_id=f"{agent_type}{i}") if i < linked else None,
            n=i,
        )
        for i in range(total)
    ]


def correlate(
    signals: list[DiagnosticSignal], configs: dict[str, AgentConfig],
) -> list[DiagnosticRecommendation]:
    return [rec for _, rec in _correlate_pairs(signals, configs, None)]


class TestDedupedSpend:
    """g1 — broken: pricing ``trace.usage`` (per-file) instead of the #701-deduped
    ``spend_usage`` would double-count fork-replayed turns."""

    def test_trace_spend_returns_the_deduped_usage(self) -> None:
        assert trace_spend(_trace(MODEL_OPUS, deduped=True)) == (_DEDUPED, MODEL_OPUS)

    def test_savings_price_the_deduped_usage(self) -> None:
        estimate = estimate_model_savings(
            [_inv(trace=_trace(MODEL_OPUS, deduped=True))], MODEL_HAIKU,
        )
        assert estimate is not None
        assert estimate.current_cost_usd == pytest.approx(_cost(_DEDUPED, MODEL_OPUS))

    def test_duration_signal_carries_the_deduped_usage(self) -> None:
        durations = [1_000, 1_100, 1_200, 1_300, 1_400, 1_500, 90_000]
        invs = []
        for i, ms in enumerate(durations):
            inv = _inv(trace=_trace(MODEL_OPUS, agent_id=f"d{i}", deduped=True), tool_uses=1, n=i)
            inv.duration_ms = ms
            invs.append(inv)
        signals = _extract_duration_outliers(invs)
        assert len(signals) == 1
        assert signals[0].spend_usage == _DEDUPED


class TestSignalPathCurrentModel:
    """g2 — broken: pricing the current side at the config-declared model."""

    def test_current_side_is_the_trace_model(self) -> None:
        # Config declares Opus (so the target is Haiku); the traces ran Sonnet.
        signals = extract_model_routing_signals(
            _group(linked=2, total=5, model=MODEL_SONNET), {"pm": _config("pm", MODEL_OPUS)},
        )
        assert len(signals) == 1
        detail = signals[0].detail
        assert detail["current_model"] == MODEL_OPUS
        assert detail["current_cost_usd"] == pytest.approx(2 * _cost(_SPEND, MODEL_SONNET))
        assert detail[SAVINGS_USD_KEY] == pytest.approx(
            2 * (_cost(_SPEND, MODEL_SONNET) - _cost(_SPEND, MODEL_HAIKU)),
        )


class TestPerTypeGrouping:
    """g3 — broken: one spend list shared by every type, or spend keyed on the
    raw ``agent_type`` while stats key on its lowercase form."""

    def test_each_type_prices_only_its_own_traces(self) -> None:
        invs = _group(linked=2, total=5, model=MODEL_OPUS, agent_type="PM") + _group(
            linked=5, total=5, model=MODEL_OPUS, agent_type="qa",
        )
        configs = {"pm": _config("pm", MODEL_OPUS), "qa": _config("qa", MODEL_OPUS)}
        by_type = {s.agent_type: s.detail for s in extract_model_routing_signals(invs, configs)}
        per_inv = _cost(_SPEND, MODEL_OPUS) - _cost(_SPEND, MODEL_HAIKU)
        assert by_type["PM"][PRICED_INVOCATION_COUNT_KEY] == 2
        assert by_type["PM"][SAVINGS_USD_KEY] == pytest.approx(2 * per_inv)
        assert by_type["qa"][PRICED_INVOCATION_COUNT_KEY] == 5
        assert by_type["qa"][SAVINGS_USD_KEY] == pytest.approx(5 * per_inv)


class TestCorrelatorScopePhrase:
    """g4 — broken: the correlator formatting "k of N" inline instead of via
    ``savings_scope_from_detail`` ("5 of 5", or a KeyError on legacy detail)."""

    def _signal(self, linked: int) -> DiagnosticSignal:
        signals = extract_model_routing_signals(
            _group(linked=linked, total=5, model=MODEL_OPUS), {"pm": _config("pm", MODEL_OPUS)},
        )
        assert len(signals) == 1
        return signals[0]

    def test_full_coverage_reads_n_invocations(self) -> None:
        recs = correlate([self._signal(5)], {"pm": _config("pm", MODEL_OPUS)})
        actions = [r.action for r in recs]
        assert any("across 5 invocations" in a for a in actions)
        assert not any(" of 5 invocations" in a for a in actions)

    def test_legacy_detail_without_priced_count_reads_full_scope(self) -> None:
        signal = self._signal(2)
        del signal.detail[PRICED_INVOCATION_COUNT_KEY]
        actions = [r.action for r in correlate([signal], {"pm": _config("pm", MODEL_OPUS)})]
        assert any("across 5 invocations" in a for a in actions)


def _session(messages: list[SessionMessage]) -> SessionAnalysis:
    return SessionAnalysis(
        session_path=Path("main.jsonl"),
        token_metrics=TokenMetrics(),
        tool_metrics=ToolMetrics(),
        agent_metrics=AgentMetrics(),
        session_kind="sdk",
        messages=messages,
    )


class TestMainSessionTurnFilter:
    """g5 — broken: filtering on ``usage`` + ``model`` alone, without
    ``counts_as_parent_call``, would price a non-assistant usage-carrying line."""

    def test_non_assistant_usage_is_not_priced(self) -> None:
        light = Usage(input_tokens=20, output_tokens=30)
        turns = [
            SessionMessage(type="assistant", model=MODEL_OPUS, entrypoint="sdk-py", usage=light)
            for _ in range(4)
        ]
        stray = SessionMessage(
            type="user", model=MODEL_OPUS, entrypoint="sdk-py", usage=Usage(input_tokens=50_000),
        )
        signals = extract_sdk_main_session_signals([_session([*turns, stray])])
        assert len(signals) == 1
        detail = signals[0].detail
        assert detail[PRICED_INVOCATION_COUNT_KEY] == 4
        assert detail["current_cost_usd"] == pytest.approx(4 * _cost(light, MODEL_OPUS))


class TestEvidenceFloor:
    """g6 — broken: a floor of 2 (or 1) instead of the approved 3."""

    def test_two_matched_invocations_are_not_simple(self) -> None:
        invs = [_inv(total_tokens=500, tool_uses=2, n=i) for i in range(2)] + [
            _inv(total_tokens=None, tool_uses=2, n=i) for i in range(2, 10)
        ]
        assert simple_branch_inputs(invs).mean_tokens == 0.0
        assert classify_complexity(aggregate_agent_stats(invs, None)["pm"]) == "moderate"


class TestComplexBranchTokens:
    """g7 — broken: the complex branch (or the published ``mean_tokens``) reading
    the matched subset instead of the group-wide mean."""

    def test_group_wide_tokens_still_make_the_group_complex(self) -> None:
        invs = [_inv(total_tokens=500, tool_uses=2, n=i) for i in range(3)] + [
            _inv(total_tokens=50_000, tool_uses=None, n=i) for i in range(3, 6)
        ]
        stats = aggregate_agent_stats(invs, None)["pm"]
        assert stats.mean_tokens == pytest.approx(25_250.0)
        assert classify_complexity(stats) == "complex"
        assert simple_branch_inputs(invs).mean_tokens == pytest.approx(500.0)


class TestPublishedSimpleBranchInputs:
    """i2 — the published detail carries what the "simple" branch read, beside
    the group-wide means. Broken: publishing only the group-wide means."""

    def test_subset_and_group_means_are_both_published(self) -> None:
        # 3 rollup invocations x 1 tool call + 3 trace-only x 10 tool calls:
        # group mean 5.5 (>= the simple ceiling of 5), simple-branch mean 1.0.
        invs = [_inv(total_tokens=500, tool_uses=1, n=i) for i in range(3)] + [
            _inv(total_tokens=None, tool_uses=10, n=i) for i in range(3, 6)
        ]
        signals = extract_model_routing_signals(invs, {"pm": _config("pm", MODEL_OPUS)})
        assert len(signals) == 1
        detail = signals[0].detail
        assert detail["complexity_tier"] == "simple"
        assert detail["mean_tool_calls"] == pytest.approx(5.5)
        assert detail["simple_branch_mean_tool_calls"] == pytest.approx(1.0)
        assert detail["simple_branch_mean_tokens"] == pytest.approx(500.0)
        assert detail["simple_branch_invocation_count"] == 3

    def test_main_session_publishes_its_own_means(self) -> None:
        light = Usage(input_tokens=20, output_tokens=30)
        turns = [
            SessionMessage(type="assistant", model=MODEL_SONNET, entrypoint="sdk-py", usage=light)
            for _ in range(4)
        ]
        signals = extract_sdk_main_session_signals([_session(turns)])
        assert len(signals) == 1
        detail = signals[0].detail
        assert detail["simple_branch_mean_tool_calls"] == detail["mean_tool_calls"]
        assert detail["simple_branch_mean_tokens"] == detail["mean_tokens"]
        assert detail["simple_branch_invocation_count"] == 4
