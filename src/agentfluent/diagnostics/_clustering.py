"""Generic TF-IDF + KMeans clustering primitives shared across diagnostics.

Houses the input-agnostic building blocks used by ``delegation.py`` and
(starting with #189) ``parent_workload.py``: KMeans fit + silhouette
sweep, cosine-cohesion scoring, top-term extraction, label grouping,
and the degenerate-input ("all rows identical") detector. Anything
shaped to a specific diagnostic input (delegation prompts, parent-thread
bursts, etc.) lives in the consumer module, not here.

scikit-learn remains an **optional extra** (``agentfluent[clustering]``).
The canonical ``SKLEARN_AVAILABLE`` flag and ``SklearnMissingError``
type live here; consumer modules re-export them so existing callers and
test monkeypatches keep working unchanged.
"""

from __future__ import annotations

import logging
import warnings
from collections import defaultdict

try:
    import numpy as np
    from sklearn.cluster import KMeans
    from sklearn.metrics import silhouette_score
    from sklearn.metrics.pairwise import cosine_similarity

    SKLEARN_AVAILABLE = True
except ImportError:  # pragma: no cover — exercised via the install-path test
    SKLEARN_AVAILABLE = False

logger = logging.getLogger(__name__)

# Tunables shared across all clustering consumers. Override via kwargs at
# the call site rather than mutating these.
_SILHOUETTE_K_MAX = 10        # upper bound on silhouette-selected k
_SMALL_N_THRESHOLD = 10       # below this, force k=2 without silhouette
_FORCED_SMALL_K = 2
_KMEANS_RANDOM_STATE = 42     # default seed for reproducible clustering
_KMEANS_N_INIT = 10           # KMeans restarts; higher resists bad local minima
_TOP_TERMS_COUNT = 5


class SklearnMissingError(RuntimeError):
    """Raised when clustering is invoked but scikit-learn is not installed."""


def fit_kmeans(
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


def cluster_embeddings(embeddings: np.ndarray, n_samples: int) -> np.ndarray:
    """Return KMeans labels from the k that scores highest on silhouette.

    For n < 10, silhouette's useful k-range collapses (e.g. k_upper=1) —
    force k=2 and do a single fit. Otherwise, sweep k and keep the
    labels from the best-scoring fit so we avoid a redundant final-k
    refit.
    """
    if n_samples < _SMALL_N_THRESHOLD:
        return fit_kmeans(embeddings, _FORCED_SMALL_K)

    k_upper = min(_SILHOUETTE_K_MAX, n_samples // 5)
    if k_upper < 2:
        return fit_kmeans(embeddings, _FORCED_SMALL_K)

    best_labels: np.ndarray | None = None
    best_score = -1.0
    for k in range(2, k_upper + 1):
        labels = fit_kmeans(embeddings, k)
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
            return fit_kmeans(embeddings, _FORCED_SMALL_K)

    return best_labels


def mean_pairwise_cosine(cluster_embeddings: np.ndarray) -> float:
    """Average pairwise cosine similarity within a cluster — our cohesion proxy."""
    if len(cluster_embeddings) < 2:
        return 1.0
    sim = cosine_similarity(cluster_embeddings)
    # Exclude the diagonal (self-sim=1) to avoid biasing toward 1.0.
    n = sim.shape[0]
    total = (sim.sum() - n) / (n * n - n)
    return float(total)


def top_tfidf_terms(
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


def group_indices_by_label(labels: np.ndarray) -> dict[int, list[int]]:
    """Single-pass grouping of sample indices by their cluster label."""
    groups: dict[int, list[int]] = defaultdict(list)
    for i, lab in enumerate(labels):
        groups[int(lab)].append(i)
    return groups


def all_rows_identical(tfidf_matrix: np.ndarray) -> bool:
    """Detect an anomaly: byte-identical TF-IDF rows across all members.

    Agent-generated prompts are probabilistic; identical rows across
    multiple invocations is very unlikely in real data. When it does
    happen, it usually points upstream — duplicate session records,
    an invocation-extraction bug, or a parent agent producing
    non-probabilistic output. Detecting this case lets callers emit a
    clear warning instead of silently producing zero clusters or sklearn
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
