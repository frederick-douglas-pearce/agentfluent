"""Shared complexity classification + model recommendation.

Single source of truth for "what model fits this workload" — consumed
by both ``diagnostics/model_routing.py`` (classifying real agents from
their declared config) and ``diagnostics/delegation.py`` (classifying
proposed delegation clusters and parent-thread offload candidates).
``config/scoring.py`` also reads ``classify_model_tier`` to decide which
models are "expensive" for its ``model_selection`` rubric (#680).

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
from agentfluent.analytics.pricing import (
    MODEL_HAIKU,
    MODEL_OPUS,
    MODEL_SONNET,
    resolve_model_alias,
)
from agentfluent.diagnostics.signals import iter_error_matches

if TYPE_CHECKING:
    from agentfluent.agents.models import AgentInvocation


ComplexityTier = Literal["simple", "moderate", "complex"]


# Model recommendation per tier — the one mapping both consumers use. The MODEL_HAIKU/
# SONNET/OPUS constants are defined in analytics.pricing (promoted there, #252 fold) and
# imported above; delegation.py / model_routing.py keep importing them from here, so the
# existing re-export chain stays green.


# Complexity thresholds. Initial values per backlog E5-S1 guidance.
_SIMPLE_MAX_TOOL_CALLS = 5
_SIMPLE_MAX_TOKENS = 2_000
_COMPLEX_MIN_TOOL_CALLS = 10
_COMPLEX_MIN_TOKENS = 5_000
_COMPLEX_MIN_ERROR_RATE = 0.20

# Minimum number of observations before a group is analyzed at all
# (``model_routing._detect_mismatch``) and, since #675, the minimum number of
# invocations carrying BOTH ``tool_uses`` and ``total_tokens`` before the
# "simple" branch may fire on them (``simple_branch_inputs``).
MIN_INVOCATIONS_FOR_ANALYSIS = 3

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
# undated ids (`claude-opus-4-7`) and dated pinned forms
# (`claude-haiku-4-5-20251001`) that subagent traces record at runtime.
# Bare names (`opus`, `fable`, `haiku`, `sonnet`) carry no prefix; they are
# resolved through `pricing.resolve_model_alias` first (#679).
# Lives here (not in the pricing module) so all model↔tier knowledge is
# co-located with the tier→model mapping above (#170).
#
# `claude-fable` joined at #674, when the family was first curated for
# pricing. It is placed by PRICE RANK AS A STAND-IN FOR CAPABILITY --
# Fable prices above every Opus ($10/$50 and $8/$40 vs $5/$25) -- and
# that placement is PROVISIONAL: we do not know Fable is more capable
# than Opus, only that it is more expensive, and it could be a
# specialized line rather than a higher tier. `complex` is the safe
# choice on the three diagnostics consumers OF THIS MAP -- `_detect_mismatch`
# treats `complex` and the unmatched-family `moderate` default
# identically at both its `current_tier` uses; `select_target_model`
# returns the same target either way (its `model_routing` call sites
# pass a fixed tier, and its `correlator` path passes the tier
# `faster_tier` computed, which is `moderate` for Fable in both
# readings); and `faster_tier` itself yields one step down (Sonnet)
# instead of two (Haiku), which is what "one tier faster" is meant to
# mean. A fourth consumer, `config.scoring._is_expensive_model` (#680),
# is NOT indifferent to the placement: `complex` is what makes a
# read-only Fable/Mythos agent draw the `model_selection` "consider a
# cheaper model" rec. That use asks a price question, so price rank is
# the right evidence there -- but moving Fable or Mythos out of `complex`
# (#678, #683) would stop that rec (pinned by
# `test_scoring.py::TestModelSelectionScoring::test_complex_tier_model_read_only_is_flagged`).
# Scoped to this map
# deliberately: `parent_workload.pick_alternative_model` is a SECOND
# ladder that does not consult it, and tiering Fable here did not fix
# it -- that needed its own branch (#674), and consolidating the two
# is #683.
#
# The deeper gap -- that ComplexityTier conflates how hard the work is
# with where a model sits on price/capability, so there is no
# "same tier, cheaper model" recommendation -- is tracked in #678 and
# is deliberately NOT resolved by adding a fourth tier here.
_TIER_BY_FAMILY: dict[ComplexityTier, tuple[str, ...]] = {
    "simple": ("claude-haiku",),
    "moderate": ("claude-sonnet",),
    # `claude-mythos` added by #664: `claude-mythos-5-2` prices at $8/$40, identical to
    # `claude-fable-5-2`, so it classifies at the same tier on rate evidence rather than on
    # a guess about the family. Curating it for pricing without teaching this map is the
    # #674 Fable defect (a false offload recommendation); `pick_alternative_model` carries
    # the matching branch, and #683 owns collapsing the two ladders into one.
    "complex": ("claude-opus", "claude-fable", "claude-mythos"),
}


def classify_model_tier(model: str) -> ComplexityTier:
    """Classify a Claude model ID into a complexity tier by family prefix.

    The model is first resolved through ``pricing.resolve_model_alias``, so a
    bare alias (``model: opus`` in agent frontmatter) classifies the same as
    the id pricing resolves it to (#679). Resolution lives here, not at the
    callers, so ``faster_tier`` and ``select_target_model`` inherit it.

    Returns "moderate" for anything that doesn't match a known family —
    a safe default that doesn't emit MODEL_MISMATCH signals against
    unrecognized models.
    """
    canonical = resolve_model_alias(model)
    for tier, prefixes in _TIER_BY_FAMILY.items():
        if any(canonical.startswith(p) for p in prefixes):
            return tier
    return "moderate"


class SimpleBranchInputs(BaseModel):
    """``mean_tool_calls`` / ``mean_tokens`` over one invocation subset (#675).

    Built by ``simple_branch_inputs`` (or by ``effective_simple_inputs`` from a
    builder's own means); read by ``classify_complexity``'s "simple" branch and
    published on ``MODEL_MISMATCH`` detail as the ``simple_branch_*`` keys.
    """

    mean_tool_calls: float
    mean_tokens: float
    invocation_count: int
    """Observations the two means cover: invocations carrying both values, or
    the builder's own observation count where it supplies no subset."""


def simple_branch_inputs(invocations: list[AgentInvocation]) -> SimpleBranchInputs:
    """Both "simple"-branch means over the invocations carrying both values (#675).

    Since #690 ``tool_uses`` is trace-sourced on every linked invocation while
    ``total_tokens`` stays on the rollup, so the group-wide means average
    different invocations. Here both are averaged over the invocations that
    carry ``tool_uses`` AND ``total_tokens``. ``mean_tokens`` stays on the rollup
    context-size proxy the thresholds were tuned on (D057(1)) -- trace spend would
    re-calibrate the tiers, which #675 AC6 rules out.

    **Evidence floor.** Below ``MIN_INVOCATIONS_FOR_ANALYSIS`` such invocations
    ``mean_tokens`` is ``0.0``, which ``classify_complexity`` reads as "no token
    evidence" and classifies "moderate" -- so one rollup invocation among many
    rollup-less traces cannot call a whole group "simple".
    """
    matched = [
        (inv.tool_uses, inv.total_tokens)
        for inv in invocations
        if inv.tool_uses is not None and inv.total_tokens is not None
    ]
    if not matched:
        return SimpleBranchInputs(mean_tool_calls=0.0, mean_tokens=0.0, invocation_count=0)
    count = len(matched)
    mean_tool_calls = sum(tools for tools, _ in matched) / count
    if count < MIN_INVOCATIONS_FOR_ANALYSIS:
        return SimpleBranchInputs(
            mean_tool_calls=mean_tool_calls, mean_tokens=0.0, invocation_count=count,
        )
    mean_tokens = sum(tokens for _, tokens in matched) / count
    return SimpleBranchInputs(
        mean_tool_calls=mean_tool_calls, mean_tokens=mean_tokens, invocation_count=count,
    )


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
    simple_inputs: SimpleBranchInputs | None = None
    """Coverage-matched inputs for the "simple" branch only (#675). ``None``
    means the branch reads the builder's own ``mean_tool_calls`` /
    ``mean_tokens`` (``effective_simple_inputs``): parent bursts compute both
    over the same bursts; SDK-main averages tool calls over all non-synthetic
    turns and tokens over the turns carrying usage. Invocation-based builders
    always set it via ``simple_branch_inputs``."""


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
    # of data, and that evidence must include token data. ``mean_tokens``
    # is 0.0 when no observation carries token data; since #690 an agent
    # type can still have trace-sourced tool calls, and reading its 0.0 as
    # "below the token threshold" would call it simple (and steer it toward
    # Haiku) with no token evidence at all. Such a group falls through to
    # "moderate" so the recommendation reflects uncertainty rather than
    # asserting the work is small.
    #
    # Coverage (#675): invocation-based builders (``model_routing.
    # aggregate_agent_stats``, ``aggregate_cluster_stats``) supply
    # ``simple_inputs`` -- both means over the invocations carrying tool
    # calls AND rollup tokens, with an evidence floor (``simple_branch_inputs``).
    # The group-wide means above still drive "complex"; the signal publishes
    # both (``simple_branch_*`` keys). Parent-session builders leave
    # ``simple_inputs`` unset and the branch reads their own means: parent
    # bursts average both over the same bursts; SDK-main averages tool calls
    # over all non-synthetic turns and tokens over the turns carrying usage.
    simple = effective_simple_inputs(stats)
    has_token_evidence = simple.mean_tokens > 0
    if (
        has_token_evidence
        and simple.mean_tool_calls < _SIMPLE_MAX_TOOL_CALLS
        and simple.mean_tokens < _SIMPLE_MAX_TOKENS
    ):
        return "simple"
    return "moderate"


def effective_simple_inputs(stats: AgentStats) -> SimpleBranchInputs:
    """The inputs ``classify_complexity``'s "simple" branch reads (#675).

    ``stats.simple_inputs`` when a builder supplied the coverage-matched subset,
    else the builder's own group means over ``invocation_count`` observations.
    The one place that fallback lives, so the published ``simple_branch_*``
    detail keys are exactly what was classified.
    """
    if stats.simple_inputs is not None:
        return stats.simple_inputs
    return SimpleBranchInputs(
        mean_tool_calls=stats.mean_tool_calls,
        mean_tokens=stats.mean_tokens,
        invocation_count=stats.invocation_count,
    )


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
        simple_inputs=simple_branch_inputs(invocations),
    )
