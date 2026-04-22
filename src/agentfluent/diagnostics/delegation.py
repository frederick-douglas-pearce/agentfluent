"""Delegation clustering + subagent draft generation.

Clusters ``general-purpose`` agent invocations by their
``description + prompt`` text using TF-IDF → LSA → KMeans, then
synthesizes a draft subagent definition for each cluster. Output is a
list of ``DelegationSuggestion`` records that surface on
``DiagnosticsResult.delegation_suggestions``.

scikit-learn is an **optional extra** (``agentfluent[clustering]``).
Users who only run ``agentfluent analyze`` without clustering flags do
not need to install it. The top-level try/except sets
``SKLEARN_AVAILABLE``; direct callers of the public functions here
get a clear ``SklearnMissingError`` instead of an ImportError. The
pipeline silently skips clustering when unavailable; the CLI surfaces
an error + non-zero exit when a user explicitly passes a clustering
flag but the extra is missing.

See issue #139 for the plan to strip this conditional machinery once
a second sklearn-dependent feature lands.
"""

from __future__ import annotations

import logging
import re
import warnings
from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

try:
    import numpy as np
    from sklearn.cluster import KMeans
    from sklearn.decomposition import TruncatedSVD
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics import silhouette_score
    from sklearn.metrics.pairwise import cosine_similarity

    SKLEARN_AVAILABLE = True
except ImportError:  # pragma: no cover — exercised via the install-path test
    SKLEARN_AVAILABLE = False

from agentfluent.agents.models import WRITE_TOOLS, is_general_purpose
from agentfluent.diagnostics.models import DelegationSuggestion

if TYPE_CHECKING:
    from agentfluent.agents.models import AgentInvocation
    from agentfluent.config.models import AgentConfig

logger = logging.getLogger(__name__)

# Module-level tunables. Override via function kwargs or CLI flags.
MIN_TEXT_TOKENS = 20          # combined description + prompt below this is filtered
LSA_COMPONENTS = 50
DEFAULT_MIN_CLUSTER_SIZE = 5
DEFAULT_MIN_SIMILARITY = 0.7
_SILHOUETTE_K_MAX = 10        # upper bound on silhouette-selected k
_SMALL_N_THRESHOLD = 10       # below this, force k=2 without silhouette
_FORCED_SMALL_K = 2
_KMEANS_RANDOM_STATE = 42     # default seed for reproducible clustering
_KMEANS_N_INIT = 10           # KMeans restarts; higher resists bad local minima
_CONFIDENCE_HIGH_SIZE = 10
_CONFIDENCE_HIGH_COHESION = 0.8
_CONFIDENCE_MEDIUM_COHESION = 0.6
_TOOL_READ_ONLY = frozenset(
    {"Read", "Grep", "Glob", "WebFetch", "WebSearch", "LS"},
)
_HEAVY_TOKEN_THRESHOLD = 20_000
_TOP_TERMS_COUNT = 5
_PROMPT_BODY_SNIPPET_CHARS = 500

# Model tiers for draft generation. Kept as module constants so later
# releases can bump to newer Claude versions in one place.
MODEL_HAIKU = "claude-haiku-4-5"
MODEL_SONNET = "claude-sonnet-4-6"
MODEL_OPUS = "claude-opus-4-7"


class SklearnMissingError(RuntimeError):
    """Raised when clustering is invoked but scikit-learn is not installed."""


@dataclass
class DelegationCluster:
    """Internal: a KMeans cluster of general-purpose invocations."""

    members: list[AgentInvocation]
    top_terms: list[str]
    cohesion_score: float


ConfidenceTier = Literal["high", "medium", "low"]


def _require_sklearn() -> None:
    if not SKLEARN_AVAILABLE:
        raise SklearnMissingError(
            "Install agentfluent[clustering] to enable delegation analysis.",
        )


def _combined_text(inv: AgentInvocation) -> str:
    return f"{inv.description} {inv.prompt}"


def _combined_text_tokens(inv: AgentInvocation) -> int:
    return len(_combined_text(inv).split())


def _filter_candidates(
    invocations: list[AgentInvocation],
) -> list[AgentInvocation]:
    """Keep general-purpose invocations with enough text to cluster on."""
    return [
        inv for inv in invocations
        if is_general_purpose(inv.agent_type)
        and _combined_text_tokens(inv) >= MIN_TEXT_TOKENS
    ]


def _fit_kmeans(
    embeddings: np.ndarray,
    n_clusters: int,
    *,
    random_state: int = _KMEANS_RANDOM_STATE,
    n_init: int = _KMEANS_N_INIT,
) -> np.ndarray:
    """Fit KMeans and return labels as a concrete ndarray.

    Thin wrapper that fixes the ``random_state`` / ``n_init`` defaults
    (both tunable via kwargs) and converts sklearn's ``Any``-typed
    ``fit_predict`` return value to an ndarray so mypy stays strict.
    """
    km = KMeans(n_clusters=n_clusters, random_state=random_state, n_init=n_init)
    return np.asarray(km.fit_predict(embeddings))


def _cluster_embeddings(embeddings: np.ndarray, n_samples: int) -> np.ndarray:
    """Return KMeans labels from the k that scores highest on silhouette.

    For n < 10, silhouette's useful k-range collapses (e.g. k_upper=1) —
    force k=2 and do a single fit. Otherwise, sweep k and keep the
    labels from the best-scoring fit so we avoid a redundant final-k
    refit.
    """
    if n_samples < _SMALL_N_THRESHOLD:
        return _fit_kmeans(embeddings, _FORCED_SMALL_K)

    k_upper = min(_SILHOUETTE_K_MAX, n_samples // 5)
    if k_upper < 2:
        return _fit_kmeans(embeddings, _FORCED_SMALL_K)

    best_labels: np.ndarray | None = None
    best_score = -1.0
    for k in range(2, k_upper + 1):
        labels = _fit_kmeans(embeddings, k)
        if len(set(labels)) < 2:
            continue
        score = silhouette_score(embeddings, labels)
        if score > best_score:
            best_score = score
            best_labels = labels

    # Degenerate fallback: every k collapsed to a single cluster. Rare,
    # but possible with near-identical embeddings that survived row
    # de-dup but lose variance after LSA. Log it so the anomaly is
    # observable, then force k=2 and suppress the resulting
    # convergence warning.
    if best_labels is None:
        logger.info(
            "KMeans produced no multi-cluster solution for %d samples; "
            "falling back to k=2. Input may have near-zero variance "
            "after TF-IDF + LSA reduction.",
            n_samples,
        )
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return _fit_kmeans(embeddings, _FORCED_SMALL_K)

    return best_labels


def _mean_pairwise_cosine(cluster_embeddings: np.ndarray) -> float:
    """Average pairwise cosine similarity within a cluster — our cohesion proxy."""
    if len(cluster_embeddings) < 2:
        return 1.0
    sim = cosine_similarity(cluster_embeddings)
    # Exclude the diagonal (self-sim=1) to avoid biasing toward 1.0.
    n = sim.shape[0]
    total = (sim.sum() - n) / (n * n - n)
    return float(total)


def _top_tfidf_terms(
    tfidf_matrix: np.ndarray,
    member_indices: list[int],
    terms: np.ndarray,
    top_n: int = _TOP_TERMS_COUNT,
) -> list[str]:
    member_vecs = tfidf_matrix[member_indices]
    mean_scores = member_vecs.mean(axis=0)
    mean_array = np.asarray(mean_scores).ravel()
    top_idx = mean_array.argsort()[::-1][:top_n]
    return [str(terms[i]) for i in top_idx if mean_array[i] > 0]


def _group_indices_by_label(labels: np.ndarray) -> dict[int, list[int]]:
    """Single-pass grouping of sample indices by their cluster label."""
    groups: dict[int, list[int]] = defaultdict(list)
    for i, lab in enumerate(labels):
        groups[int(lab)].append(i)
    return groups


def _all_rows_identical(tfidf_matrix: np.ndarray) -> bool:
    """Detect an anomaly: byte-identical TF-IDF rows across all members.

    Agent-generated prompts are probabilistic; identical rows across
    multiple invocations is very unlikely in real data. When it does
    happen, it usually points upstream — duplicate session records,
    an invocation-extraction bug, or a parent agent producing
    non-probabilistic output. Detecting this case lets us emit a clear
    warning instead of silently producing zero clusters or sklearn
    convergence noise.

    Densifies once and compares rows to row 0 with a vectorized numpy
    equality check. A single ``toarray()`` + broadcast is ~O(n × features);
    faster and more predictable than a row-by-row densify loop, and
    avoids sparse-sparse ``!=`` short-circuit quirks across scipy
    versions.
    """
    if tfidf_matrix.shape[0] <= 1:
        return True
    # sklearn TfidfVectorizer returns a scipy sparse matrix, not a
    # numpy ndarray — no type stubs for scipy.sparse in this project.
    dense = tfidf_matrix.toarray()  # type: ignore[attr-defined]
    return bool((dense == dense[0]).all())


def _build_single_cluster(
    candidates: list[AgentInvocation],
    tfidf_matrix: np.ndarray,
    terms: np.ndarray,
) -> DelegationCluster:
    """Build one cluster holding every candidate — the answer when
    every TF-IDF row is identical. Cohesion is 1.0 by definition
    (all members share the same vector)."""
    all_indices = list(range(len(candidates)))
    return DelegationCluster(
        members=list(candidates),
        top_terms=_top_tfidf_terms(tfidf_matrix, all_indices, terms),
        cohesion_score=1.0,
    )


def cluster_delegations(
    invocations: list[AgentInvocation],
    *,
    min_cluster_size: int = DEFAULT_MIN_CLUSTER_SIZE,
) -> list[DelegationCluster]:
    """Group general-purpose invocations into clusters of similar delegations.

    Raises ``SklearnMissingError`` when scikit-learn is not installed.
    Returns ``[]`` if there are not enough candidates or all are filtered
    out by the minimum-text-length guard.
    """
    _require_sklearn()

    candidates = _filter_candidates(invocations)
    if len(candidates) < min_cluster_size:
        return []

    texts = [_combined_text(inv) for inv in candidates]
    tfidf = TfidfVectorizer(stop_words="english", max_features=500)
    tfidf_matrix = tfidf.fit_transform(texts)
    terms = tfidf.get_feature_names_out()

    # Anomaly: if every row is identical, surface the upstream issue and
    # return a single cluster. Clustering algorithms below would either
    # emit confusing convergence warnings or silently produce no output.
    if _all_rows_identical(tfidf_matrix):
        logger.warning(
            "Delegation clustering: all %d TF-IDF rows are identical — "
            "this is unusual for real agent data. Check for duplicate "
            "invocation records or upstream extraction bugs.",
            tfidf_matrix.shape[0],
        )
        return [_build_single_cluster(candidates, tfidf_matrix, terms)]

    # LSA dimensionality reduction — skip gracefully if we do not have
    # enough features or samples to support TruncatedSVD.
    n_components = min(
        LSA_COMPONENTS, tfidf_matrix.shape[1] - 1, len(candidates) - 1,
    )
    if n_components >= 2:
        with warnings.catch_warnings():
            # TruncatedSVD can raise a RuntimeWarning on near-zero
            # variance input; the degenerate-fallback in
            # _cluster_embeddings already handles the output path.
            warnings.simplefilter("ignore", category=RuntimeWarning)
            lsa = TruncatedSVD(n_components=n_components, random_state=42)
            embeddings = lsa.fit_transform(tfidf_matrix)
    else:
        embeddings = tfidf_matrix.toarray()

    labels = _cluster_embeddings(embeddings, len(candidates))
    groups = _group_indices_by_label(labels)

    clusters: list[DelegationCluster] = []
    for _label, member_indices in sorted(groups.items()):
        if len(member_indices) < min_cluster_size:
            continue
        members = [candidates[i] for i in member_indices]
        cluster_embeddings = embeddings[member_indices]
        cohesion = _mean_pairwise_cosine(cluster_embeddings)
        top_terms = _top_tfidf_terms(tfidf_matrix, member_indices, terms)
        clusters.append(
            DelegationCluster(
                members=members,
                top_terms=top_terms,
                cohesion_score=cohesion,
            ),
        )
    return clusters


_SLUG_STRIP_RE = re.compile(r"[^a-z0-9-]")


def _synthesize_name(top_terms: list[str]) -> str:
    """Build a kebab-case agent name from the top TF-IDF terms.

    Prefers the first two terms when available; otherwise falls back to
    a generic placeholder. Not unique across clusters — the caller is
    expected to show these as *drafts*, not final names.
    """
    cleaned = [_SLUG_STRIP_RE.sub("", t.lower().replace("_", "-")) for t in top_terms]
    cleaned = [c for c in cleaned if c]
    if not cleaned:
        return "custom-agent"
    if len(cleaned) == 1:
        return cleaned[0]
    return f"{cleaned[0]}-{cleaned[1]}"


def _synthesize_description(top_terms: list[str]) -> str:
    if not top_terms:
        return "Handles a recurring delegation pattern."
    terms_list = ", ".join(top_terms[:3])
    return f"Handles delegations related to: {terms_list}."


def _synthesize_prompt(top_terms: list[str], members: list[AgentInvocation]) -> str:
    """Generic prompt scaffold anchored on the cluster's top terms.

    Intentionally minimal — users will refine before committing. The
    scaffold references the top terms so it feels purposeful rather
    than boilerplate.
    """
    terms_list = ", ".join(top_terms[:3]) if top_terms else "this task"
    example_descs = [m.description for m in members[:2] if m.description]
    examples_block = ""
    if example_descs:
        examples_block = "\n\nExample delegations observed:\n" + "\n".join(
            f"- {d}" for d in example_descs
        )
    return (
        f"You handle recurring delegations involving {terms_list}.\n\n"
        "Scope your work to the specific task described in the user's "
        "prompt. Return concise, structured output."
        f"{examples_block}"
    )


def _collect_tools_from_traces(members: list[AgentInvocation]) -> list[str]:
    tools: set[str] = set()
    for m in members:
        if m.trace is not None:
            tools.update(m.trace.unique_tool_names)
    return sorted(tools)


def _mean_tokens(members: list[AgentInvocation]) -> float:
    values = [m.total_tokens for m in members if m.total_tokens is not None]
    if not values:
        return 0.0
    return sum(values) / len(values)


def _classify_model(tools: list[str], members: list[AgentInvocation]) -> str:
    """Pick a model tier based on the tool mix + observed token burn.

    - All read-only tools → haiku (cheap, fast)
    - Contains heavy-write tools + high average tokens → opus
    - Default → sonnet
    """
    if not tools:
        return MODEL_SONNET
    tool_set = set(tools)
    if tool_set and tool_set <= _TOOL_READ_ONLY:
        return MODEL_HAIKU
    if tool_set & WRITE_TOOLS and _mean_tokens(members) > _HEAVY_TOKEN_THRESHOLD:
        return MODEL_OPUS
    return MODEL_SONNET


def _classify_confidence(cluster_size: int, cohesion: float) -> ConfidenceTier:
    if cluster_size >= _CONFIDENCE_HIGH_SIZE and cohesion >= _CONFIDENCE_HIGH_COHESION:
        return "high"
    if cohesion >= _CONFIDENCE_MEDIUM_COHESION:
        return "medium"
    return "low"


def generate_draft(cluster: DelegationCluster) -> DelegationSuggestion:
    """Synthesize a draft subagent definition from a cluster."""
    tools = _collect_tools_from_traces(cluster.members)
    tools_note = (
        "" if tools
        else "# run with newer session data for tool recommendations"
    )
    return DelegationSuggestion(
        name=_synthesize_name(cluster.top_terms),
        description=_synthesize_description(cluster.top_terms),
        model=_classify_model(tools, cluster.members),
        tools=tools,
        tools_note=tools_note,
        prompt_template=_synthesize_prompt(cluster.top_terms, cluster.members),
        confidence=_classify_confidence(len(cluster.members), cluster.cohesion_score),
        cluster_size=len(cluster.members),
        cohesion_score=cluster.cohesion_score,
        top_terms=list(cluster.top_terms),
    )


def _config_text(config: AgentConfig) -> str:
    """Pick the text to dedup against: description, or prompt_body snippet.

    ``AgentConfig.description`` is often empty in real configs (YAML
    frontmatter isn't always populated). Fall back to a prompt_body
    prefix so dedup still finds obvious overlaps.
    """
    if config.description:
        return config.description
    return config.prompt_body[:_PROMPT_BODY_SNIPPET_CHARS]


def _apply_dedup(
    drafts: list[DelegationSuggestion],
    existing_configs: list[AgentConfig],
    min_similarity: float,
) -> list[DelegationSuggestion]:
    """Mark drafts whose description+prompt is too close to any existing
    agent config. Deduped drafts are NOT removed from the output — they
    ship with a ``dedup_note`` so users see what was suppressed and why.
    """
    if not existing_configs:
        return drafts
    draft_texts = [f"{d.description} {d.prompt_template}" for d in drafts]
    config_texts = [_config_text(c) for c in existing_configs]
    # TF-IDF needs at least one non-empty text per document; skip dedup
    # entirely if all the config texts are empty.
    if not any(t.strip() for t in config_texts):
        return drafts
    vectorizer = TfidfVectorizer(stop_words="english")
    corpus = draft_texts + config_texts
    matrix = vectorizer.fit_transform(corpus)
    draft_vecs = matrix[: len(drafts)]
    config_vecs = matrix[len(drafts) :]
    sim = cosine_similarity(draft_vecs, config_vecs)
    for i, draft in enumerate(drafts):
        if sim.shape[1] == 0:
            break
        max_j = int(sim[i].argmax())
        max_sim = float(sim[i][max_j])
        if max_sim > min_similarity:
            matched_name = existing_configs[max_j].name
            draft.matched_agent = matched_name
            draft.dedup_note = (
                f"suppressed — already covered by '{matched_name}' "
                f"(similarity {max_sim:.2f})"
            )
    return drafts


def suggest_delegations(
    invocations: list[AgentInvocation],
    *,
    existing_configs: list[AgentConfig] | None = None,
    min_cluster_size: int = DEFAULT_MIN_CLUSTER_SIZE,
    min_similarity: float = DEFAULT_MIN_SIMILARITY,
) -> list[DelegationSuggestion]:
    """End-to-end: cluster + draft + dedup → list of DelegationSuggestion.

    Raises ``SklearnMissingError`` when scikit-learn is not installed.
    """
    _require_sklearn()
    clusters = cluster_delegations(
        invocations, min_cluster_size=min_cluster_size,
    )
    if not clusters:
        return []
    drafts = [generate_draft(c) for c in clusters]
    if existing_configs:
        drafts = _apply_dedup(drafts, existing_configs, min_similarity)
    return drafts
