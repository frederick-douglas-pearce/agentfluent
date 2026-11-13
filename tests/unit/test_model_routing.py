"""Tests for model-routing diagnostics."""

from __future__ import annotations

from pathlib import Path

from agentfluent.agents.models import AgentInvocation
from agentfluent.analytics.pricing import get_known_models
from agentfluent.config.models import AgentConfig, Scope, Severity
from agentfluent.diagnostics._complexity import (
    _TIER_BY_FAMILY,
    _TIER_TO_MODEL,
    compute_error_rate,
    faster_tier,
)
from agentfluent.diagnostics.delegation import (
    MODEL_HAIKU,
    MODEL_OPUS,
    MODEL_SONNET,
)
from agentfluent.diagnostics.model_routing import (
    AgentStats,
    _compute_savings,
    _resolve_current_model,
    aggregate_agent_stats,
    classify_complexity,
    classify_model_tier,
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
    resolved_model: str | None = None,
) -> AgentInvocation:
    return AgentInvocation(
        agent_type=agent_type,
        description="test",
        prompt="do something",
        tool_use_id=f"tool_{agent_type}",
        total_tokens=total_tokens,
        tool_uses=tool_uses,
        output_text=output_text,
        trace=trace,
        resolved_model=resolved_model,
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


class TestModelPrecedence:
    """Precedence chain: `AgentConfig.model` → `SubagentTrace.model`
    → None. See #142 — when no config is declared, fall back to the
    model observed on the subagent's own trace so model-routing can
    still analyze the agent."""

    def _trace_with_model(self, model: str) -> SubagentTrace:
        return SubagentTrace(
            agent_id="t", agent_type="unknown",
            delegation_prompt="", model=model,
        )

    def test_trace_model_used_when_no_config(self) -> None:
        trace = self._trace_with_model(MODEL_OPUS)
        invs = [
            _inv(agent_type="pm", trace=trace, tool_uses=2, total_tokens=500)
            for _ in range(5)
        ]
        # No config at all — previously this would skip the agent.
        signals = extract_model_routing_signals(invs, configs=None)
        assert len(signals) == 1
        assert signals[0].detail["mismatch_type"] == "overspec"
        assert signals[0].detail["current_model"] == MODEL_OPUS

    def test_config_model_wins_over_trace_model(self) -> None:
        # Config says Opus; trace says Haiku. Config is the explicit
        # declaration the author would edit, so it wins.
        trace = self._trace_with_model(MODEL_HAIKU)
        invs = [
            _inv(agent_type="pm", trace=trace, tool_uses=2, total_tokens=500)
            for _ in range(5)
        ]
        configs = {"pm": _config(model=MODEL_OPUS)}
        signals = extract_model_routing_signals(invs, configs)
        # Flagged as overspec because config says Opus on a simple task.
        assert len(signals) == 1
        assert signals[0].detail["current_model"] == MODEL_OPUS

    def test_no_model_anywhere_still_skipped(self) -> None:
        # Neither config nor trace carries a model — agent stays
        # invisible to model-routing, the existing MVP behavior.
        trace = SubagentTrace(
            agent_id="t", agent_type="unknown",
            delegation_prompt="", model=None,
        )
        invs = [
            _inv(agent_type="pm", trace=trace, tool_uses=2, total_tokens=500)
            for _ in range(5)
        ]
        assert extract_model_routing_signals(invs, configs=None) == []


class TestResolvedModelPrecedence:
    """`resolved_model` fallback (#112 AC#4): `config → trace → resolved
    → None`, verified without a cross-file join into the child trace.

    The load-bearing case is the single-pass held-fallback in
    ``_resolve_current_model``: any linked ``trace.model`` in the group
    must beat any ``resolved_model``, EVEN when the trace lives on a
    LATER invocation than an earlier ``resolved_model`` — the pass holds
    the resolved value as a fallback and only returns it if no trace
    surfaces.
    """

    def _trace_with_model(self, model: str) -> SubagentTrace:
        return SubagentTrace(
            agent_id="t", agent_type="unknown",
            delegation_prompt="", model=model,
        )

    def test_resolved_model_used_when_no_config_no_trace(self) -> None:
        # SDK subagent: no .claude/agents config, no linked trace — only
        # the parent tool-result's resolved_model. It becomes current_model.
        assert (
            _resolve_current_model(
                [_inv(agent_type="pm", resolved_model=MODEL_OPUS)],
                config=None,
            )
            == MODEL_OPUS
        )

    def test_config_wins_over_resolved(self) -> None:
        assert (
            _resolve_current_model(
                [_inv(agent_type="pm", resolved_model=MODEL_HAIKU)],
                config=_config(model=MODEL_OPUS),
            )
            == MODEL_OPUS
        )

    def test_trace_wins_over_resolved_same_inv(self) -> None:
        inv = _inv(
            agent_type="pm",
            trace=self._trace_with_model(MODEL_SONNET),
            resolved_model=MODEL_HAIKU,
        )
        assert _resolve_current_model([inv], config=None) == MODEL_SONNET

    def test_trace_on_later_inv_beats_resolved_on_earlier_inv(self) -> None:
        # The dicey held-fallback case: inv[0] has ONLY resolved_model,
        # inv[1] has a trace.model. The trace must win despite appearing
        # after the resolved value in iteration order.
        invs = [
            _inv(agent_type="pm", resolved_model=MODEL_HAIKU),
            _inv(agent_type="pm", trace=self._trace_with_model(MODEL_SONNET)),
        ]
        assert _resolve_current_model(invs, config=None) == MODEL_SONNET

    def test_first_resolved_used_when_multiple_and_no_trace(self) -> None:
        invs = [
            _inv(agent_type="pm", resolved_model=MODEL_HAIKU),
            _inv(agent_type="pm", resolved_model=MODEL_OPUS),
        ]
        assert _resolve_current_model(invs, config=None) == MODEL_HAIKU

    def test_none_when_no_source(self) -> None:
        assert (
            _resolve_current_model([_inv(agent_type="pm")], config=None) is None
        )

    def test_resolved_model_drives_signal_end_to_end(self) -> None:
        # No config, no trace — resolved_model=Opus on a simple task
        # yields an overspec signal, proving resolved_model flows through
        # aggregation → detection without a child-trace join.
        invs = [
            _inv(
                agent_type="pm", resolved_model=MODEL_OPUS,
                tool_uses=2, total_tokens=500,
            )
            for _ in range(5)
        ]
        signals = extract_model_routing_signals(invs, configs=None)
        assert len(signals) == 1
        assert signals[0].detail["current_model"] == MODEL_OPUS
        assert signals[0].detail["mismatch_type"] == "overspec"
        # Subagent origin is the default scope.
        assert signals[0].detail["routing_scope"] == "subagent"


class TestClassifyComplexity:
    def test_simple_case(self) -> None:
        assert classify_complexity(
            _stats(mean_tool_calls=2.0, mean_tokens=500.0),
        ) == "simple"

    def test_write_tools_alone_does_not_escalate(self) -> None:
        # Per #185 architect review, has_write_tools is NOT a
        # classification input — write-tool presence alone (without
        # high token volume or tool-call count) must not escalate to
        # "complex". With _stats() defaults (mean_tool_calls=3,
        # mean_tokens=1000) the result is "simple" via the simple-tier
        # check; under the pre-#185 unified path it would have been
        # "complex" (the over-recommendation #185 was filed to fix).
        assert classify_complexity(_stats(has_write_tools=True)) == "simple"

    def test_high_tokens_escalates_regardless_of_write_tools(self) -> None:
        # The token-volume threshold is the actual escalation signal.
        # Write-tool presence is metadata, not a classifier input.
        assert classify_complexity(
            _stats(has_write_tools=True, mean_tokens=10_000),
        ) == "complex"
        assert classify_complexity(
            _stats(has_write_tools=False, mean_tokens=10_000),
        ) == "complex"

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
        assert compute_error_rate(inv) == 0.0

    def test_error_rate_ignores_mid_text_keywords(self) -> None:
        """#281: ``compute_error_rate`` only counts ``ERROR_REGEX``
        matches in the leading window. Long agent outputs that mention
        error-handling topics in the body (code identifiers like
        ``tool_error_sequence``, schema fields like ``is_error?``)
        previously inflated the rate via unbounded ``findall`` —
        biasing complexity classification toward 'complex' on prose
        that wasn't reporting actual errors."""
        # 800-char benign prefix (no keywords), then four mid-text
        # mentions of error-related topics. Pre-fix this would compute
        # error_rate = 4 / tool_uses. Post-fix the count is 0.
        prefix = "The implementation completed cleanly. " * 25  # ~975 chars
        body = (
            " The is_error field on tool_result is set when a permission "
            "denied or other failure occurs. The retry logic handles "
            "transient errors gracefully."
        )
        inv = _inv(tool_uses=4, output_text=prefix + body)
        assert compute_error_rate(inv) == 0.0

    def test_error_rate_counts_leading_keywords(self) -> None:
        """Real errors leading the response still count toward the rate."""
        # Two distinct keywords in the leading 200 chars; tool_uses=4 →
        # rate=0.5.
        inv = _inv(
            tool_uses=4,
            output_text=(
                "Permission denied: cannot access /etc/shadow. "
                "Retry attempted but failed. Long trailing prose…"
                + (" filler" * 100)
            ),
        )
        rate = compute_error_rate(inv)
        # Exact match count depends on regex tokenization; assert
        # non-zero and bounded by 1.0 (sanity).
        assert 0.0 < rate <= 1.0

    def test_classify_model_tier_short_aliases(self) -> None:
        assert classify_model_tier(MODEL_HAIKU) == "simple"
        assert classify_model_tier(MODEL_SONNET) == "moderate"
        assert classify_model_tier(MODEL_OPUS) == "complex"

    def test_classify_model_tier_handles_dated_pinned_forms(self) -> None:
        # Subagent traces record dated forms at runtime; family-prefix
        # matching covers both short aliases and pinned variants.
        assert classify_model_tier("claude-haiku-4-5-20251001") == "simple"
        assert classify_model_tier("claude-sonnet-4-5-20250929") == "moderate"
        assert classify_model_tier("claude-opus-4-5-20251101") == "complex"

    def test_classify_model_tier_claims_the_fable_family(self) -> None:
        # #674 curated Fable for pricing, which made it reachable here. Placed in
        # `complex` by price rank (it prices above every Opus) -- see the caveat on
        # `_TIER_BY_FAMILY`. Without this the family falls to the `moderate` default,
        # which is precisely what `test_every_priced_model_classifies_by_family` forbids.
        assert classify_model_tier("claude-fable-5-1") == "complex"
        assert classify_model_tier("claude-fable-5-2") == "complex"

    def test_faster_tier_steps_fable_down_one_tier_not_two(self) -> None:
        # The one consumer where the tier choice actually bites (#674 architect review):
        # DurationOutlier recommends `faster_tier(current_model)`. On the `moderate`
        # default a slow Fable agent would be told to drop straight to Haiku -- two steps
        # down from the most expensive model in the lineup. One step is the contract.
        assert faster_tier("claude-fable-5-1") == "moderate"
        assert faster_tier("claude-fable-5-2") == "moderate"

    def test_ladders_agree_for_every_curated_model(self) -> None:
        # `parent_workload.pick_alternative_model` is a SECOND model->tier ladder,
        # hand-written and not derived from `_TIER_BY_FAMILY`. #674 curated the Fable
        # family for pricing and that ladder did not know it, so a Fable parent session
        # was recommended the model it was already running — with a large negative
        # saving that also tripped a "cache appears load-bearing" note, a false
        # diagnosis. Neither half of #674 caused that alone: it needed pricing to make
        # Fable priceable AND the ladder to not know the family.
        #
        # This pins the two ladders together over the whole curated registry, so the
        # NEXT family added fails here instead of shipping a nonsense recommendation.
        # Consolidating them outright is #683 — it needs an explicit "unknown" result
        # from `classify_model_tier` first, since `faster_tier` currently maps an
        # unknown model to "simple" via the moderate default.
        from agentfluent.diagnostics.parent_workload import pick_alternative_model

        mismatched = []
        for model in get_known_models():
            tier = faster_tier(model)
            expected = _TIER_TO_MODEL[tier] if tier is not None else model
            actual = pick_alternative_model(model)
            if actual != expected:
                mismatched.append((model, actual, expected))
        assert not mismatched, (
            "pick_alternative_model disagrees with faster_tier/_TIER_TO_MODEL for: "
            f"{mismatched}"
        )

    def test_classify_model_tier_unknown_family_defaults_moderate(self) -> None:
        assert classify_model_tier("claude-experimental-9000") == "moderate"
        assert classify_model_tier("") == "moderate"

    def test_every_priced_model_classifies_by_family(self) -> None:
        # Regression guard: every model pricing knows about must match a family
        # prefix in `_TIER_BY_FAMILY`. A priced model matching none of them has
        # shipped a family the classifier doesn't recognize, and it lands on the
        # "moderate" default — which would silently suppress real MODEL_MISMATCH
        # emissions.
        #
        # Iterate the prefixes rather than testing the classifier's OUTPUT against
        # a hard-coded family name (#674 review). The earlier form asked for
        # `classify_model_tier(m) == "moderate" and not m.startswith("claude-sonnet")`,
        # which could not fail for a REMOVED family: "moderate" is also the
        # unmatched-family default, so deleting `claude-sonnet` from
        # `_TIER_BY_FAMILY` sent every sonnet id to the default, the literal
        # excluded them all, and the guard stayed green. Reading the map is what
        # makes removal detectable, and it keeps the family list in exactly one place.
        prefixes = tuple(p for ps in _TIER_BY_FAMILY.values() for p in ps)
        unknown = [m for m in get_known_models() if not m.startswith(prefixes)]
        assert not unknown, (
            f"Priced models not claimed by any family prefix: {unknown}"
        )

    def test_severity_is_warning(self) -> None:
        invs = [_inv(agent_type="pm", tool_uses=2, total_tokens=500) for _ in range(5)]
        configs = {"pm": _config(model=MODEL_OPUS)}
        signals = extract_model_routing_signals(invs, configs)
        assert signals[0].severity == Severity.WARNING
