"""Model-routing diagnostics: complexity classification + mismatch detection.

Aggregates per-agent-type statistics from observed invocations, classifies
each agent's task complexity (simple / moderate / complex), and emits a
``MODEL_MISMATCH`` signal when the declared model tier is wrong for the
complexity. Downstream, ``ModelRoutingRule`` in ``correlator.py`` turns
the signal into an actionable ``target="model"`` recommendation with a
cost-savings estimate (when pricing is available).

**Model resolution.** An agent type's model resolves from its
``.claude/agents/X.md`` frontmatter ``model:``, else a linked trace's model,
else the parent tool-result's ``resolved_model`` (``_resolve_current_model``);
types with none of these are skipped. SDK main sessions are routed
separately (``extract_sdk_main_session_signals``).

**Cost source (#675).** Dollar savings price observed spend, never the
``total_tokens`` context-size proxy: a subagent's linked trace's deduplicated
per-turn usage (``analytics.tokens.trace_spend_usage``) and, for an SDK main
session, the turns the parent token fold counts -- each at the model it ran on
and at the alternative model, through ``analytics.tokens.price_usage``. An
invocation without trace spend contributes no dollars; it is never estimated.
Both sides price at the latest rate: savings is a forward-looking figure.

**Threshold calibration.** The complexity thresholds below are informed
by the same backlog guidance that drove #110's defaults. Empirical
tuning against real session data is tracked in #140; that issue covers
delegation-clustering thresholds and is extended by #111's needs.
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from agentfluent.analytics.tokens import (
    counts_as_parent_call,
    price_usage,
    trace_spend,
)
from agentfluent.config.models import Severity
from agentfluent.diagnostics._complexity import (
    MIN_INVOCATIONS_FOR_ANALYSIS,
    AgentStats,
    ComplexityTier,
    classify_complexity,
    classify_model_tier,
    compute_error_rate,
    effective_simple_inputs,
    has_write_tools_in_trace,
    select_target_model,
    simple_branch_inputs,
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
    "PRICED_INVOCATION_COUNT_KEY",
    "SAVINGS_USD_KEY",
    "AgentStats",
    "SavingsEstimate",
    "ComplexityTier",
    "MismatchType",
    "aggregate_agent_stats",
    "classify_complexity",
    "classify_model_tier",
    "estimate_model_savings",
    "estimate_usage_savings",
    "invocation_spend",
    "savings_scope_from_detail",
    "savings_scope_phrase",
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
# How many of the signal's ``invocation_count`` observations the dollar figure
# covers (#675). Shared by the two renderers of "across k of N invocations".
PRICED_INVOCATION_COUNT_KEY = "priced_invocation_count"

_MIN_INVOCATIONS_FOR_ANALYSIS = MIN_INVOCATIONS_FOR_ANALYSIS
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
            simple_inputs=simple_branch_inputs(group),
        )
    return stats_by_type


@dataclass(frozen=True)
class SavingsEstimate:
    """A model-switch savings figure over the observations it could price (#675)."""

    savings_usd: float
    """``max(0, current - alternative)``."""
    current_cost_usd: float
    """Observed spend of the priced observations at the models they ran on."""
    priced_invocation_count: int
    """How many observations both sides could price; never extrapolated to the rest."""


def invocation_spend(inv: AgentInvocation) -> tuple[Usage, str] | None:
    """An invocation's spend and the model it ran on, or ``None`` (#675).

    Spend is the linked trace's deduplicated per-turn usage
    (``analytics.tokens.trace_spend``) -- what the analytics fold and per-agent
    cost read (#646, #701). An invocation with no linked trace, or whose trace
    does not count as spend, has none.
    """
    return trace_spend(inv.trace)


def estimate_usage_savings(
    observations: Iterable[tuple[Usage, str]],
    alt_model: str,
) -> SavingsEstimate | None:
    """Price each ``(usage, model it ran on)`` at its own model and at ``alt_model``.

    An observation counts only when both rates exist; the rest are left out,
    never estimated. Returns ``None`` when nothing could be priced -- no dollar
    figure, not $0. Latest rate on both sides (``timestamp=None``); see
    ``analytics.tokens.price_usage`` for the counterfactual and what it ignores.
    """
    current_total = 0.0
    alt_total = 0.0
    priced = 0
    for usage, model in observations:
        current = price_usage(usage, model, timestamp=None)
        alternative = price_usage(usage, alt_model, timestamp=None)
        if current is None or alternative is None:
            continue
        current_total += current
        alt_total += alternative
        priced += 1
    if priced == 0:
        return None
    return SavingsEstimate(
        savings_usd=max(0.0, current_total - alt_total),
        current_cost_usd=current_total,
        priced_invocation_count=priced,
    )


def estimate_model_savings(
    invocations: Iterable[AgentInvocation],
    alt_model: str,
) -> SavingsEstimate | None:
    """Savings from switching these invocations to ``alt_model``, from trace spend.

    Sums over invocations with trace spend (``invocation_spend``), each priced at
    its trace's model -- what was actually spent -- not the group's declared
    model. Invocations without trace spend add nothing and are not extrapolated:
    ``priced_invocation_count`` says how many the figure covers.
    """
    spend = (invocation_spend(inv) for inv in invocations)
    return estimate_usage_savings(
        (observation for observation in spend if observation is not None), alt_model,
    )


def savings_scope_phrase(priced: int, total: int) -> str:
    """``"k of N invocations"`` when the figure covers fewer than all, else ``"N invocations"``.

    Shared by both renderers of a mismatch savings figure (``correlator.
    ModelRoutingRule`` and ``pipeline._append_mismatch_phrase``) so neither
    claims the whole group for a figure priced over part of it (#675).
    """
    if priced < total:
        return f"{priced} of {total} invocations"
    return f"{total} invocations"


def savings_scope_from_detail(detail: Mapping[str, object]) -> str:
    """``savings_scope_phrase`` read off a ``MODEL_MISMATCH`` signal's detail.

    A detail without ``priced_invocation_count`` (a signal built before #675)
    reads as covering every invocation, which is what that figure claimed.
    """
    total = detail.get("invocation_count", 0)
    total_count = total if isinstance(total, int) else 0
    priced = detail.get(PRICED_INVOCATION_COUNT_KEY, total_count)
    priced_count = priced if isinstance(priced, int) else total_count
    return savings_scope_phrase(priced_count, total_count)


def _build_mismatch_signal(
    stats: AgentStats,
    mismatch_type: MismatchType,
    complexity: ComplexityTier,
    recommended_model: str,
    spend: Sequence[tuple[Usage, str]],
    routing_scope: str = "subagent",
) -> DiagnosticSignal:
    # Savings are only meaningful for overspec (cheap recommended model).
    # For underspec, Haiku → Sonnet costs MORE; `max(0.0, ...)` would
    # always wipe it to zero anyway, so skip the pricing lookup.
    estimate = (
        estimate_usage_savings(spend, recommended_model)
        if mismatch_type == "overspec" else None
    )
    simple = effective_simple_inputs(stats)
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
            # What the "simple" branch actually read (#675): the group-wide
            # means above drive "complex"; these may cover fewer invocations.
            "simple_branch_mean_tool_calls": simple.mean_tool_calls,
            "simple_branch_mean_tokens": simple.mean_tokens,
            "simple_branch_invocation_count": simple.invocation_count,
            SAVINGS_USD_KEY: estimate.savings_usd if estimate else None,
            "current_cost_usd": estimate.current_cost_usd if estimate else None,
            PRICED_INVOCATION_COUNT_KEY: (
                estimate.priced_invocation_count if estimate else 0
            ),
        },
    )


def _detect_mismatch(
    stats: AgentStats,
    spend: Sequence[tuple[Usage, str]],
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
                spend=spend, routing_scope=routing_scope,
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
                spend=spend, routing_scope=routing_scope,
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
    spend_by_type: dict[str, list[tuple[Usage, str]]] = defaultdict(list)
    for inv in invocations:
        observation = invocation_spend(inv)
        if observation is not None:
            spend_by_type[inv.agent_type.lower()].append(observation)
    signals: list[DiagnosticSignal] = []
    for key, stats in stats_by_type.items():
        signal = _detect_mismatch(stats, spend_by_type.get(key, []))
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


def _main_session_spend(session: SessionAnalysis) -> list[tuple[Usage, str]]:
    """The main session's own spend: the turns the parent token fold counts (#675).

    ``counts_as_parent_call`` is ``compute_token_metrics``'s predicate (usage
    present, not ``<synthetic>``), so the savings figure prices exactly the turns
    analytics prices, each at its own ``message.model``. Classification inputs
    (``_build_main_session_stats``) are untouched.
    """
    return [
        (msg.usage, msg.model)
        for msg in session.messages
        if counts_as_parent_call(msg) and msg.usage is not None and msg.model
    ]


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
        signal = _detect_mismatch(
            stats, _main_session_spend(session), routing_scope="main_session",
        )
        if signal is not None:
            signals.append(signal)
    return signals
