"""Shared complexity classification + model recommendation.

Single source of truth for "what model fits this workload" — consumed
by both ``diagnostics/model_routing.py`` (classifying real agents from
their declared config) and ``diagnostics/delegation.py`` (classifying
proposed delegation clusters and parent-thread offload candidates).

Pre-#185, the two paths diverged: model_routing classified agents into
``simple/moderate/complex`` tiers using observed metrics, while
delegation used a separate three-branch heuristic based on tools +
mean tokens. They could disagree on the same workload — see #185 for
the example that motivated consolidation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel

from agentfluent.agents.models import WRITE_TOOLS
from agentfluent.diagnostics.signals import iter_error_matches

if TYPE_CHECKING:
    from agentfluent.agents.models import AgentInvocation


ComplexityTier = Literal["simple", "moderate", "complex"]


# Model recommendation per tier — the one mapping both consumers use.
# Kept undated to match the pricing module's _ALIASES resolution.
MODEL_HAIKU = "claude-haiku-4-5"
MODEL_SONNET = "claude-sonnet-4-6"
MODEL_OPUS = "claude-opus-4-7"


# Complexity thresholds. Initial values per backlog E5-S1 guidance.
_SIMPLE_MAX_TOOL_CALLS = 5
_SIMPLE_MAX_TOKENS = 2_000
_COMPLEX_MIN_TOOL_CALLS = 10
_COMPLEX_MIN_TOKENS = 5_000
_COMPLEX_MIN_ERROR_RATE = 0.20

_TIER_TO_MODEL: dict[ComplexityTier, str] = {
    "simple": MODEL_HAIKU,
    "moderate": MODEL_SONNET,
    "complex": MODEL_OPUS,
}

# Tiers ordered cheapest/fastest → most capable. The single source for
# "what's one tier faster/slower" (``faster_tier``) — an explicit tuple
# rather than relying on ``_TIER_TO_MODEL`` dict ordering.
_TIER_ORDER: tuple[ComplexityTier, ...] = ("simple", "moderate", "complex")

# Complexity tier by Claude model family. Model IDs follow the pattern
# `claude-<family>-<version>[-<date>]`, so a prefix match covers both
# short aliases (`claude-opus-4-7`) and dated pinned forms
# (`claude-haiku-4-5-20251001`) that subagent traces record at runtime.
# Lives here (not in the pricing module) so all model↔tier knowledge is
# co-located with the tier→model mapping above (#170).
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


class AgentStats(BaseModel):
    """Aggregated stats consumed by complexity classification.

    Used in two contexts:

    - ``model_routing.aggregate_agent_stats`` builds one per real
      ``agent_type`` from observed invocations — ``current_model`` is
      the declared/inferred model that gets compared against the
      classified tier for mismatch detection.
    - ``aggregate_cluster_stats`` (this module) builds one for a
      synthetic group (a delegation cluster, a parent-thread offload
      cluster) — ``current_model`` is ``None`` because synthetic
      groups have no declared model to mismatch against.
    """

    agent_type: str
    invocation_count: int
    mean_tool_calls: float
    mean_tokens: float
    error_rate: float
    has_write_tools: bool
    current_model: str | None


def compute_error_rate(inv: AgentInvocation) -> float:
    """Observed error rate for a single invocation.

    Trace is preferred when linked — counts concrete tool errors.
    Metadata fallback counts ``ERROR_REGEX`` matches in the leading
    window of ``output_text`` (via ``iter_error_matches``) and divides
    by ``tool_uses``. Returns 0.0 when no signal is available either
    way. The window bound is the FP defense for long agent outputs
    that discuss error-handling code as a topic (#281).
    """
    trace = inv.trace
    if trace is not None and trace.tool_calls:
        return trace.total_errors / len(trace.tool_calls)
    tool_uses = inv.tool_uses or 0
    if tool_uses == 0 or not inv.output_text:
        return 0.0
    matches = sum(1 for _ in iter_error_matches(inv.output_text))
    return matches / tool_uses


def has_write_tools_in_trace(inv: AgentInvocation) -> bool:
    trace = inv.trace
    if trace is None:
        return False
    return bool(trace.unique_tool_names & WRITE_TOOLS)


def classify_complexity(stats: AgentStats) -> ComplexityTier:
    """Bin observed behavior into simple / moderate / complex.

    ``has_write_tools`` is intentionally NOT a classification input. Per
    #185 architect review, write-tool presence alone (without high
    token volume or tool-call count) must not escalate to ``complex`` —
    that's the over-recommendation pattern #185 was filed to fix. The
    field is retained on ``AgentStats`` as observation metadata that
    other diagnostics (or future signals) can consume; classification
    is driven entirely by token volume, tool-call count, and error rate.
    """
    if (
        stats.mean_tool_calls > _COMPLEX_MIN_TOOL_CALLS
        or stats.mean_tokens > _COMPLEX_MIN_TOKENS
        or stats.error_rate > _COMPLEX_MIN_ERROR_RATE
    ):
        return "complex"
    # The "simple" branch requires evidence of light work, not absence
    # of data. A cluster with no observed tool calls AND no token data
    # falls through to "moderate" so the recommendation reflects
    # uncertainty rather than asserting the work is small.
    has_observed_data = stats.mean_tool_calls > 0 or stats.mean_tokens > 0
    if (
        has_observed_data
        and stats.mean_tool_calls < _SIMPLE_MAX_TOOL_CALLS
        and stats.mean_tokens < _SIMPLE_MAX_TOKENS
    ):
        return "simple"
    return "moderate"


def recommend_model_for_complexity(tier: ComplexityTier) -> str:
    """Map a complexity tier to its recommended model id."""
    return _TIER_TO_MODEL[tier]


def select_target_model(
    current_model: str | None,
    target_tier: ComplexityTier,
) -> str | None:
    """Pick a concrete target model for ``target_tier`` — the shared
    "pick a target model for this agent" helper (#170).

    Both ``target: model`` recommendation paths (duration-outlier and
    complexity-mismatch) route through this so they produce concrete,
    deduplicated suggestions from one place. Returns the tier-matched
    model id, or ``None`` when there's no actionable switch:

    - ``current_model`` is unknown (caller has no model to name), or
    - the current model already classifies into ``target_tier`` — the
      "suggested target == current → no recommendation" guard (AC3).
    """
    if not current_model:
        return None
    if classify_model_tier(current_model) == target_tier:
        return None
    return recommend_model_for_complexity(target_tier)


def faster_tier(current_model: str | None) -> ComplexityTier | None:
    """The complexity tier one step faster/cheaper than ``current_model``.

    Returns ``None`` when the model is unknown or already at the fastest
    tier (Haiku) — callers fall back to a non-model recommendation in
    that case.
    """
    if not current_model:
        return None
    idx = _TIER_ORDER.index(classify_model_tier(current_model))
    if idx == 0:
        return None
    return _TIER_ORDER[idx - 1]


def aggregate_cluster_stats(
    invocations: list[AgentInvocation],
    *,
    tools: list[str] | None = None,
    label: str = "<cluster>",
) -> AgentStats:
    """Build ``AgentStats`` from a synthetic group (e.g., a delegation cluster).

    Mirrors ``model_routing.aggregate_agent_stats`` but treats the input
    as a single group regardless of ``agent_type``. ``current_model``
    is always ``None`` for synthetic groups (no declared config to
    mismatch against).

    Pass ``tools`` when the caller has already collected the union
    (e.g. delegation's ``_collect_tools_from_traces``) — saves
    re-walking traces; ``has_write_tools`` is derived from the union.
    Otherwise it's derived from each member's trace.
    """
    if tools is not None:
        has_writes = bool(set(tools) & WRITE_TOOLS)
    else:
        has_writes = any(has_write_tools_in_trace(i) for i in invocations)

    tool_use_values = [i.tool_uses for i in invocations if i.tool_uses is not None]
    token_values = [i.total_tokens for i in invocations if i.total_tokens is not None]
    error_rates = [compute_error_rate(i) for i in invocations]

    return AgentStats(
        agent_type=label,
        invocation_count=len(invocations),
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
        current_model=None,
    )
