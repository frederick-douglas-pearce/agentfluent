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

from pydantic import BaseModel, Field

from agentfluent.analytics.pricing import compute_cost, get_pricing
from agentfluent.config.models import Severity
from agentfluent.diagnostics.delegation import MODEL_HAIKU, MODEL_OPUS, MODEL_SONNET
from agentfluent.diagnostics.models import DiagnosticSignal, SignalType
from agentfluent.diagnostics.signals import ERROR_REGEX

if TYPE_CHECKING:
    from agentfluent.agents.models import AgentInvocation
    from agentfluent.config.models import AgentConfig

logger = logging.getLogger(__name__)


ComplexityTier = Literal["simple", "moderate", "complex"]
MismatchType = Literal["overspec", "underspec"]


# Thresholds — initial values per backlog E5-S1 guidance.
# Empirical calibration against real project data is tracked in #140.
_MIN_INVOCATIONS_FOR_ANALYSIS = 3
_SIMPLE_MAX_TOOL_CALLS = 5
_SIMPLE_MAX_TOKENS = 2_000
_COMPLEX_MIN_TOOL_CALLS = 10
_COMPLEX_MIN_TOKENS = 5_000
_COMPLEX_MIN_ERROR_RATE = 0.20

# Tool tiers — mirrored from delegation.py's classification. Kept as
# local copies so model_routing doesn't depend on private helpers in
# delegation; the read-only set is the meaningful contract.
_READ_ONLY_TOOLS = frozenset(
    {"Read", "Grep", "Glob", "WebFetch", "WebSearch", "LS"},
)
_WRITE_TOOLS = frozenset({"Write", "Edit", "Bash", "NotebookEdit"})


# Mapping: declared model → complexity tier the model is suited for.
# Older/unknown aliases fall through to "moderate" via .get().
MODEL_TIER_MAP: dict[str, ComplexityTier] = {
    MODEL_HAIKU: "simple",
    MODEL_SONNET: "moderate",
    MODEL_OPUS: "complex",
}


class AgentStats(BaseModel):
    """Per-agent-type aggregated stats consumed by model-routing detection."""

    agent_type: str
    invocation_count: int
    mean_tool_calls: float
    mean_tokens: float
    error_rate: float
    has_write_tools: bool
    current_model: str | None
    """Declared model from `AgentConfig.model` — None if the agent has
    no config or the config doesn't set a model. MVP skips None."""
    observed_tools: set[str] = Field(default_factory=set)


def _compute_error_rate(inv: AgentInvocation) -> float:
    """Observed error rate for a single invocation.

    Trace is preferred when linked — it counts concrete tool errors.
    Metadata fallback scans ``output_text`` for the ERROR_REGEX keyword
    set and divides by ``tool_uses``. Returns 0.0 when no signal is
    available either way.
    """
    trace = inv.trace
    if trace is not None and trace.tool_calls:
        return trace.total_errors / len(trace.tool_calls)
    tool_uses = inv.tool_uses or 0
    if tool_uses == 0 or not inv.output_text:
        return 0.0
    matches = len(ERROR_REGEX.findall(inv.output_text))
    return matches / tool_uses


def _has_write_tools_in_trace(inv: AgentInvocation) -> bool:
    trace = inv.trace
    if trace is None:
        return False
    return bool(trace.unique_tool_names & _WRITE_TOOLS)


def _invocation_tools(inv: AgentInvocation) -> set[str]:
    if inv.trace is None:
        return set()
    return set(inv.trace.unique_tool_names)


def aggregate_agent_stats(
    invocations: list[AgentInvocation],
    configs: dict[str, AgentConfig] | None,
) -> dict[str, AgentStats]:
    """Roll up per-invocation metrics into per-agent-type aggregates.

    Keyed by lowercased agent_type to match the correlator's config
    lookup contract. ``current_model`` is sourced from the matching
    ``AgentConfig.model`` (MVP); invocations with no config map to
    ``None`` and are skipped downstream.
    """
    groups: dict[str, list[AgentInvocation]] = defaultdict(list)
    for inv in invocations:
        groups[inv.agent_type.lower()].append(inv)

    stats_by_type: dict[str, AgentStats] = {}
    for key, group in groups.items():
        canonical_name = group[0].agent_type
        tool_use_values = [i.tool_uses for i in group if i.tool_uses is not None]
        token_values = [i.total_tokens for i in group if i.total_tokens is not None]
        error_rates = [_compute_error_rate(i) for i in group]
        has_writes = any(_has_write_tools_in_trace(i) for i in group)
        observed: set[str] = set()
        for inv in group:
            observed.update(_invocation_tools(inv))

        config = configs.get(key) if configs else None
        current_model = config.model if (config and config.model) else None

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
            observed_tools=observed,
        )
    return stats_by_type


def classify_complexity(stats: AgentStats) -> ComplexityTier:
    """Bin an agent's observed behavior into simple / moderate / complex."""
    if (
        stats.has_write_tools
        or stats.mean_tool_calls > _COMPLEX_MIN_TOOL_CALLS
        or stats.mean_tokens > _COMPLEX_MIN_TOKENS
        or stats.error_rate > _COMPLEX_MIN_ERROR_RATE
    ):
        return "complex"
    if (
        stats.mean_tool_calls < _SIMPLE_MAX_TOOL_CALLS
        and stats.mean_tokens < _SIMPLE_MAX_TOKENS
    ):
        return "simple"
    return "moderate"


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
    savings, current_cost = _compute_savings(stats, recommended_model)
    phrase = (
        f"complexity '{complexity}' agent '{stats.agent_type}' runs on "
        f"{stats.current_model} — consider {recommended_model}"
    )
    if mismatch_type == "overspec":
        message = f"Overspec'd model: {phrase}."
    else:
        message = f"Underspec'd model: {phrase}."
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
    current_tier = MODEL_TIER_MAP.get(stats.current_model, "moderate")
    complexity = classify_complexity(stats)

    if complexity == "simple" and current_tier in ("moderate", "complex"):
        return _build_mismatch_signal(
            stats, "overspec", complexity, recommended_model=MODEL_HAIKU,
        )
    if (
        complexity == "complex"
        and current_tier == "simple"
        and stats.error_rate > _COMPLEX_MIN_ERROR_RATE
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
