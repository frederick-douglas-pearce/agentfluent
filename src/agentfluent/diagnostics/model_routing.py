"""Model-routing diagnostics: complexity classification + mismatch detection.

Aggregates per-agent-type statistics from observed invocations, classifies
each agent's task complexity (simple / moderate / complex), and emits a
``MODEL_MISMATCH`` signal when the declared model tier is wrong for the
complexity. Downstream, ``ModelRoutingRule`` in ``correlator.py`` turns
the signal into an actionable ``target="model"`` recommendation with a
cost-savings estimate (when pricing is available).

**MVP scope constraint.** Model-routing only activates for agent types
with an explicit ``model:`` field in their ``.claude/agents/X.md``
frontmatter. Agents without a declared model are silently skipped.
Broader coverage (inferring the model from ``SubagentTrace``) is
tracked in #142.

**Cost approximation.** ``AgentInvocation.total_tokens`` is a single
combined figure; we split 50/50 for the cost calculation. The real
input/output ratio would change the savings estimate by up to ~2×
(Opus's output rate is 5× its input rate). Tracked in #143.

**Threshold calibration.** The complexity thresholds below are informed
by the same backlog guidance that drove #110's defaults. Empirical
tuning against real session data is tracked in #140; that issue covers
delegation-clustering thresholds and is extended by #111's needs.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import TYPE_CHECKING, Literal

from agentfluent.analytics.pricing import compute_cost, get_pricing
from agentfluent.config.models import Severity
from agentfluent.diagnostics._complexity import (
    MODEL_HAIKU,
    MODEL_SONNET,
    AgentStats,
    ComplexityTier,
    classify_complexity,
    compute_error_rate,
    has_write_tools_in_trace,
)
from agentfluent.diagnostics.models import DiagnosticSignal, SignalType

if TYPE_CHECKING:
    from agentfluent.agents.models import AgentInvocation
    from agentfluent.config.models import AgentConfig

logger = logging.getLogger(__name__)

# Re-exported for tests and downstream consumers that already import
# AgentStats / ComplexityTier / classify_complexity from this module.
__all__ = [
    "AgentStats",
    "ComplexityTier",
    "MismatchType",
    "aggregate_agent_stats",
    "classify_complexity",
    "classify_model_tier",
    "extract_model_routing_signals",
]

MismatchType = Literal["overspec", "underspec"]

_MIN_INVOCATIONS_FOR_ANALYSIS = 3
# Complexity thresholds and the AgentStats model live in
# diagnostics/_complexity.py — see #185 for the consolidation rationale.
# The error-rate threshold below is reused for the underspec gate; kept
# local here so the gate stays close to its consumer.
_UNDERSPEC_ERROR_RATE_GATE = 0.20

# Complexity tier by Claude model family. Model IDs follow the pattern
# `claude-<family>-<version>[-<date>]`, so a prefix match covers both
# short aliases (`claude-opus-4-7`) and dated pinned forms
# (`claude-haiku-4-5-20251001`) that subagent traces record at runtime.
# Avoids duplicating the model catalog with `analytics.pricing._PRICING`.
_TIER_BY_FAMILY: dict[ComplexityTier, tuple[str, ...]] = {
    "simple": ("claude-haiku",),
    "moderate": ("claude-sonnet",),
    "complex": ("claude-opus",),
}


def classify_model_tier(model: str) -> ComplexityTier:
    """Classify a Claude model ID into a complexity tier by family prefix.

    Returns "moderate" for anything that doesn't match a known family —
    a safe default that doesn't emit MODEL_MISMATCH signals against
    unrecognized models.
    """
    for tier, prefixes in _TIER_BY_FAMILY.items():
        if any(model.startswith(p) for p in prefixes):
            return tier
    return "moderate"


def _resolve_current_model(
    group: list[AgentInvocation],
    config: AgentConfig | None,
) -> str | None:
    """Pick the model for an agent_type using `config → trace → None`.

    The explicit ``AgentConfig.model`` wins when set — it's the
    declaration the user would edit. Fallback to the first linked
    trace's ``model`` field (populated at parse time from the
    subagent's first assistant message), which covers the common case
    where subagents inherit the parent session's model without
    declaring anything in frontmatter. Returns ``None`` when neither
    source has a value; downstream skips those agents.
    """
    if config and config.model:
        return config.model
    for inv in group:
        if inv.trace is not None and inv.trace.model:
            return inv.trace.model
    return None


def aggregate_agent_stats(
    invocations: list[AgentInvocation],
    configs: dict[str, AgentConfig] | None,
) -> dict[str, AgentStats]:
    """Roll up per-invocation metrics into per-agent-type aggregates.

    Keyed by lowercased agent_type to match the correlator's config
    lookup contract. ``current_model`` is resolved by
    ``_resolve_current_model`` using the ``config → trace → None``
    precedence chain.
    """
    groups: dict[str, list[AgentInvocation]] = defaultdict(list)
    for inv in invocations:
        groups[inv.agent_type.lower()].append(inv)

    stats_by_type: dict[str, AgentStats] = {}
    for key, group in groups.items():
        canonical_name = group[0].agent_type
        tool_use_values = [i.tool_uses for i in group if i.tool_uses is not None]
        token_values = [i.total_tokens for i in group if i.total_tokens is not None]
        error_rates = [compute_error_rate(i) for i in group]
        has_writes = any(has_write_tools_in_trace(i) for i in group)

        config = configs.get(key) if configs else None
        current_model = _resolve_current_model(group, config)

        stats_by_type[key] = AgentStats(
            agent_type=canonical_name,
            invocation_count=len(group),
            mean_tool_calls=(
                sum(tool_use_values) / len(tool_use_values)
                if tool_use_values else 0.0
            ),
            mean_tokens=(
                sum(token_values) / len(token_values)
                if token_values else 0.0
            ),
            error_rate=(
                sum(error_rates) / len(error_rates) if error_rates else 0.0
            ),
            has_write_tools=has_writes,
            current_model=current_model,
        )
    return stats_by_type


def _compute_savings(
    stats: AgentStats,
    alt_model: str,
) -> tuple[float | None, float | None]:
    """Estimate (savings_usd, current_cost_usd) for switching current → alt.

    Returns ``(None, None)`` when pricing is unavailable for either
    model — the recommendation still ships, just without the dollar
    figure. Token in/out split is approximated 50/50; real ratio
    tracked in #143.
    """
    if stats.current_model is None:
        return None, None
    current_pricing = get_pricing(stats.current_model)
    alt_pricing = get_pricing(alt_model)
    if current_pricing is None or alt_pricing is None:
        return None, None
    half = stats.mean_tokens / 2.0
    current_per_inv = compute_cost(
        current_pricing, input_tokens=int(half), output_tokens=int(half),
    )
    alt_per_inv = compute_cost(
        alt_pricing, input_tokens=int(half), output_tokens=int(half),
    )
    current_cost = current_per_inv * stats.invocation_count
    alt_cost = alt_per_inv * stats.invocation_count
    savings = max(0.0, current_cost - alt_cost)
    return savings, current_cost


def _build_mismatch_signal(
    stats: AgentStats,
    mismatch_type: MismatchType,
    complexity: ComplexityTier,
    recommended_model: str,
) -> DiagnosticSignal:
    # Savings are only meaningful for overspec (cheap recommended model).
    # For underspec, Haiku → Sonnet costs MORE; `max(0.0, ...)` would
    # always wipe it to zero anyway, so skip the pricing lookup.
    if mismatch_type == "overspec":
        savings, current_cost = _compute_savings(stats, recommended_model)
    else:
        savings, current_cost = None, None
    phrase = (
        f"complexity '{complexity}' agent '{stats.agent_type}' runs on "
        f"{stats.current_model} — consider {recommended_model}"
    )
    message = (
        f"Overspec'd model: {phrase}." if mismatch_type == "overspec"
        else f"Underspec'd model: {phrase}."
    )
    return DiagnosticSignal(
        signal_type=SignalType.MODEL_MISMATCH,
        severity=Severity.WARNING,
        agent_type=stats.agent_type,
        message=message,
        detail={
            "mismatch_type": mismatch_type,
            "current_model": stats.current_model,
            "recommended_model": recommended_model,
            "complexity_tier": complexity,
            "invocation_count": stats.invocation_count,
            "mean_tool_calls": stats.mean_tool_calls,
            "mean_tokens": stats.mean_tokens,
            "error_rate": stats.error_rate,
            "estimated_savings_usd": savings,
            "current_cost_usd": current_cost,
        },
    )


def _detect_mismatch(stats: AgentStats) -> DiagnosticSignal | None:
    """Emit a MODEL_MISMATCH signal when declared tier is wrong for complexity.

    Overspec: simple task on moderate/complex model. Always flagged.
    Underspec: complex task on simple (Haiku) model AND elevated error
    rate. The error-rate gate reduces false positives — a Haiku
    quietly handling complex work isn't a problem until it's also
    failing.
    """
    if stats.invocation_count < _MIN_INVOCATIONS_FOR_ANALYSIS:
        return None
    if stats.current_model is None:
        return None
    current_tier = classify_model_tier(stats.current_model)
    complexity = classify_complexity(stats)

    if complexity == "simple" and current_tier in ("moderate", "complex"):
        return _build_mismatch_signal(
            stats, "overspec", complexity, recommended_model=MODEL_HAIKU,
        )
    if (
        complexity == "complex"
        and current_tier == "simple"
        and stats.error_rate > _UNDERSPEC_ERROR_RATE_GATE
    ):
        return _build_mismatch_signal(
            stats, "underspec", complexity, recommended_model=MODEL_SONNET,
        )
    return None


def extract_model_routing_signals(
    invocations: list[AgentInvocation],
    configs: dict[str, AgentConfig] | None,
) -> list[DiagnosticSignal]:
    """Public entry: aggregate → classify → detect mismatches.

    Returns a list of ``MODEL_MISMATCH`` signals (possibly empty).
    Each signal's ``detail`` dict carries all the fields #113 needs to
    merge with #110's ``DelegationSuggestion`` output — see the
    signal contract documented on the signal type's module docstring.
    """
    if not invocations:
        return []
    stats_by_type = aggregate_agent_stats(invocations, configs)
    signals: list[DiagnosticSignal] = []
    for stats in stats_by_type.values():
        signal = _detect_mismatch(stats)
        if signal is not None:
            signals.append(signal)
    return signals
