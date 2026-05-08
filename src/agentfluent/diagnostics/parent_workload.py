"""Parent-thread tool-burst extraction for offload-candidate diagnostics.

Walks a parsed parent-thread session and groups consecutive tool calls into
``ToolBurst`` records — one per "what the user asked, and what the assistant
did to answer it." Bursts are the unit of clustering for #189's offload
recommendations: cluster bursts by similarity, project the parent-thread
cost of each cluster against a cheaper alternative model, surface the
delta as an offload candidate.

This module owns extraction + filtering only. Clustering, cost
estimation, candidate synthesis, pipeline wiring, and CLI rendering land
in sub-issues C–F of #189.

**Burst boundary rule** (assistant-turn with cross-turn merging):

A burst opens at the first assistant message containing ``tool_use``
blocks after a "real" user message. It extends across subsequent
assistant messages so long as only ``tool_result``-only user messages
intervene (the standard Claude tool loop: assistant calls tools, user
message carries results, assistant calls more tools — no human turn).
A burst closes when a real user message arrives or the session ends.

A "real" user message has non-empty ``text`` AND no ``tool_result``
content block. Claude Code emits tool-result responses as user messages
with no text — that's structural, not a human turn.
"""

from __future__ import annotations

import logging
import warnings
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

# sklearn is an optional extra; the cluster_bursts/generate_offload_candidate
# code paths gate on SKLEARN_AVAILABLE. Pre-existing extract/cost helpers
# don't need sklearn — they keep working without the extra installed.
try:
    import numpy as np
    from sklearn.decomposition import TruncatedSVD
    from sklearn.feature_extraction.text import TfidfVectorizer
except ImportError:  # pragma: no cover — exercised via install-path test
    pass

from agentfluent.agents.models import WRITE_TOOLS
from agentfluent.analytics.pricing import ModelPricing, compute_cost, get_pricing
from agentfluent.config.models import AgentConfig
from agentfluent.core.session import (
    SessionMessage,
    ToolUseBlock,
    Usage,
    index_tool_results_by_id,
)
from agentfluent.diagnostics._clustering import (
    SKLEARN_AVAILABLE,
    SklearnMissingError,
    all_rows_identical,
    cluster_embeddings,
    group_indices_by_label,
    mean_pairwise_cosine,
    top_tfidf_terms,
)
from agentfluent.diagnostics._complexity import (
    AgentStats,
    classify_complexity,
    recommend_model_for_complexity,
)
from agentfluent.diagnostics.delegation import (
    DEFAULT_MIN_SIMILARITY,
    MODEL_HAIKU,
    MODEL_SONNET,
    apply_dedup,
    synthesize_description,
    synthesize_name,
)
from agentfluent.diagnostics.models import (
    DelegationSuggestion,
    OffloadCandidate,
)

logger = logging.getLogger(__name__)

MIN_BURST_TOOLS = 2

MAX_BURST_TOOLS = 20
"""Cap degenerate single-message mega-bursts. Without this, a 'batch refactor'
assistant turn emitting 50 Edit calls in one message would become one
huge burst that dominates any cluster it joined and distorts cost
estimates."""

MIN_BURST_TEXT_TOKENS = 30
"""Whitespace-token floor on ``burst_text`` output. Below this the burst
lacks the semantic context needed for meaningful TF-IDF clustering in
sub-issue D."""


@dataclass
class ToolBurst:
    """A contiguous run of parent-thread tool calls anchored to one user request.

    Internal type — never serialized in JSON output, never crosses the
    diagnostics/CLI boundary. ``OffloadCandidate`` (sub-issue D, in
    ``diagnostics/models.py``) is the cross-boundary Pydantic counterpart.
    """

    preceding_user_text: str
    assistant_text: str
    tool_use_blocks: list[ToolUseBlock]
    """Tool calls in original order. NOT de-duplicated — repeated tool use
    IS a discriminative signal that sub-issue D's TF-IDF clustering will
    weight."""
    tool_result_errors: int = 0
    """Count of paired ``tool_result`` blocks with ``is_error=True`` for
    the tools in :attr:`tool_use_blocks`. Computed at extract time via
    ``index_tool_results_by_id``; consumed by ``_aggregate_burst_stats``
    to drive the burst-cluster ``error_rate`` that feeds
    ``classify_complexity``. Defaults to 0 when the paired ``tool_result``
    is missing (interrupted session) or its ``is_error`` is None/False
    (#264)."""
    usage: Usage = field(default_factory=Usage)
    model: str = ""
    """Model id from the first contributing assistant message. All messages
    in a single tool loop should share a model in practice; if they don't,
    we keep the first and log."""


@dataclass
class _OpenBurst:
    """In-progress burst accumulator. Promoted to ``ToolBurst`` via
    :meth:`finalize` once a real user message or end-of-session closes it."""

    preceding_user_text: str
    model: str = ""
    assistant_texts: list[str] = field(default_factory=list)
    tool_blocks: list[ToolUseBlock] = field(default_factory=list)
    usages: list[Usage] = field(default_factory=list)

    def add_assistant_message(self, msg: SessionMessage) -> None:
        if msg.text:
            self.assistant_texts.append(msg.text)
        self.tool_blocks.extend(msg.tool_use_blocks)
        if msg.usage is not None:
            self.usages.append(msg.usage)

    def add_text(self, text: str) -> None:
        if text:
            self.assistant_texts.append(text)

    def finalize(
        self,
        count_errors: Callable[[list[ToolUseBlock]], int] | None = None,
    ) -> ToolBurst | None:
        if not self.tool_blocks:
            return None
        return ToolBurst(
            preceding_user_text=self.preceding_user_text,
            assistant_text="\n".join(t for t in self.assistant_texts if t),
            tool_use_blocks=list(self.tool_blocks),
            tool_result_errors=(
                count_errors(self.tool_blocks) if count_errors is not None else 0
            ),
            usage=sum(self.usages, Usage()),
            model=self.model,
        )


def _is_real_user_text(msg: SessionMessage) -> bool:
    """A 'real' user turn vs a tool-result wrapper.

    Claude Code emits tool-result responses as user-typed messages with no
    text and a ``tool_result`` content block. Those don't break a burst.
    A real user turn has actual text AND no tool_result block.
    """
    if msg.type != "user":
        return False
    if any(b.type == "tool_result" for b in msg.content_blocks):
        return False
    return bool(msg.text.strip())


def extract_bursts(messages: list[SessionMessage]) -> list[ToolBurst]:
    """Group consecutive parent-thread tool calls into ``ToolBurst`` records.

    See module docstring for the boundary rule. No filtering applied here —
    use :func:`filter_bursts` to drop too-small / too-large / too-short
    bursts before clustering.

    Pairs each tool_use to its tool_result via
    :func:`agentfluent.core.session.index_tool_results_by_id` and stamps
    the per-burst error count on ``ToolBurst.tool_result_errors``. The
    index is built once over all messages so an interrupted-session
    pair (tool_use without a paired tool_result) silently falls back to
    "not an error" for that tool — see #264.
    """
    tool_results = index_tool_results_by_id(messages)

    def _count_errors(blocks: list[ToolUseBlock]) -> int:
        # ``is True`` rather than truthy: ``is_error`` is ``bool | None``
        # on ``ContentBlock``; missing field stays out of the error count.
        return sum(
            1 for b in blocks
            if (entry := tool_results.get(b.id)) is not None
            and entry[2] is True
        )

    bursts: list[ToolBurst] = []
    last_real_user_text = ""
    cur: _OpenBurst | None = None

    for msg in messages:
        if _is_real_user_text(msg):
            if cur is not None and (b := cur.finalize(_count_errors)) is not None:
                bursts.append(b)
            cur = None
            last_real_user_text = msg.text
            continue

        if msg.type != "assistant":
            continue

        tool_blocks = msg.tool_use_blocks
        if not tool_blocks:
            # Text-only assistant turn (e.g., "I'll now do X" between two
            # tool_use turns) — fold its text into the open burst's context
            # without breaking the run. Doesn't open a burst on its own.
            if cur is not None:
                cur.add_text(msg.text)
            continue

        if cur is None:
            cur = _OpenBurst(
                preceding_user_text=last_real_user_text,
                model=msg.model or "",
            )
        elif msg.model and cur.model and msg.model != cur.model:
            logger.debug(
                "Burst spans assistant messages with differing models "
                "(%r vs %r); keeping the first.",
                cur.model, msg.model,
            )

        cur.add_assistant_message(msg)

    if cur is not None and (b := cur.finalize(_count_errors)) is not None:
        bursts.append(b)
    return bursts


def burst_text(burst: ToolBurst) -> str:
    """Compose the text representation a burst contributes to TF-IDF clustering.

    Tool names are NOT de-duplicated: the duplicate ``Read`` in ``"Bash
    Read Read Edit"`` is a discriminative pattern signal that sub-issue
    D's vectorizer should be free to weight.
    """
    parts = [
        burst.preceding_user_text,
        burst.assistant_text,
        " ".join(b.name for b in burst.tool_use_blocks),
    ]
    return " ".join(p for p in parts if p)


def filter_bursts(bursts: list[ToolBurst]) -> list[ToolBurst]:
    """Apply ``MIN_BURST_TOOLS``, ``MAX_BURST_TOOLS``, ``MIN_BURST_TEXT_TOKENS``.

    Bursts above ``MAX_BURST_TOOLS`` are dropped (with a debug log) rather
    than truncated — a 50-tool batch is structurally different from a
    typical workflow and shouldn't be folded into one.
    """
    kept: list[ToolBurst] = []
    for b in bursts:
        n_tools = len(b.tool_use_blocks)
        if n_tools < MIN_BURST_TOOLS:
            continue
        if n_tools > MAX_BURST_TOOLS:
            logger.debug(
                "Dropping burst with %d tool calls (cap: %d); "
                "preceding_user_text=%r",
                n_tools, MAX_BURST_TOOLS, b.preceding_user_text[:60],
            )
            continue
        if len(burst_text(b).split()) < MIN_BURST_TEXT_TOKENS:
            continue
        kept.append(b)
    return kept


def pick_alternative_model(parent_model: str) -> str:
    """Pick the next-cheaper tier; Haiku and unknowns are returned unchanged."""
    lowered = parent_model.lower()
    if "opus" in lowered:
        return MODEL_SONNET
    if "sonnet" in lowered:
        return MODEL_HAIKU
    if "haiku" in lowered:
        # Explicit fixed-point branch (rather than falling through to the
        # bottom-of-function "unchanged" return) — Haiku is intentionally
        # terminal, not "unknown."
        return parent_model
    return parent_model


def estimate_burst_cost(
    burst: ToolBurst,
    *,
    parent_pricing: ModelPricing | None,
    alt_pricing: ModelPricing | None,
) -> tuple[float, float]:
    """Return ``(parent_cost_usd, savings_usd_signed)`` for one burst.

    Savings is signed; negative means offloading would cost MORE than
    staying on the parent (cache is load-bearing for this pattern). Per
    architect review (#189), the sign is preserved — never clamped to
    zero — so callers can render negative-savings clusters with a
    distinct "do not offload" treatment.

    When either pricing is unknown (lookup returned ``None``), returns
    ``(0.0, 0.0)`` and emits a debug log. Callers treat that as "no
    estimate available" rather than "free."
    """
    if parent_pricing is None or alt_pricing is None:
        logger.debug(
            "Skipping cost estimate for burst: pricing unavailable "
            "(parent=%s alt=%s).",
            parent_pricing is not None, alt_pricing is not None,
        )
        return (0.0, 0.0)

    u = burst.usage
    parent_cost = compute_cost(
        parent_pricing,
        u.input_tokens, u.output_tokens,
        u.cache_creation_input_tokens, u.cache_read_input_tokens,
    )
    # Alt-model has no cache benefit: cache_read becomes fresh input,
    # cache_creation drops out (a delegated subagent would re-fetch its
    # own context, not pay to write the parent's cache).
    effective_input = u.input_tokens + u.cache_read_input_tokens
    alt_cost = compute_cost(
        alt_pricing, effective_input, u.output_tokens, 0, 0,
    )
    return (parent_cost, parent_cost - alt_cost)


# Burst-cluster classification + offload-candidate synthesis. Mirrors
# delegation.cluster_delegations / generate_draft on parent-thread bursts.
# The TF-IDF + LSA + KMeans pipeline is duplicated rather than extracted
# (#189-D Q3 architect review): two consumers, ~30 lines each, premature
# abstraction. Structured as a contiguous block for future extraction if
# a third consumer appears.

DEFAULT_BURST_CLUSTER_SIZE = 5
"""Minimum bursts per cluster surfaced as an offload candidate."""

LSA_COMPONENTS = 50
"""Mirrors ``delegation.LSA_COMPONENTS``. Calibration sweep tracked in
``scripts/calibration/threshold_validation.ipynb`` (#140)."""

_TOOL_FREQUENCY_THRESHOLD = 0.5
"""Same default as ``delegation.DEFAULT_TOOL_FREQUENCY_THRESHOLD`` (#184).
Local copy rather than import because the burst path may calibrate
independently — sub-issue F's notebook explicitly checks this. See #184
architect-review note."""

_CONFIDENCE_HIGH_SIZE = 10
_CONFIDENCE_HIGH_COHESION = 0.50
_CONFIDENCE_MEDIUM_COHESION = 0.35

_BURST_AGENT_TYPE = "<bursts>"
"""Sentinel ``AgentStats.agent_type`` for burst clusters. Not user-facing;
exists so downstream consumers (e.g., ``classify_complexity``) can detect
burst-cluster stats vs real-agent stats if they ever need to."""


@dataclass
class BurstCluster:
    """A KMeans cluster of similar parent-thread bursts.

    Internal type. ``OffloadCandidate`` (in ``diagnostics/models.py``) is
    the cross-boundary Pydantic counterpart that downstream consumers see.
    """

    members: list[ToolBurst]
    top_terms: list[str]
    cohesion_score: float


def _require_sklearn() -> None:
    if not SKLEARN_AVAILABLE:
        raise SklearnMissingError(
            "Install agentfluent[clustering] to enable parent-thread "
            "offload-candidate analysis.",
        )


def _build_single_burst_cluster(
    bursts: list[ToolBurst],
    tfidf_matrix: np.ndarray,
    terms: np.ndarray,
) -> BurstCluster:
    """All-rows-identical fallback. Mirrors delegation's ``_build_single_cluster``.
    Cohesion is 1.0 by definition since every member shares the same vector."""
    all_indices = list(range(len(bursts)))
    return BurstCluster(
        members=list(bursts),
        top_terms=top_tfidf_terms(tfidf_matrix, all_indices, terms),
        cohesion_score=1.0,
    )


def cluster_bursts(
    bursts: list[ToolBurst],
    *,
    min_cluster_size: int = DEFAULT_BURST_CLUSTER_SIZE,
) -> list[BurstCluster]:
    """Group similar parent-thread bursts via TF-IDF + LSA + KMeans.

    Mirrors ``delegation.cluster_delegations`` operating on ``ToolBurst``
    records and ``burst_text``. ``filter_bursts`` is applied internally to
    drop too-small / too-large / sub-token-floor bursts.

    Raises ``SklearnMissingError`` when scikit-learn is not installed.
    Returns ``[]`` when fewer than ``min_cluster_size`` bursts survive
    filtering, or when no KMeans cluster met ``min_cluster_size``.
    """
    _require_sklearn()

    candidates = filter_bursts(bursts)
    if len(candidates) < min_cluster_size:
        return []

    texts = [burst_text(b) for b in candidates]
    tfidf = TfidfVectorizer(stop_words="english", max_features=500)
    tfidf_matrix = tfidf.fit_transform(texts)
    terms = tfidf.get_feature_names_out()

    if all_rows_identical(tfidf_matrix):
        logger.warning(
            "Burst clustering: all %d TF-IDF rows are identical — "
            "this is unusual for real parent-thread data. Check for "
            "duplicate burst records or upstream extraction bugs.",
            tfidf_matrix.shape[0],
        )
        return [_build_single_burst_cluster(candidates, tfidf_matrix, terms)]

    n_components = min(
        LSA_COMPONENTS, tfidf_matrix.shape[1] - 1, len(candidates) - 1,
    )
    if n_components >= 2:
        with warnings.catch_warnings():
            # TruncatedSVD can warn on near-zero variance; the
            # degenerate-fallback in cluster_embeddings handles output.
            warnings.simplefilter("ignore", category=RuntimeWarning)
            lsa = TruncatedSVD(n_components=n_components, random_state=42)
            embeddings = lsa.fit_transform(tfidf_matrix)
    else:
        embeddings = tfidf_matrix.toarray()

    labels = cluster_embeddings(embeddings, len(candidates))
    groups = group_indices_by_label(labels)

    clusters: list[BurstCluster] = []
    for _label, member_indices in sorted(groups.items()):
        if len(member_indices) < min_cluster_size:
            continue
        members = [candidates[i] for i in member_indices]
        member_embeddings = embeddings[member_indices]
        cohesion = mean_pairwise_cosine(member_embeddings)
        cluster_top_terms = top_tfidf_terms(tfidf_matrix, member_indices, terms)
        clusters.append(
            BurstCluster(
                members=members,
                top_terms=cluster_top_terms,
                cohesion_score=cohesion,
            ),
        )
    return clusters


def _collect_tools_from_bursts(bursts: list[ToolBurst]) -> list[str]:
    """Sorted union of every tool name observed across the cluster's bursts."""
    return sorted({b.name for burst in bursts for b in burst.tool_use_blocks})


def _filter_tools_from_bursts(
    bursts: list[ToolBurst],
    *,
    threshold: float = _TOOL_FREQUENCY_THRESHOLD,
) -> list[str]:
    """Keep tools used in at least ``threshold`` fraction of cluster bursts.

    Presence-based, mirroring ``delegation._filter_tools_by_frequency``: a
    tool counts once per burst it appears in, regardless of call volume
    within that burst. See #184 for the architect-reviewed rationale.
    """
    if not bursts:
        return []
    counts: Counter[str] = Counter()
    for burst in bursts:
        counts.update({b.name for b in burst.tool_use_blocks})
    cutoff = threshold * len(bursts)
    return sorted(t for t, c in counts.items() if c >= cutoff)


def _aggregate_burst_stats(bursts: list[ToolBurst]) -> AgentStats:
    """Build ``AgentStats`` for ``classify_complexity`` from a burst cluster.

    ``error_rate`` is the **mean of per-burst rates** rather than the
    cluster-pooled rate, matching the convention used for invocation-
    based stats in ``model_routing.py`` and ``_complexity.py`` so the
    same ``_COMPLEX_MIN_ERROR_RATE = 0.20`` threshold means the same
    thing on both surfaces. Per-burst error data comes from
    :attr:`ToolBurst.tool_result_errors` populated at extract time
    (#264).
    """
    if not bursts:
        return AgentStats(
            agent_type=_BURST_AGENT_TYPE,
            invocation_count=0,
            mean_tool_calls=0.0,
            mean_tokens=0.0,
            error_rate=0.0,
            has_write_tools=False,
            current_model=None,
        )
    tool_counts = [len(b.tool_use_blocks) for b in bursts]
    token_totals = [b.usage.total_tokens for b in bursts]
    all_tools = {b.name for burst in bursts for b in burst.tool_use_blocks}
    per_burst_rates = [
        b.tool_result_errors / n if (n := len(b.tool_use_blocks)) else 0.0
        for b in bursts
    ]
    return AgentStats(
        agent_type=_BURST_AGENT_TYPE,
        invocation_count=len(bursts),
        mean_tool_calls=sum(tool_counts) / len(tool_counts),
        mean_tokens=sum(token_totals) / len(token_totals),
        error_rate=sum(per_burst_rates) / len(per_burst_rates),
        has_write_tools=bool(all_tools & WRITE_TOOLS),
        current_model=None,
    )


def _tool_sequence_summary(bursts: list[ToolBurst]) -> list[str]:
    """The most common tool sequence across the cluster, in original order.

    Picks the modal sequence by ``Counter`` of tool-name tuples. Ties are
    broken by insertion order (Counter.most_common is stable on equal
    counts). Returns an empty list when no burst has tool calls.
    """
    if not bursts:
        return []
    sequences = [
        tuple(b.name for b in burst.tool_use_blocks)
        for burst in bursts
        if burst.tool_use_blocks
    ]
    if not sequences:
        return []
    most_common, _ = Counter(sequences).most_common(1)[0]
    return list(most_common)


def _classify_confidence(
    cluster_size: int, cohesion: float,
) -> Literal["high", "medium", "low"]:
    """Same boundaries as ``delegation._classify_confidence``."""
    if (
        cluster_size >= _CONFIDENCE_HIGH_SIZE
        and cohesion >= _CONFIDENCE_HIGH_COHESION
    ):
        return "high"
    if cohesion >= _CONFIDENCE_MEDIUM_COHESION:
        return "medium"
    return "low"


def _synthesize_burst_prompt(
    top_terms: list[str], members: list[ToolBurst],
) -> str:
    """Burst-specific prompt scaffold.

    Uses ``preceding_user_text[:120]`` from up to two members as
    "Example user requests observed" — the ToolBurst analogue of
    delegation's ``AgentInvocation.description``. Per architect review
    for #189-D Q2: keep ``_synthesize_prompt`` private to delegation
    (members type differs); write this sibling for bursts.
    """
    terms_list = ", ".join(top_terms[:3]) if top_terms else "this task"
    examples = [
        m.preceding_user_text[:120].strip()
        for m in members[:2]
        if m.preceding_user_text and m.preceding_user_text.strip()
    ]
    examples_block = ""
    if examples:
        examples_block = "\n\nExample user requests observed:\n" + "\n".join(
            f"- {e}" for e in examples
        )
    return (
        f"You handle recurring parent-thread workflows involving "
        f"{terms_list}.\n\n"
        "Scope your work to the specific request the parent agent "
        "delegated. Return concise, structured output."
        f"{examples_block}"
    )


def _build_subagent_draft(
    cluster: BurstCluster, recommended_model: str,
) -> DelegationSuggestion:
    tools = _filter_tools_from_bursts(cluster.members)
    tools_observed = _collect_tools_from_bursts(cluster.members)
    if not tools_observed:
        tools_note = "# no tool data captured from bursts"
    elif not tools:
        tools_note = (
            f"# no tool used in >="
            f"{int(_TOOL_FREQUENCY_THRESHOLD * 100)}% of cluster bursts "
            f"— see tools_observed for the unfiltered list"
        )
    else:
        tools_note = ""
    return DelegationSuggestion(
        name=synthesize_name(cluster.top_terms),
        description=synthesize_description(cluster.top_terms),
        model=recommended_model,
        tools=tools,
        tools_observed=tools_observed,
        tools_note=tools_note,
        prompt_template=_synthesize_burst_prompt(
            cluster.top_terms, cluster.members,
        ),
        confidence=_classify_confidence(
            len(cluster.members), cluster.cohesion_score,
        ),
        cluster_size=len(cluster.members),
        cohesion_score=cluster.cohesion_score,
        top_terms=list(cluster.top_terms),
    )


def generate_offload_candidate(
    cluster: BurstCluster,
    *,
    parent_model: str,
    parent_pricing: ModelPricing | None,
    alt_pricing: ModelPricing | None,
) -> OffloadCandidate:
    """Synthesize an ``OffloadCandidate`` from a clustered group of bursts.

    Aggregates parent-thread cost and signed savings across the cluster's
    bursts (negative savings is preserved per #189-C — actionable signal).
    Builds a ``DelegationSuggestion`` as the ``subagent_draft`` so the YAML
    rendering surface stays consistent with the existing delegation flow.
    Sub-issue E populates ``matched_agent`` / ``dedup_note`` afterward.
    """
    alt_model = pick_alternative_model(parent_model)

    parent_tokens = 0
    parent_cost_total = 0.0
    savings_total = 0.0
    for b in cluster.members:
        parent_tokens += b.usage.total_tokens
        parent_cost, savings_signed = estimate_burst_cost(
            b, parent_pricing=parent_pricing, alt_pricing=alt_pricing,
        )
        parent_cost_total += parent_cost
        savings_total += savings_signed

    cost_note = ""
    if savings_total < 0:
        cost_note = (
            "Offloading would increase cost — parent-thread cache appears "
            "load-bearing for this pattern. Consider keeping on parent."
        )

    stats = _aggregate_burst_stats(cluster.members)
    recommended_model = recommend_model_for_complexity(classify_complexity(stats))

    subagent_draft = _build_subagent_draft(cluster, recommended_model)

    return OffloadCandidate(
        name=subagent_draft.name,
        description=subagent_draft.description,
        confidence=subagent_draft.confidence,
        cluster_size=len(cluster.members),
        cohesion_score=cluster.cohesion_score,
        top_terms=list(cluster.top_terms),
        tool_sequence_summary=_tool_sequence_summary(cluster.members),
        tools=list(subagent_draft.tools),
        tools_note=subagent_draft.tools_note,
        estimated_parent_tokens=parent_tokens,
        estimated_parent_cost_usd=parent_cost_total,
        estimated_savings_usd=savings_total,
        parent_model=parent_model,
        alternative_model=alt_model,
        cost_note=cost_note,
        target_kind="subagent",
        subagent_draft=subagent_draft,
        skill_draft=None,
    )


def _pick_cluster_parent_model(cluster: BurstCluster) -> str:
    """The parent model id observed on the cluster's bursts.

    All bursts in a cluster should share a model in practice (the
    extractor logs at debug when they don't); we use the first member's
    model. Empty string means "unknown" — pricing lookup falls back to
    ``None`` and the candidate ships with zero cost figures.
    """
    if not cluster.members:
        return ""
    return cluster.members[0].model or ""


def apply_offload_dedup(
    candidates: list[OffloadCandidate],
    existing_configs: list[AgentConfig],
    min_similarity: float,
) -> list[OffloadCandidate]:
    """Mark offload candidates whose draft overlaps an existing agent config.

    Reuses ``delegation.apply_dedup`` against each candidate's
    ``subagent_draft`` (a ``DelegationSuggestion``), then mirrors the
    populated ``matched_agent`` and ``dedup_note`` back onto the parent
    ``OffloadCandidate``. Single source of dedup truth — the TF-IDF
    cosine logic lives in one place.

    Candidates without a ``subagent_draft`` are passed through
    untouched (defensive — production code always sets one).
    """
    if not existing_configs:
        return candidates
    drafts = [c.subagent_draft for c in candidates if c.subagent_draft is not None]
    if not drafts:
        return candidates
    apply_dedup(drafts, existing_configs, min_similarity)
    for candidate in candidates:
        if candidate.subagent_draft is None:
            continue
        candidate.matched_agent = candidate.subagent_draft.matched_agent
        candidate.dedup_note = candidate.subagent_draft.dedup_note
    return candidates


def build_offload_candidates(
    messages: list[SessionMessage],
    existing_configs: list[AgentConfig] | None = None,
    *,
    min_cluster_size: int = DEFAULT_BURST_CLUSTER_SIZE,
    min_similarity: float = DEFAULT_MIN_SIMILARITY,
) -> list[OffloadCandidate]:
    """End-to-end: extract → filter → cluster → generate → dedup.

    Pipeline orchestration lives here (rather than in
    ``diagnostics/pipeline.py``) so parent-thread logic stays in one
    module — ``run_diagnostics`` becomes a thin caller. Symmetric with
    ``delegation.suggest_delegations`` placement.

    Pricing is looked up per cluster from
    ``cluster.members[0].model``; clusters whose model has no pricing
    table entry ship with ``estimated_*_usd = 0.0`` and a debug log.
    Returns ``[]`` when sklearn is missing OR no cluster meets
    ``min_cluster_size``.
    """
    if not SKLEARN_AVAILABLE:
        return []
    bursts = filter_bursts(extract_bursts(messages))
    clusters = cluster_bursts(bursts, min_cluster_size=min_cluster_size)
    if not clusters:
        return []
    candidates: list[OffloadCandidate] = []
    for cluster in clusters:
        parent_model = _pick_cluster_parent_model(cluster)
        parent_pricing = get_pricing(parent_model) if parent_model else None
        alt_model = pick_alternative_model(parent_model)
        alt_pricing = get_pricing(alt_model) if alt_model else None
        candidates.append(
            generate_offload_candidate(
                cluster,
                parent_model=parent_model,
                parent_pricing=parent_pricing,
                alt_pricing=alt_pricing,
            ),
        )
    if existing_configs:
        candidates = apply_offload_dedup(candidates, existing_configs, min_similarity)
    return candidates
