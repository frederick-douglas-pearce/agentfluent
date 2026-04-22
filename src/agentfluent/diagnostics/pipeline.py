"""Diagnostics orchestration.

Owns the `run_diagnostics` pipeline: extracts metadata-level and
trace-level signals, deduplicates overlapping metadata error signals
when strictly-more-informative trace signals cover the same
`agent_type`, runs correlation against any available agent configs,
computes the parsed-trace count, and (when scikit-learn is installed)
proposes draft subagent definitions from clustered general-purpose
delegations.

Kept separate from `analytics/pipeline.py` to keep the analytics layer
free of diagnostics imports and to match the one-file-per-concern
pattern used elsewhere.
"""

from __future__ import annotations

import logging

from agentfluent.agents.models import AgentInvocation
from agentfluent.config.models import AgentConfig
from agentfluent.config.scanner import scan_agents
from agentfluent.diagnostics.correlator import correlate
from agentfluent.diagnostics.delegation import (
    DEFAULT_MIN_CLUSTER_SIZE,
    DEFAULT_MIN_SIMILARITY,
    SKLEARN_AVAILABLE,
    suggest_delegations,
)
from agentfluent.diagnostics.model_routing import extract_model_routing_signals
from agentfluent.diagnostics.models import (
    DelegationSuggestion,
    DiagnosticSignal,
    DiagnosticsResult,
    SignalType,
)
from agentfluent.diagnostics.signals import extract_signals
from agentfluent.diagnostics.trace_signals import extract_trace_signals

logger = logging.getLogger(__name__)

# Signal types emitted by the trace-level extractor. Used by the dedup
# pass to identify agent_types whose metadata ERROR_PATTERN signals can
# be safely suppressed in favor of more-specific trace evidence.
TRACE_SIGNAL_TYPES: frozenset[SignalType] = frozenset(
    {
        SignalType.TOOL_ERROR_SEQUENCE,
        SignalType.RETRY_LOOP,
        SignalType.PERMISSION_FAILURE,
        SignalType.STUCK_PATTERN,
    },
)


def _append_mismatch_phrase(
    dedup_note: str, signal: DiagnosticSignal,
) -> str:
    """Extend a `dedup_note` with the human-readable model-mismatch summary.

    Format mirrors `ModelRoutingRule.recommend`'s action text so the
    user sees the same phrasing across the "Suggested Subagents" and
    "Recommendations" surfaces. Omits the savings clause when pricing
    is unavailable. The original dedup prefix ("suppressed — already
    covered by ... (similarity ...)") is preserved intact so existing
    CLI parsing and assertions still hold.
    """
    detail = signal.detail
    matched_name = str(detail.get("current_model", ""))
    recommended = str(detail.get("recommended_model", ""))
    mismatch_type = str(detail.get("mismatch_type", ""))
    savings = detail.get("estimated_savings_usd")
    invocation_count = detail.get("invocation_count", 0)

    clauses = [
        f"Note: '{signal.agent_type}' is {mismatch_type}'d on {matched_name}",
        f"consider switching to {recommended}",
    ]
    if mismatch_type == "overspec" and isinstance(savings, int | float):
        clauses.append(
            f"est. savings ${savings:.2f} across {invocation_count} invocations",
        )
    # Strip trailing terminal punctuation from the incoming note so the
    # ". " separator produces a well-formed sentence regardless of the
    # dedup-note format's current or future shape.
    prefix = dedup_note.rstrip(" .;")
    return f"{prefix}. {'; '.join(clauses)}."


def _enrich_dedup_with_mismatches(
    suggestions: list[DelegationSuggestion],
    signals: list[DiagnosticSignal],
) -> None:
    """Append model-mismatch context to deduped suggestions in place.

    When a ``DelegationSuggestion`` was suppressed (``matched_agent``
    set) because it overlaps an existing agent, and that agent also
    has a live ``MODEL_MISMATCH`` signal, extend the suggestion's
    ``dedup_note`` with the mismatch summary so the user sees both
    facts in one place. Non-deduped suggestions and non-mismatch
    signals are ignored.

    Agent names are matched case-insensitively so frontmatter casing
    (``PM`` in the draft vs ``pm`` in the signal) does not defeat the
    cross-reference.
    """
    if not suggestions:
        return
    mismatches_by_agent: dict[str, DiagnosticSignal] = {
        s.agent_type.lower(): s
        for s in signals
        if s.signal_type == SignalType.MODEL_MISMATCH
    }
    if not mismatches_by_agent:
        return
    for sug in suggestions:
        if not sug.matched_agent:
            continue
        mismatch = mismatches_by_agent.get(sug.matched_agent.lower())
        if mismatch is None:
            continue
        sug.dedup_note = _append_mismatch_phrase(sug.dedup_note, mismatch)


def _dedup_error_patterns(signals: list[DiagnosticSignal]) -> list[DiagnosticSignal]:
    """Drop metadata ERROR_PATTERN signals for agent_types that already
    have at least one trace-level signal.

    Trace signals carry specific evidence (which tool, which call index,
    which keyword) and specific remediation; metadata ERROR_PATTERN is a
    best-effort keyword scan of the final output. Showing both for the
    same agent would produce two recommendations for the same underlying
    issue — one vague, one precise.

    Only `ERROR_PATTERN` is suppressed. `TOKEN_OUTLIER` and
    `DURATION_OUTLIER` measure different axes and are left alone.
    """
    trace_agent_types = {
        s.agent_type for s in signals if s.signal_type in TRACE_SIGNAL_TYPES
    }
    if not trace_agent_types:
        return signals
    return [
        s for s in signals
        if not (
            s.signal_type == SignalType.ERROR_PATTERN
            and s.agent_type in trace_agent_types
        )
    ]


def run_diagnostics(
    invocations: list[AgentInvocation],
    *,
    min_cluster_size: int = DEFAULT_MIN_CLUSTER_SIZE,
    min_similarity: float = DEFAULT_MIN_SIMILARITY,
) -> DiagnosticsResult:
    """Run the full diagnostics pipeline on agent invocations.

    Extracts metadata + trace-level signals, dedups overlapping metadata
    error signals, scans for agent config files, correlates signals
    with config, produces recommendations, and (when scikit-learn is
    installed) proposes draft subagent definitions from clustered
    general-purpose delegations. ``subagent_trace_count`` on the result
    reflects traces that successfully parsed and linked, not the raw
    filesystem enumeration.
    """
    signals = extract_signals(invocations)

    # Fold in trace-level signals for any invocation with an attached
    # subagent trace. Passing agent_type explicitly avoids depending on
    # the linker having populated trace.agent_type.
    for inv in invocations:
        if inv.trace is None:
            continue
        signals.extend(extract_trace_signals(inv.trace, agent_type=inv.agent_type))

    signals = _dedup_error_patterns(signals)

    agent_configs: list[AgentConfig] = []
    try:
        agent_configs = list(scan_agents("all"))
    except OSError:
        logger.debug("Could not scan agent config files", exc_info=True)

    configs_by_name = (
        {c.name.lower(): c for c in agent_configs} if agent_configs else None
    )

    # Aggregate-level signals (model-routing) use the same config lookup
    # the correlator will read from; fold them in before correlation.
    signals.extend(extract_model_routing_signals(invocations, configs_by_name))

    recommendations = correlate(signals, configs_by_name)

    subagent_trace_count = sum(1 for inv in invocations if inv.trace is not None)

    delegation_suggestions: list[DelegationSuggestion] = []
    if SKLEARN_AVAILABLE:
        delegation_suggestions = suggest_delegations(
            invocations,
            existing_configs=agent_configs or None,
            min_cluster_size=min_cluster_size,
            min_similarity=min_similarity,
        )
        _enrich_dedup_with_mismatches(delegation_suggestions, signals)
    else:
        logger.debug(
            "Delegation clustering skipped: scikit-learn not installed. "
            "Install agentfluent[clustering] to enable.",
        )

    return DiagnosticsResult(
        signals=signals,
        recommendations=recommendations,
        subagent_trace_count=subagent_trace_count,
        delegation_suggestions=delegation_suggestions,
    )
