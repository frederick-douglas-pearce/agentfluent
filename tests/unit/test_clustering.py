"""Tests for the shared TF-IDF + KMeans clustering primitives.

Covers each helper at the ``_clustering.py`` boundary, in isolation.
End-to-end behavior (these helpers wired into ``cluster_delegations``)
is exercised by ``test_delegation.py`` and is unaffected by sub-issue
A's pure refactor — these tests just lock the public-ish surface so
``parent_workload.py`` (sub-issue B onward) can rely on it.
"""

from __future__ import annotations

import pytest

pytest.importorskip("sklearn")

import numpy as np  # noqa: E402
from sklearn.feature_extraction.text import TfidfVectorizer  # noqa: E402

from agentfluent.diagnostics import _clustering  # noqa: E402
from agentfluent.diagnostics._clustering import (  # noqa: E402
    SKLEARN_AVAILABLE,
    SklearnMissingError,
    _all_rows_identical,
    _cluster_embeddings,
    _fit_kmeans,
    _group_indices_by_label,
    _mean_pairwise_cosine,
    _top_tfidf_terms,
)


def _two_blob_embeddings(n_per: int = 6, seed: int = 0) -> np.ndarray:
    """Two well-separated Gaussian blobs in 4-D — easy KMeans target."""
    rng = np.random.default_rng(seed)
    a = rng.normal(loc=0.0, scale=0.05, size=(n_per, 4))
    b = rng.normal(loc=5.0, scale=0.05, size=(n_per, 4))
    return np.vstack([a, b])


class TestFitKmeans:
    def test_recovers_two_blobs(self) -> None:
        embeddings = _two_blob_embeddings(n_per=6)
        labels = _fit_kmeans(embeddings, n_clusters=2)
        # Either label assignment is valid; what matters is that the two
        # halves end up in different clusters.
        assert labels[:6].tolist().count(labels[0]) == 6
        assert labels[6:].tolist().count(labels[6]) == 6
        assert labels[0] != labels[6]

    def test_returns_ndarray(self) -> None:
        embeddings = _two_blob_embeddings(n_per=4)
        labels = _fit_kmeans(embeddings, n_clusters=2)
        assert isinstance(labels, np.ndarray)

    def test_random_state_is_reproducible(self) -> None:
        embeddings = _two_blob_embeddings(n_per=5)
        a = _fit_kmeans(embeddings, n_clusters=2, random_state=7)
        b = _fit_kmeans(embeddings, n_clusters=2, random_state=7)
        assert np.array_equal(a, b)


class TestClusterEmbeddings:
    def test_small_n_forces_k2(self) -> None:
        # n=8 < _SMALL_N_THRESHOLD (10) → forced k=2 path.
        embeddings = _two_blob_embeddings(n_per=4)
        labels = _cluster_embeddings(embeddings, n_samples=8)
        assert set(labels.tolist()) == {0, 1}

    def test_large_n_uses_silhouette_sweep(self) -> None:
        # n=12 >= threshold → sweep path. With clearly separated blobs,
        # the sweep should still resolve to two clusters.
        embeddings = _two_blob_embeddings(n_per=6)
        labels = _cluster_embeddings(embeddings, n_samples=12)
        assert len(set(labels.tolist())) >= 2

    def test_degenerate_input_logs_and_falls_back(
        self, caplog: pytest.LogCaptureFixture,
    ) -> None:
        # All-identical rows: silhouette can't separate them, sweep
        # collapses, fallback path fires + logs.
        embeddings = np.zeros((12, 4))
        with caplog.at_level("INFO", logger="agentfluent.diagnostics._clustering"):
            labels = _cluster_embeddings(embeddings, n_samples=12)
        assert isinstance(labels, np.ndarray)
        assert len(labels) == 12
        assert any(
            "no multi-cluster solution" in rec.message for rec in caplog.records
        )


class TestMeanPairwiseCosine:
    def test_single_member_is_one(self) -> None:
        assert _mean_pairwise_cosine(np.array([[1.0, 0.0, 0.0]])) == 1.0

    def test_identical_vectors_max_cohesion(self) -> None:
        embeddings = np.array([[1.0, 0.0], [1.0, 0.0], [1.0, 0.0]])
        assert _mean_pairwise_cosine(embeddings) == pytest.approx(1.0)

    def test_orthogonal_vectors_zero_cohesion(self) -> None:
        embeddings = np.array([[1.0, 0.0], [0.0, 1.0]])
        assert _mean_pairwise_cosine(embeddings) == pytest.approx(0.0, abs=1e-9)

    def test_excludes_self_similarity_from_average(self) -> None:
        # Two identical rows: pairwise sim matrix is [[1,1],[1,1]].
        # If we forgot to subtract the diagonal, the average would still
        # be 1.0 only by coincidence — use mixed similarity to expose it.
        embeddings = np.array([[1.0, 0.0], [0.0, 1.0], [1.0, 1.0]])
        # Off-diagonal cosines: (a,b)=0, (a,c)=√2/2, (b,c)=√2/2.
        # Mean of off-diagonal half-matrix = (0 + 0.7071 + 0.7071)/3.
        expected = (0.0 + (2 ** 0.5) / 2 + (2 ** 0.5) / 2) / 3
        assert _mean_pairwise_cosine(embeddings) == pytest.approx(expected, abs=1e-6)


class TestTopTfidfTerms:
    def test_picks_term_with_highest_mean_score(self) -> None:
        texts = [
            "pytest pytest pytest",
            "pytest run failure",
            "pytest assertion error",
        ]
        vec = TfidfVectorizer()
        matrix = vec.fit_transform(texts)
        terms = vec.get_feature_names_out()
        top = _top_tfidf_terms(matrix, member_indices=[0, 1, 2], terms=terms, top_n=1)
        assert top == ["pytest"]

    def test_skips_zero_score_terms(self) -> None:
        # All members are the same single word; only that word should
        # appear in the top-N — never zero-score noise.
        texts = ["alpha", "alpha", "alpha"]
        vec = TfidfVectorizer()
        matrix = vec.fit_transform(texts)
        terms = vec.get_feature_names_out()
        top = _top_tfidf_terms(matrix, member_indices=[0, 1, 2], terms=terms, top_n=5)
        assert top == ["alpha"]

    def test_returns_at_most_top_n(self) -> None:
        # Default TfidfVectorizer regex requires 2+ char tokens, so use
        # words rather than single letters.
        texts = [
            "alpha beta gamma delta epsilon zeta",
            "alpha beta gamma delta epsilon zeta",
            "alpha beta gamma delta epsilon zeta",
        ]
        vec = TfidfVectorizer()
        matrix = vec.fit_transform(texts)
        terms = vec.get_feature_names_out()
        top = _top_tfidf_terms(matrix, member_indices=[0, 1, 2], terms=terms, top_n=3)
        assert len(top) == 3


class TestGroupIndicesByLabel:
    def test_groups_in_input_order(self) -> None:
        labels = np.array([0, 1, 0, 1, 0])
        groups = _group_indices_by_label(labels)
        assert groups[0] == [0, 2, 4]
        assert groups[1] == [1, 3]

    def test_handles_single_cluster(self) -> None:
        labels = np.array([7, 7, 7])
        groups = _group_indices_by_label(labels)
        assert dict(groups) == {7: [0, 1, 2]}

    def test_handles_empty_labels(self) -> None:
        groups = _group_indices_by_label(np.array([], dtype=int))
        assert dict(groups) == {}


class TestAllRowsIdentical:
    def test_single_row_is_identical(self) -> None:
        vec = TfidfVectorizer()
        matrix = vec.fit_transform(["only one"])
        assert _all_rows_identical(matrix) is True

    def test_truly_identical_rows(self) -> None:
        vec = TfidfVectorizer()
        matrix = vec.fit_transform(["same text", "same text", "same text"])
        assert _all_rows_identical(matrix) is True

    def test_distinct_rows_return_false(self) -> None:
        vec = TfidfVectorizer()
        matrix = vec.fit_transform(["pytest run", "review pull request", "build deploy"])
        assert _all_rows_identical(matrix) is False


class TestSklearnAvailability:
    def test_module_exposes_sklearn_flag(self) -> None:
        # The flag is the canonical source of truth for whether the
        # optional extra is installed; consumer modules re-export it.
        assert SKLEARN_AVAILABLE is True

    def test_sklearn_missing_error_is_runtime_error(self) -> None:
        assert issubclass(SklearnMissingError, RuntimeError)

    def test_module_has_canonical_definitions(self) -> None:
        # Sub-issue B and onward will import these from _clustering.
        assert hasattr(_clustering, "SKLEARN_AVAILABLE")
        assert hasattr(_clustering, "SklearnMissingError")
