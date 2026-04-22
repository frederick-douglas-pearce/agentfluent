"""Tests for delegation clustering, draft generation, and dedup.

scikit-learn is an optional extra; tests are skipped wholesale when
it's not installed (``pytest.importorskip``) and the dedicated
"sklearn-missing" tests stub the module flag to exercise the error
path without uninstalling anything.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("sklearn")

from agentfluent.agents.models import AgentInvocation  # noqa: E402
from agentfluent.config.models import AgentConfig, Scope  # noqa: E402
from agentfluent.diagnostics import delegation  # noqa: E402
from agentfluent.diagnostics.delegation import (  # noqa: E402
    MODEL_HAIKU,
    MODEL_OPUS,
    MODEL_SONNET,
    DelegationCluster,
    SklearnMissingError,
    _apply_dedup,
    _classify_confidence,
    _classify_model,
    _collect_tools_from_traces,
    cluster_delegations,
    generate_draft,
    suggest_delegations,
)
from agentfluent.diagnostics.models import DelegationSuggestion  # noqa: E402
from agentfluent.traces.models import SubagentToolCall, SubagentTrace  # noqa: E402


def _inv(
    agent_type: str = "general-purpose",
    description: str = "",
    prompt: str = "",
    total_tokens: int | None = None,
    trace: SubagentTrace | None = None,
) -> AgentInvocation:
    return AgentInvocation(
        agent_type=agent_type,
        is_builtin=agent_type.lower() == "general-purpose",
        description=description,
        prompt=prompt,
        tool_use_id="toolu_" + description[:10],
        total_tokens=total_tokens,
        trace=trace,
    )


def _trace_with_tools(tools: list[str]) -> SubagentTrace:
    calls = [
        SubagentToolCall(tool_name=t, input_summary="x", result_summary="ok")
        for t in tools
    ]
    return SubagentTrace(
        agent_id="agent-x",
        agent_type="general-purpose",
        delegation_prompt="",
        tool_calls=calls,
    )


def _config(
    name: str = "existing-agent",
    description: str = "",
    prompt_body: str = "",
) -> AgentConfig:
    return AgentConfig(
        name=name,
        file_path=Path(f"/home/user/.claude/agents/{name}.md"),
        scope=Scope.USER,
        description=description,
        prompt_body=prompt_body,
    )


# Two distinct delegation patterns, 5 invocations each — TF-IDF should
# separate these cleanly. Each combined description + prompt clears the
# MIN_TEXT_TOKENS filter (20 tokens). The default threshold itself is
# tracked for empirical calibration in #140; these fixtures test
# algorithm correctness at the current setting, not the threshold choice.
_TEST_INVS = [
    _inv(
        description="run the pytest suite and report failures",
        prompt=(
            "execute pytest on the tests directory report failures coverage "
            "and any slow tests collect output pytest fixtures markers"
        ),
    ),
    _inv(
        description="run unit tests with pytest and coverage",
        prompt=(
            "invoke pytest for the unit tests collect coverage metrics "
            "report slow failures fixtures markers output across the suite"
        ),
    ),
    _inv(
        description="execute pytest test runner on suite",
        prompt=(
            "run pytest against the testing folder capture output report "
            "failures fixtures markers coverage slow tests across the suite"
        ),
    ),
    _inv(
        description="pytest execution request for full suite",
        prompt=(
            "please run pytest over the entire testing directory report "
            "failures coverage markers fixtures output slow tests collect"
        ),
    ),
    _inv(
        description="test suite pytest run with coverage",
        prompt=(
            "kick off pytest across the test modules return results "
            "coverage markers fixtures output failures slow tests collect"
        ),
    ),
    _inv(
        description="parse session JSONL file extract tool_use",
        prompt=(
            "read the claude JSONL session file extract tool_use content "
            "blocks from assistant messages parse metadata timestamps model"
        ),
    ),
    _inv(
        description="read JSONL session data for parsing",
        prompt=(
            "parse a session JSONL surface the assistant tool_use "
            "invocations extract metadata timestamps model content blocks"
        ),
    ),
    _inv(
        description="process session JSONL file extraction",
        prompt=(
            "extract tool_use blocks from the claude session JSONL "
            "parse assistant message metadata timestamps model content"
        ),
    ),
    _inv(
        description="session parser task JSONL extraction",
        prompt=(
            "read the JSONL session parse assistant messages for tool "
            "calls extract metadata timestamps model content blocks"
        ),
    ),
    _inv(
        description="JSONL parsing delegation session file",
        prompt=(
            "open the session JSONL file extract the tool_use content "
            "blocks parse assistant messages metadata timestamps model"
        ),
    ),
]


class TestClusterDelegations:
    def test_below_min_cluster_size_returns_empty(self) -> None:
        # Three invs, min=5 → empty.
        invs = _TEST_INVS[:3]
        assert cluster_delegations(invs, min_cluster_size=5) == []

    def test_filters_non_general_purpose(self) -> None:
        invs = [_inv(agent_type="pm", description="x", prompt="y z w " * 10)]
        assert cluster_delegations(invs) == []

    def test_filters_short_text(self) -> None:
        # 2 tokens combined — below MIN_TEXT_TOKENS.
        invs = [
            _inv(description="x", prompt="y")
            for _ in range(10)
        ]
        assert cluster_delegations(invs) == []

    def test_two_well_separated_patterns_form_two_clusters(self) -> None:
        clusters = cluster_delegations(_TEST_INVS, min_cluster_size=3)
        assert len(clusters) == 2
        for c in clusters:
            assert len(c.members) >= 3

    def test_small_n_forces_k_equals_two(self) -> None:
        # 7 invocations — below _SMALL_N_THRESHOLD (10). k forced to 2.
        # Confirm we don't crash and we produce ≤ 2 clusters.
        invs = _TEST_INVS[:7]
        clusters = cluster_delegations(invs, min_cluster_size=3)
        assert len(clusters) <= 2

    def test_below_min_cluster_size_after_filter_returns_empty(self) -> None:
        # Mix of general-purpose + non-matching: only 2 general-purpose
        # candidates pass the filter, below min.
        invs = [
            _inv(description="run pytest unit tests with coverage", prompt="x y z"),
            _inv(description="run pytest unit tests with coverage", prompt="x y z"),
            _inv(agent_type="architect", description="design", prompt="x y z" * 10),
        ]
        assert cluster_delegations(invs, min_cluster_size=5) == []


class TestGenerateDraft:
    def _cluster(
        self,
        top_terms: list[str] | None = None,
        members: list[AgentInvocation] | None = None,
        cohesion: float = 0.85,
    ) -> DelegationCluster:
        # Use `is None` rather than `or` so an explicitly-empty
        # `top_terms=[]` reaches the cluster (needed to exercise the
        # fallback-name path in _synthesize_name).
        return DelegationCluster(
            members=members if members is not None else [_inv(description="d", prompt="p")] * 10,
            top_terms=top_terms if top_terms is not None else ["pytest", "tests", "run"],
            cohesion_score=cohesion,
        )

    def test_synthesizes_name_from_top_terms(self) -> None:
        draft = generate_draft(self._cluster(top_terms=["pytest", "runner"]))
        assert draft.name == "pytest-runner"

    def test_empty_top_terms_fallback_name(self) -> None:
        draft = generate_draft(self._cluster(top_terms=[]))
        assert draft.name == "custom-agent"

    def test_read_only_traces_recommend_haiku(self) -> None:
        members = [
            _inv(description="d", prompt="p", trace=_trace_with_tools(["Read", "Grep"])),
        ] * 5
        draft = generate_draft(self._cluster(members=members))
        assert draft.model == MODEL_HAIKU
        assert draft.tools == ["Grep", "Read"]

    def test_write_heavy_high_tokens_recommend_opus(self) -> None:
        members = [
            _inv(
                description="d", prompt="p",
                total_tokens=50_000,
                trace=_trace_with_tools(["Write", "Edit", "Bash"]),
            ),
        ] * 5
        draft = generate_draft(self._cluster(members=members))
        assert draft.model == MODEL_OPUS

    def test_default_to_sonnet(self) -> None:
        # Mix of read + write but low tokens.
        members = [
            _inv(
                description="d", prompt="p",
                total_tokens=5_000,
                trace=_trace_with_tools(["Read", "Write"]),
            ),
        ] * 5
        draft = generate_draft(self._cluster(members=members))
        assert draft.model == MODEL_SONNET

    def test_no_traces_emits_tools_note(self) -> None:
        members = [_inv(description="d", prompt="p", trace=None)] * 5
        draft = generate_draft(self._cluster(members=members))
        assert draft.tools == []
        assert "newer session data" in draft.tools_note

    def test_confidence_high(self) -> None:
        members = [_inv()] * 12
        draft = generate_draft(self._cluster(members=members, cohesion=0.85))
        assert draft.confidence == "high"

    def test_confidence_medium(self) -> None:
        members = [_inv()] * 6
        draft = generate_draft(self._cluster(members=members, cohesion=0.65))
        assert draft.confidence == "medium"

    def test_confidence_low(self) -> None:
        members = [_inv()] * 5
        draft = generate_draft(self._cluster(members=members, cohesion=0.4))
        assert draft.confidence == "low"


class TestClassifyHelpers:
    def test_mean_tokens_empty_list(self) -> None:
        # Helper exercised indirectly via _classify_model with no token data.
        members = [_inv(total_tokens=None)] * 3
        assert _classify_model(["Write"], members) == MODEL_SONNET

    def test_classify_confidence_size_guard(self) -> None:
        # size < 10 even with high cohesion → medium, not high.
        assert _classify_confidence(9, 0.95) == "medium"

    def test_collect_tools_dedups_across_traces(self) -> None:
        members = [
            _inv(trace=_trace_with_tools(["Read", "Grep"])),
            _inv(trace=_trace_with_tools(["Grep", "Bash"])),
        ]
        assert _collect_tools_from_traces(members) == ["Bash", "Grep", "Read"]


class TestDedup:
    def _draft(
        self, description: str = "run pytest suite",
    ) -> DelegationSuggestion:
        return DelegationSuggestion(
            name="test-runner",
            description=description,
            model=MODEL_SONNET,
            tools=[],
            tools_note="",
            prompt_template="You run pytest tests and report results.",
            confidence="medium",
            cluster_size=5,
            cohesion_score=0.7,
            top_terms=["pytest"],
        )

    def test_similar_existing_agent_marks_dedup_note(self) -> None:
        draft = self._draft()
        configs = [_config(
            name="pytest-runner",
            description="Runs pytest suite and reports test results",
        )]
        result = _apply_dedup([draft], configs, min_similarity=0.3)
        assert result[0].dedup_note
        assert "pytest-runner" in result[0].dedup_note

    def test_dissimilar_existing_agent_leaves_dedup_note_empty(self) -> None:
        draft = self._draft()
        configs = [_config(
            name="database-migrator",
            description="Manages SQL schema migrations for the payments service",
        )]
        result = _apply_dedup([draft], configs, min_similarity=0.7)
        assert result[0].dedup_note == ""

    def test_falls_back_to_prompt_body_when_description_empty(self) -> None:
        draft = self._draft()
        # Description empty; prompt_body carries the matching signal —
        # deliberately overlapping the draft's "run pytest tests and
        # report results" phrasing.
        configs = [_config(
            name="pytest-runner",
            description="",
            prompt_body="You run pytest tests and report results from the test suite.",
        )]
        result = _apply_dedup([draft], configs, min_similarity=0.3)
        assert "pytest-runner" in result[0].dedup_note

    def test_empty_existing_configs_passes_through(self) -> None:
        draft = self._draft()
        result = _apply_dedup([draft], [], min_similarity=0.7)
        assert result[0].dedup_note == ""

    def test_all_empty_config_texts_skip_dedup(self) -> None:
        draft = self._draft()
        # Both description and prompt_body empty on every config.
        configs = [_config(name="empty1"), _config(name="empty2")]
        result = _apply_dedup([draft], configs, min_similarity=0.7)
        assert result[0].dedup_note == ""


class TestSuggestDelegations:
    def test_end_to_end_produces_suggestions(self) -> None:
        suggestions = suggest_delegations(_TEST_INVS, min_cluster_size=3)
        assert len(suggestions) >= 1
        # Every suggestion carries a name, description, model, confidence.
        for s in suggestions:
            assert s.name
            assert s.description
            assert s.model in {MODEL_HAIKU, MODEL_SONNET, MODEL_OPUS}
            assert s.confidence in {"high", "medium", "low"}

    def test_no_general_purpose_returns_empty(self) -> None:
        invs = [
            _inv(agent_type="pm", description="d " * 10, prompt="p " * 10)
            for _ in range(10)
        ]
        assert suggest_delegations(invs) == []


class TestIdenticalRowsAnomaly:
    """Byte-identical delegation text across all invocations is unusual
    for real agent data (parent agents are probabilistic). We detect it
    upfront, log a WARNING so the anomaly is observable, and emit a
    single cluster holding all members rather than letting sklearn
    produce convergence noise."""

    def _identical_invs(self, n: int = 10) -> list[AgentInvocation]:
        # Identical prompt across all N — simulates an upstream bug
        # producing duplicate records, not a realistic use case.
        shared_prompt = (
            "read the session JSONL file extract tool_use blocks "
            "parse assistant messages metadata timestamps model content"
        )
        return [
            _inv(description="extract tool_use blocks from session", prompt=shared_prompt)
            for _ in range(n)
        ]

    def test_identical_rows_produces_single_cluster(self) -> None:
        clusters = cluster_delegations(self._identical_invs())
        assert len(clusters) == 1
        assert len(clusters[0].members) == 10

    def test_identical_rows_logs_warning(
        self, caplog: pytest.LogCaptureFixture,
    ) -> None:
        with caplog.at_level("WARNING", logger="agentfluent.diagnostics.delegation"):
            cluster_delegations(self._identical_invs())
        assert any(
            "identical" in rec.message.lower() for rec in caplog.records
        )


class TestSklearnMissing:
    def test_cluster_delegations_raises_when_sklearn_unavailable(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(delegation, "SKLEARN_AVAILABLE", False)
        with pytest.raises(SklearnMissingError, match="agentfluent\\[clustering\\]"):
            cluster_delegations(_TEST_INVS)

    def test_suggest_delegations_raises_when_sklearn_unavailable(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(delegation, "SKLEARN_AVAILABLE", False)
        with pytest.raises(SklearnMissingError):
            suggest_delegations(_TEST_INVS)
