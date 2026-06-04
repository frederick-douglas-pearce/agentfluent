"""Tool-orchestration-chain diagnostics (Tier A, metadata-only).

Detects agent invocations that orchestrate long sequential tool-call
chains with large intermediate payloads -- a pattern where each
``tool_result`` enters the context window even though the agent only
needs the final output. Programmatic Tool Calling
(``allowed_callers: ["code_execution_20250825"]``) addresses this by
running the orchestration in a code-execution sandbox where intermediate
results stay out of the context window. Anthropic reports a 37% token
reduction on complex research tasks (see the Advanced Tool Use article).

**Tier A scope (#406).** This is a coarse, metadata-only proxy built
from the parent session's ``toolUseResult`` fields
(``AgentInvocation.tool_uses`` and ``.total_tokens``). It fires on
invocations with a high tool-call count *and* a high per-call token
average, aggregated to 3+ matching invocations per agent type. Tier B
(trace-enhanced, per-call inspection) is a deferred follow-up.

**Known precision risk.** The proxy cannot distinguish true orchestration
chains (intermediate results consumed and discarded) from agents that
genuinely need each intermediate result in context for reasoning. Severity
is therefore ``INFO``, not ``WARNING``, and every emitted signal carries an
explicit low-confidence caveat (``_LOW_CONFIDENCE_CAVEAT``).

**Calibration (v0.9, #407).** On the agentfluent + codefluent dogfood
corpora (195 matching invocations; seeded stratified sample of 30) the
signal scored **0% precision (0/30 true positives)**. The corpus contained
only reasoning/review/scoping subagents (architect, general-purpose, pm,
explore) whose intermediate results are genuinely consumed -- it had no
agents running real orchestration chains. The root cause is structural:
``tokens_per_tool_use`` divides whole-invocation token burn (large cached
context re-sent each turn + reasoning output) by tool count, not
intermediate-*result* size, so no threshold band separates the two (every
band emits the same reasoning agents). The signal nonetheless ships **live
with the caveat** (decision 2026-06-02): the 0% is a corpus artifact, and
the detection surface is retained for future corpora that include real
orchestration-chain agents. Trace-level detection (Tier B: summed
tool-result tokens vs. final-output size) is the precision fix; D035 (LLM
relevance classifier) is the complement -- the ~100% rule-based FP rate is
the measured baseline it would improve upon. See
``.claude/specs/analysis/407-calibration/`` for the rubric, classification,
and tuning simulation.

**Thresholds.** The three constants below are the PRD's starting defaults.
The #407 calibration confirmed no threshold band improves precision on the
current corpus, so they are unchanged pending Tier B.
"""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

from agentfluent.config.models import Severity
from agentfluent.diagnostics.models import DiagnosticSignal, SignalType

if TYPE_CHECKING:
    from agentfluent.agents.models import AgentInvocation

__all__ = [
    "ESTIMATED_TOKEN_SAVINGS_KEY",
    "TOKEN_REDUCTION_FACTOR",
    "extract_tool_orchestration_signals",
]

# Per-invocation match predicate. An invocation is part of an
# orchestration chain when it makes many tool calls AND each call carries
# a large average token payload (the proxy for "big intermediate results
# passing through context").
_MIN_TOOL_CALLS = 10
_MIN_TOKENS_PER_TOOL_CALL = 2000.0

# Aggregate gate: require the pattern across multiple invocations of the
# same agent type before emitting, mirroring MODEL_MISMATCH's
# `_MIN_INVOCATIONS_FOR_ANALYSIS`. A single unusual invocation isn't
# enough signal to recommend a config change (OQ1 in the PRD).
_MIN_MATCHING_INVOCATIONS = 3

# Estimated token savings from migrating to Programmatic Tool Calling,
# anchored on Anthropic's reported 37% reduction (43,588 -> 27,297) on
# complex research tasks. Applied to the summed tokens of the matching
# invocations as a rough projection, surfaced in the recommendation.
TOKEN_REDUCTION_FACTOR = 0.37

# Appended to every emitted signal's message. The #407 calibration measured
# 0% precision on the dogfood corpus (a metadata-only proxy can't tell a real
# orchestration chain from a token-heavy reasoning agent), so the signal is
# shipped live but flagged low-confidence rather than presented as a finding.
# Removed once trace-level (Tier B) detection lifts precision. Kept as a
# module constant so it's greppable and assertable in tests.
_LOW_CONFIDENCE_CAVEAT = (
    "Low-confidence heuristic: this metadata-only proxy cannot yet distinguish "
    "genuine orchestration chains from token-heavy reasoning agents, so verify "
    "against the agent's trace before acting on this."
)

# Producer/consumer contract: this module stores the projected token
# savings under this key in the signal's ``detail`` dict;
# ``correlator.ToolOrchestrationRule`` reads it to build the action text.
# Distinct from MODEL_MISMATCH's ``estimated_savings_usd`` -- this is a
# token count, not a dollar figure.
ESTIMATED_TOKEN_SAVINGS_KEY = "estimated_token_savings"


def _is_orchestration_chain(inv: AgentInvocation) -> bool:
    """Whether a single invocation matches the Tier A chain predicate.

    Requires both metadata fields and a non-zero tool count; invocations
    missing ``tool_uses`` or ``total_tokens`` (older sessions, interrupted
    runs) can't be classified and don't match.
    """
    if inv.tool_uses is None or inv.tool_uses < _MIN_TOOL_CALLS:
        return False
    ratio = inv.tokens_per_tool_use
    return ratio is not None and ratio > _MIN_TOKENS_PER_TOOL_CALL


def _build_signal(
    agent_type: str,
    matching: list[AgentInvocation],
) -> DiagnosticSignal:
    """Build one aggregate ``TOOL_ORCHESTRATION_CHAIN`` signal.

    Sums tool calls and tokens across the matching invocations so the
    recommendation can quote concrete totals and a blended per-call
    ratio. ``estimated_token_savings`` projects the article's 37%
    reduction onto the summed tokens.
    """
    total_tool_calls = sum(i.tool_uses or 0 for i in matching)
    total_tokens = sum(i.total_tokens or 0 for i in matching)
    invocation_count = len(matching)
    ratio = total_tokens / total_tool_calls if total_tool_calls else 0.0
    estimated_savings = int(total_tokens * TOKEN_REDUCTION_FACTOR)

    message = (
        f"Agent '{agent_type}' made {total_tool_calls} tool calls consuming "
        f"{total_tokens:,} tokens across {invocation_count} invocations. "
        f"Average token cost per tool call: {ratio:,.0f} tokens. "
        f"{_LOW_CONFIDENCE_CAVEAT}"
    )
    return DiagnosticSignal(
        signal_type=SignalType.TOOL_ORCHESTRATION_CHAIN,
        severity=Severity.INFO,
        agent_type=agent_type,
        message=message,
        detail={
            "invocation_count": invocation_count,
            "total_tool_calls": total_tool_calls,
            "total_tokens": total_tokens,
            "mean_tokens_per_tool_call": ratio,
            ESTIMATED_TOKEN_SAVINGS_KEY: estimated_savings,
        },
    )


def extract_tool_orchestration_signals(
    invocations: list[AgentInvocation],
) -> list[DiagnosticSignal]:
    """Public entry: group by agent type, gate, emit one signal per type.

    Each invocation is tested against the per-invocation chain predicate
    (``_is_orchestration_chain``). For an agent type with
    ``_MIN_MATCHING_INVOCATIONS`` or more matching invocations, a single
    aggregated ``INFO`` signal is emitted carrying summed evidence.
    Returns an empty list when no agent type clears the gate.
    """
    if not invocations:
        return []

    # Group case-insensitively (real data varies: "PM" vs "pm") so case
    # variants don't split across the min-invocation gate. The canonical
    # display name is the first-seen casing; the correlator lowercases
    # again for its config lookup.
    matching_by_type: dict[str, list[AgentInvocation]] = defaultdict(list)
    for inv in invocations:
        if _is_orchestration_chain(inv):
            matching_by_type[inv.agent_type.lower()].append(inv)

    signals: list[DiagnosticSignal] = []
    for matching in matching_by_type.values():
        if len(matching) >= _MIN_MATCHING_INVOCATIONS:
            signals.append(_build_signal(matching[0].agent_type, matching))
    return signals
