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
from collections import Counter, defaultdict
from typing import TYPE_CHECKING, Literal

from agentfluent.analytics.pricing import compute_cost, get_pricing
from agentfluent.config.models import Severity
from agentfluent.diagnostics._complexity import (
    AgentStats,
    ComplexityTier,
    classify_complexity,
    classify_model_tier,
    compute_error_rate,
    has_write_tools_in_trace,
    select_target_model,
)
from agentfluent.diagnostics.models import DiagnosticSignal, SignalType

if TYPE_CHECKING:
    from agentfluent.agents.models import AgentInvocation
    from agentfluent.analytics.pipeline import SessionAnalysis
    from agentfluent.config.models import AgentConfig
    from agentfluent.core.session import Usage

logger = logging.getLogger(__name__)

# Re-exported for tests and downstream consumers that already import
# AgentStats / ComplexityTier / classify_complexity from this module.
__all__ = [
    "SAVINGS_USD_KEY",
    "AgentStats",
    "ComplexityTier",
    "MismatchType",
    "aggregate_agent_stats",
    "classify_complexity",
    "classify_model_tier",
    "estimate_model_savings",
    "extract_model_routing_signals",
    "extract_sdk_main_session_signals",
]

MismatchType = Literal["overspec", "underspec"]

# Producer/consumer contract: this module emits ``MODEL_MISMATCH``
# signals carrying ``estimated_savings_usd`` in their ``detail`` dict.
# Three consumers read that key — ``correlator.ModelRoutingRule``,
# ``pipeline._append_mismatch_phrase``, and ``aggregation._summed_savings_usd``.
# Sharing the constant prevents silent drift if any consumer typos
# the key (the dict-get fallback would degrade output without raising).
SAVINGS_USD_KEY = "estimated_savings_usd"

_MIN_INVOCATIONS_FOR_ANALYSIS = 3
# Complexity thresholds and the AgentStats model live in
# diagnostics/_complexity.py — see #185 for the consolidation rationale.
# The error-rate threshold below is reused for the underspec gate; kept
# local here so the gate stays close to its consumer.
_UNDERSPEC_ERROR_RATE_GATE = 0.20

def _resolve_current_model(
    group: list[AgentInvocation],
    config: AgentConfig | None,
) -> str | None:
    """Pick the model for an agent_type: `config → trace → resolved → None`.

    The explicit ``AgentConfig.model`` wins when set — it's the
    declaration the user would edit. Fallback to the first linked
    trace's ``model`` field (populated at parse time from the
    subagent's first assistant message), which covers the common case
    where subagents inherit the parent session's model without
    declaring anything in frontmatter. Final fallback is the parent
    tool-result's ``resolved_model`` (#593) — the concrete model the
    subagent resolved to, read without a cross-file join into the child
    trace (#112 AC#4). This covers SDK subagents defined in code (no
    ``.claude/agents/*.md`` config, and sometimes no linked trace).
    Returns ``None`` when no source has a value; downstream skips those.

    Any linked ``trace.model`` in the group beats ``resolved_model`` —
    a single pass returns a trace model as soon as one is seen, and
    otherwise falls back to the first ``resolved_model`` encountered.
    """
    if config and config.model:
        return config.model
    resolved_fallback: str | None = None
    for inv in group:
        if inv.trace is not None and inv.trace.model:
            return inv.trace.model
        if resolved_fallback is None and inv.resolved_model:
            resolved_fallback = inv.resolved_model
    return resolved_fallback


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


def estimate_model_savings(
    current_model: str | None,
    alt_model: str,
    total_tokens: float | None,
    count: int,
) -> tuple[float | None, float | None]:
    """Estimate ``(savings_usd, current_cost_usd)`` for ``current → alt``.

    Shared by both ``target: model`` paths (#170): the complexity-mismatch
    path prices ``mean_tokens × invocation_count``; the duration-outlier
    path prices a single outlier's ``total_tokens × 1``. Returns
    ``(None, None)`` when the current model is unknown, token data is
    missing, or pricing is unavailable for either model — the
    recommendation still ships, just without the dollar figure. Token
    in/out split is approximated 50/50; real ratio tracked in #143.
    """
    if not current_model or total_tokens is None:
        return None, None
    current_pricing = get_pricing(current_model)
    alt_pricing = get_pricing(alt_model)
    if current_pricing is None or alt_pricing is None:
        return None, None
    half = total_tokens / 2.0
    current_per_inv = compute_cost(
        current_pricing, input_tokens=int(half), output_tokens=int(half),
    )
    alt_per_inv = compute_cost(
        alt_pricing, input_tokens=int(half), output_tokens=int(half),
    )
    current_cost = current_per_inv * count
    alt_cost = alt_per_inv * count
    savings = max(0.0, current_cost - alt_cost)
    return savings, current_cost


def _compute_savings(
    stats: AgentStats,
    alt_model: str,
) -> tuple[float | None, float | None]:
    """Per-agent savings for the mismatch path: ``mean_tokens × count``."""
    return estimate_model_savings(
        stats.current_model, alt_model, stats.mean_tokens, stats.invocation_count,
    )


def _build_mismatch_signal(
    stats: AgentStats,
    mismatch_type: MismatchType,
    complexity: ComplexityTier,
    recommended_model: str,
    routing_scope: str = "subagent",
) -> DiagnosticSignal:
    # Savings are only meaningful for overspec (cheap recommended model).
    # For underspec, Haiku → Sonnet costs MORE; `max(0.0, ...)` would
    # always wipe it to zero anyway, so skip the pricing lookup.
    if mismatch_type == "overspec":
        savings, current_cost = _compute_savings(stats, recommended_model)
    else:
        savings, current_cost = None, None
    if routing_scope == "main_session":
        # ``stats.agent_type`` already reads "SDK main [<model>]" — keep the
        # phrase focused on the configured main-session model surface.
        phrase = (
            f"SDK main session (configured {stats.current_model}) runs "
            f"'{complexity}'-complexity work — consider {recommended_model}"
        )
    else:
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
            "routing_scope": routing_scope,
            SAVINGS_USD_KEY: savings,
            "current_cost_usd": current_cost,
        },
    )


def _detect_mismatch(
    stats: AgentStats,
    routing_scope: str = "subagent",
) -> DiagnosticSignal | None:
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

    # Both gates resolve their target through the shared selector (#170);
    # it returns None when the target tier equals the current tier, so
    # the same-as-current guard lives in one place. Overspec aims at the
    # complexity-matched tier (simple → Haiku); underspec deliberately
    # steps just one tier up to Sonnet rather than the complexity-matched
    # Opus — a conservative, cheaper recommendation.
    if complexity == "simple" and current_tier in ("moderate", "complex"):
        recommended = select_target_model(stats.current_model, "simple")
        if recommended is not None:
            return _build_mismatch_signal(
                stats, "overspec", complexity, recommended_model=recommended,
                routing_scope=routing_scope,
            )
    if (
        complexity == "complex"
        and current_tier == "simple"
        and stats.error_rate > _UNDERSPEC_ERROR_RATE_GATE
    ):
        recommended = select_target_model(stats.current_model, "moderate")
        if recommended is not None:
            return _build_mismatch_signal(
                stats, "underspec", complexity, recommended_model=recommended,
                routing_scope=routing_scope,
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


# ``<synthetic>`` is Claude Code's ghost-response model marker (zero-usage
# filler with no API round-trip, #507). Excluded from main-session turns so
# it never inflates the turn count or dilutes the per-turn token mean.
_SYNTHETIC_MODEL = "<synthetic>"


def _turn_work_tokens(usage: Usage) -> int:
    """Per-turn "new work" tokens: ``input + cache_creation + output``.

    All three are new processing this turn — uncached input, input written to
    the cache for the first time (full-price, billed 1.25×), and generation.
    ``cache_read_input_tokens`` is deliberately EXCLUDED: it is previously-
    processed context re-fed from cache (billed 0.1×), not new work. That
    exclusion is load-bearing for main sessions specifically — each turn
    re-reads the entire accumulated context, so cache reads grow unboundedly
    over a session and would trip the ``_COMPLEX_MIN_TOKENS`` threshold on
    every turn, masking overspec. Cache *creation* has no such pathology
    (mostly the turn-1 prompt write, then small deltas) and is genuine input
    processing, so it counts.
    """
    return (
        usage.input_tokens
        + usage.cache_creation_input_tokens
        + usage.output_tokens
    )


def _build_main_session_stats(session: SessionAnalysis) -> AgentStats | None:
    """Synthesize an ``AgentStats`` for an SDK main session's own turns (#112).

    The main session is not an ``AgentInvocation``; its "current model" is the
    configured ``ClaudeAgentOptions.model`` surfaced on each main-thread
    assistant message's ``message.model`` (findings §3). Metrics use a
    **per-turn** unit, never whole-session sums: the complexity thresholds
    (``_COMPLEX_MIN_TOKENS`` etc.) were tuned on per-invocation subagent
    figures, so summing a whole session would push every main session to
    ``complex`` and the primary overspec story would never fire (architect
    review Q2/C3). ``invocation_count`` is the main session's model-turn count,
    which the ≥3 gate in ``_detect_mismatch`` reads (architect Q5). Per-turn
    tokens use ``_turn_work_tokens`` (see its note on the cache-read exclusion).
    Returns ``None`` when the session has no non-synthetic assistant turn
    carrying a model.
    """
    turns = [
        m
        for m in session.messages
        if m.type == "assistant" and m.model and m.model != _SYNTHETIC_MODEL
    ]
    if not turns:
        return None
    # Configured main-session model. Real SDK main sessions are homogeneous
    # (findings §3), so the mode is the configured ``ClaudeAgentOptions.model``;
    # ``most_common`` is robust to a stray divergent line without asserting it.
    current_model = Counter(m.model for m in turns if m.model).most_common(1)[0][0]

    per_turn_tokens = [
        _turn_work_tokens(m.usage) for m in turns if m.usage is not None
    ]
    per_turn_tool_calls = [len(m.tool_use_blocks) for m in turns]
    total_tool_calls = sum(per_turn_tool_calls)
    # Main-thread tool errors: ``is_error`` tool_result blocks over total tool
    # calls. Best-effort — feeds only the underspec gate (overspec, the primary
    # story, doesn't read error_rate).
    error_count = sum(
        1
        for m in session.messages
        for b in m.content_blocks
        if b.type == "tool_result" and b.is_error
    )
    error_rate = error_count / total_tool_calls if total_tool_calls else 0.0

    return AgentStats(
        agent_type=f"SDK main [{current_model}]",
        invocation_count=len(turns),
        mean_tool_calls=(
            sum(per_turn_tool_calls) / len(per_turn_tool_calls)
            if per_turn_tool_calls
            else 0.0
        ),
        mean_tokens=(
            sum(per_turn_tokens) / len(per_turn_tokens) if per_turn_tokens else 0.0
        ),
        error_rate=error_rate,
        has_write_tools=False,
        current_model=current_model,
    )


def extract_sdk_main_session_signals(
    sessions: list[SessionAnalysis],
) -> list[DiagnosticSignal]:
    """Emit MODEL_MISMATCH signals for Agent SDK **main** sessions (#112).

    Gated to ``session_kind == "sdk"`` — Claude Code interactive (``"cli"``)
    and indeterminate (``"unknown"``) main sessions are skipped, honoring the
    D013 boundary (AC#7): a human-driven main session is CodeFluent's scope,
    not a configured agent. Each qualifying session contributes at most one
    signal, scoped ``routing_scope="main_session"`` and keyed per configured
    model (via ``agent_type="SDK main [<model>]"``) so the aggregator merges
    same-model sessions but never blends distinct configured models (C2).
    """
    signals: list[DiagnosticSignal] = []
    for session in sessions:
        if session.session_kind != "sdk":
            continue
        stats = _build_main_session_stats(session)
        if stats is None:
            continue
        signal = _detect_mismatch(stats, routing_scope="main_session")
        if signal is not None:
            signals.append(signal)
    return signals
