"""Tests for burst clustering + offload-candidate synthesis (sub-issue D of #189).

Covers ``cluster_bursts``, ``generate_offload_candidate``, and the helpers
that build ``OffloadCandidate`` records (frequency-filtered tool list,
burst-stats aggregation, modal tool-sequence summary, signed-savings
preservation, offload-flavored YAML preamble).

scikit-learn is an optional extra; the cluster-path tests are skipped
wholesale when it's not installed (``pytest.importorskip``). The
"sklearn missing" path is exercised by stubbing ``SKLEARN_AVAILABLE``.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("sklearn")

from agentfluent.analytics.pricing import get_pricing  # noqa: E402
from agentfluent.core.session import ToolUseBlock, Usage  # noqa: E402
from agentfluent.diagnostics import parent_workload  # noqa: E402
from agentfluent.diagnostics.models import (  # noqa: E402
    DelegationSuggestion,
    OffloadCandidate,
    SkillScaffold,
)
from agentfluent.diagnostics.parent_workload import (  # noqa: E402
    DEFAULT_BURST_CLUSTER_SIZE,
    BurstCluster,
    SklearnMissingError,
    ToolBurst,
    _aggregate_burst_stats,
    _classify_confidence,
    _collect_tools_from_bursts,
    _filter_tools_from_bursts,
    _tool_sequence_summary,
    cluster_bursts,
    generate_offload_candidate,
)

_OPUS = get_pricing("claude-opus-4-7")
_SONNET = get_pricing("claude-sonnet-4-6")
assert _OPUS is not None
assert _SONNET is not None


def _tool(name: str, idx: int = 0) -> ToolUseBlock:
    return ToolUseBlock(id=f"toolu_{name}_{idx}", name=name, input={})


def _burst(
    *,
    user_text: str = "run the test suite",
    assistant_text: str = "I'll run pytest now.",
    tools: list[str] | None = None,
    input_tokens: int = 100,
    output_tokens: int = 200,
    cache_creation_input_tokens: int = 0,
    cache_read_input_tokens: int = 0,
    model: str = "claude-opus-4-7",
    tool_result_errors: int = 0,
) -> ToolBurst:
    blocks = [_tool(t, i) for i, t in enumerate(tools or [])]
    return ToolBurst(
        preceding_user_text=user_text,
        assistant_text=assistant_text,
        tool_use_blocks=blocks,
        tool_result_errors=tool_result_errors,
        usage=Usage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_creation_input_tokens=cache_creation_input_tokens,
            cache_read_input_tokens=cache_read_input_tokens,
        ),
        model=model,
    )


# Two semantically-distinct burst patterns — pytest-flavored vs PR-review-
# flavored. Each cluster has 6 bursts (above DEFAULT_BURST_CLUSTER_SIZE=5)
# and enough lexical variation to clear MIN_BURST_TEXT_TOKENS without
# blunting inter-cluster TF-IDF separation.
_PYTEST_TAIL = (
    " collect coverage with pytest-cov and emit xunit results per pytest "
    "module; use pytest fixtures and parametrize markers to exercise the "
    "edge cases reported by pytest collectors."
)
_PR_TAIL = (
    " summarize the pull request diff, list changed files, identify any "
    "regressions in the PR description, and highlight review comments "
    "from prior reviewers in the pull-request thread."
)


def _pytest_burst(idx: int) -> ToolBurst:
    return _burst(
        user_text=f"run pytest cycle {idx} for the test suite" + _PYTEST_TAIL,
        assistant_text=f"Running pytest now for round {idx}." + _PYTEST_TAIL,
        tools=["Bash", "Read", "Read"],
    )


def _pr_burst(idx: int) -> ToolBurst:
    return _burst(
        user_text=f"summarize pull request #{idx + 100}" + _PR_TAIL,
        assistant_text=f"Reviewing PR diff for #{idx + 100}." + _PR_TAIL,
        tools=["Bash", "Read", "Grep"],
    )


# ---------------------------------------------------------------------------
# Helper coverage
# ---------------------------------------------------------------------------


class TestCollectAndFilterTools:
    def test_collect_dedups_across_bursts(self) -> None:
        bursts = [
            _burst(tools=["Read", "Bash"]),
            _burst(tools=["Read", "Grep"]),
        ]
        assert _collect_tools_from_bursts(bursts) == ["Bash", "Grep", "Read"]

    def test_filter_drops_tool_below_threshold(self) -> None:
        # Read appears in 4/5 bursts (0.8) → kept. Bash appears in
        # 1/5 (0.2) → dropped — least-privilege filter mirrors #184.
        bursts = [
            _burst(tools=["Read", "Bash"]),
            _burst(tools=["Read"]),
            _burst(tools=["Read"]),
            _burst(tools=["Read"]),
            _burst(tools=["Edit"]),
        ]
        assert _filter_tools_from_bursts(bursts) == ["Read"]

    def test_filter_call_volume_does_not_inflate_presence(self) -> None:
        # 50 Bash calls in one burst still count as 1 burst-presence.
        bash_heavy = ToolBurst(
            preceding_user_text="x",
            assistant_text="x",
            tool_use_blocks=[_tool("Bash", i) for i in range(50)],
            usage=Usage(),
            model="claude-opus-4-7",
        )
        bursts = [
            bash_heavy,
            _burst(tools=["Read"]),
            _burst(tools=["Read"]),
            _burst(tools=["Read"]),
            _burst(tools=["Read"]),
        ]
        assert _filter_tools_from_bursts(bursts) == ["Read"]


class TestAggregateBurstStats:
    def test_means_computed_across_bursts(self) -> None:
        bursts = [
            _burst(tools=["Read", "Read"], input_tokens=100, output_tokens=100),
            _burst(tools=["Read"] * 6, input_tokens=300, output_tokens=300),
        ]
        stats = _aggregate_burst_stats(bursts)
        assert stats.mean_tool_calls == 4.0
        assert stats.mean_tokens == 400.0
        assert stats.error_rate == 0.0
        assert stats.has_write_tools is False

    def test_has_write_tools_set_when_any_member_writes(self) -> None:
        bursts = [
            _burst(tools=["Read"]),
            _burst(tools=["Edit"]),
        ]
        stats = _aggregate_burst_stats(bursts)
        assert stats.has_write_tools is True

    def test_empty_bursts_returns_zero_stats(self) -> None:
        stats = _aggregate_burst_stats([])
        assert stats.invocation_count == 0
        assert stats.mean_tokens == 0.0

    def test_error_rate_zero_for_all_success_cluster(self) -> None:
        """All bursts have ``tool_result_errors=0`` → ``error_rate=0.0``
        (preserves prior behavior on clusters without errors)."""
        bursts = [
            _burst(tools=["Read", "Read"], tool_result_errors=0),
            _burst(tools=["Read", "Bash"], tool_result_errors=0),
        ]
        assert _aggregate_burst_stats(bursts).error_rate == 0.0

    def test_error_rate_mean_of_per_burst_rates(self) -> None:
        """``error_rate`` is the mean of per-burst rates, not pooled
        (matches ``model_routing.py:166-167`` and
        ``_complexity.py:177-178`` conventions so the same
        ``_COMPLEX_MIN_ERROR_RATE = 0.20`` threshold means the same
        thing on both surfaces)."""
        # Burst A: 1 error / 4 tools → rate 0.25
        # Burst B: 1 error / 2 tools → rate 0.5
        # Mean of rates = 0.375. Pooled would be 2/6 = 0.333. They differ.
        bursts = [
            _burst(tools=["Read"] * 4, tool_result_errors=1),
            _burst(tools=["Read"] * 2, tool_result_errors=1),
        ]
        assert _aggregate_burst_stats(bursts).error_rate == 0.375

    def test_error_rate_one_for_all_error_cluster(self) -> None:
        bursts = [
            _burst(tools=["Bash"] * 3, tool_result_errors=3),
            _burst(tools=["Bash"] * 2, tool_result_errors=2),
        ]
        assert _aggregate_burst_stats(bursts).error_rate == 1.0

    def test_error_rate_zero_for_burst_with_no_tools(self) -> None:
        """Defensive: a burst with empty ``tool_use_blocks`` contributes
        rate 0.0 to the mean (avoids division by zero). In practice
        ``_OpenBurst.finalize`` won't emit such a burst, but guard the
        aggregation function regardless."""
        empty = ToolBurst(
            preceding_user_text="x",
            assistant_text="y",
            tool_use_blocks=[],
            usage=Usage(),
        )
        bursts = [
            _burst(tools=["Read"] * 2, tool_result_errors=2),
            empty,
        ]
        # Mean of [1.0, 0.0] = 0.5
        assert _aggregate_burst_stats(bursts).error_rate == 0.5

    def test_error_rate_triggers_complex_classification(self) -> None:
        """Issue #264 motivation: a cluster with mean tool calls and
        mean tokens below the complex thresholds but ``error_rate`` above
        ``_COMPLEX_MIN_ERROR_RATE`` should classify as ``complex``.
        Pre-fix this never fired because ``error_rate`` was always 0.0."""
        from agentfluent.diagnostics._complexity import (
            _COMPLEX_MIN_ERROR_RATE,
            classify_complexity,
        )
        # Two bursts, each with 3 tools, 2 of which errored → per-burst
        # rate 2/3 ≈ 0.667 each → mean 0.667 (well above 0.20 threshold).
        # mean_tool_calls=3 is below the complex tool-count gate; the
        # error_rate is what should drive the "complex" classification.
        bursts = [
            _burst(tools=["Bash"] * 3, tool_result_errors=2,
                   input_tokens=50, output_tokens=50),
            _burst(tools=["Bash"] * 3, tool_result_errors=2,
                   input_tokens=50, output_tokens=50),
        ]
        stats = _aggregate_burst_stats(bursts)
        assert stats.error_rate > _COMPLEX_MIN_ERROR_RATE
        assert classify_complexity(stats) == "complex"


class TestToolSequenceSummary:
    def test_picks_modal_sequence(self) -> None:
        bursts = [
            _burst(tools=["Read", "Bash"]),
            _burst(tools=["Read", "Bash"]),
            _burst(tools=["Read", "Bash"]),
            _burst(tools=["Edit", "Write"]),
        ]
        assert _tool_sequence_summary(bursts) == ["Read", "Bash"]

    def test_empty_bursts_returns_empty_list(self) -> None:
        assert _tool_sequence_summary([]) == []

    def test_bursts_without_tool_calls_returns_empty_list(self) -> None:
        bursts = [_burst(tools=[]), _burst(tools=[])]
        assert _tool_sequence_summary(bursts) == []


class TestClassifyConfidence:
    def test_high_requires_size_and_cohesion(self) -> None:
        assert _classify_confidence(10, 0.50) == "high"
        assert _classify_confidence(9, 0.95) == "medium"  # size guard

    def test_medium_at_realistic_cohesion(self) -> None:
        assert _classify_confidence(5, 0.40) == "medium"

    def test_low_below_medium_floor(self) -> None:
        assert _classify_confidence(5, 0.30) == "low"


# ---------------------------------------------------------------------------
# cluster_bursts
# ---------------------------------------------------------------------------


class TestClusterBursts:
    def test_two_distinct_patterns_produce_two_clusters(self) -> None:
        bursts = [_pytest_burst(i) for i in range(6)] + [
            _pr_burst(i) for i in range(6)
        ]
        clusters = cluster_bursts(bursts, min_cluster_size=5)
        assert len(clusters) == 2
        # Each cluster covers all 6 of its kind (clean separation).
        sizes = sorted(len(c.members) for c in clusters)
        assert sizes == [6, 6]
        # Top terms reflect the dominant vocabulary.
        all_top_terms = {term for c in clusters for term in c.top_terms}
        assert "pytest" in all_top_terms
        # PR-cluster surfaces some PR-flavored term ("pull", "request",
        # "summarize", "reviewers", or "diff" — TF-IDF picks one).
        pr_terms = {"pull", "request", "summarize", "reviewers", "diff"}
        assert all_top_terms & pr_terms

    def test_below_min_cluster_size_returns_empty(self) -> None:
        bursts = [_pytest_burst(i) for i in range(3)]
        assert cluster_bursts(bursts, min_cluster_size=5) == []

    def test_sklearn_missing_raises(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(parent_workload, "SKLEARN_AVAILABLE", False)
        with pytest.raises(SklearnMissingError):
            cluster_bursts([_pytest_burst(i) for i in range(6)])


# ---------------------------------------------------------------------------
# generate_offload_candidate
# ---------------------------------------------------------------------------


class TestGenerateOffloadCandidate:
    def _cluster(
        self,
        members: list[ToolBurst] | None = None,
        top_terms: list[str] | None = None,
        cohesion: float = 0.55,
    ) -> BurstCluster:
        return BurstCluster(
            members=members if members is not None else [_pytest_burst(i) for i in range(6)],
            top_terms=top_terms if top_terms is not None else ["pytest", "tests", "run"],
            cohesion_score=cohesion,
        )

    def test_basic_candidate_population(self) -> None:
        candidate = generate_offload_candidate(
            self._cluster(),
            parent_model="claude-opus-4-7",
            parent_pricing=_OPUS,
            alt_pricing=_SONNET,
        )
        assert candidate.parent_model == "claude-opus-4-7"
        assert candidate.alternative_model == "claude-sonnet-4-6"
        assert candidate.cluster_size == 6
        assert candidate.target_kind == "subagent"
        assert candidate.subagent_draft is not None
        assert candidate.subagent_draft.cluster_size == 6
        assert candidate.skill_draft is None
        assert candidate.tool_sequence_summary == ["Bash", "Read", "Read"]

    def test_positive_savings_with_modest_cache_read(self) -> None:
        members = [
            _burst(
                tools=["Bash", "Read"],
                input_tokens=2000, output_tokens=2000,
                cache_creation_input_tokens=0,
                cache_read_input_tokens=500,
            ),
        ] * 6
        candidate = generate_offload_candidate(
            BurstCluster(members=members, top_terms=["pytest"], cohesion_score=0.55),
            parent_model="claude-opus-4-7",
            parent_pricing=_OPUS,
            alt_pricing=_SONNET,
        )
        assert candidate.estimated_savings_usd > 0
        assert candidate.cost_note == ""

    def test_negative_savings_preserved_with_cost_note(self) -> None:
        # Cache-read-dominated burst: alt-model loses cache benefit so
        # savings goes negative. #189-C invariant: the sign is preserved
        # rather than clamped, and cost_note carries the warning.
        members = [
            _burst(
                tools=["Read"],
                input_tokens=100, output_tokens=100,
                cache_creation_input_tokens=0,
                cache_read_input_tokens=100_000,
            ),
        ] * 6
        candidate = generate_offload_candidate(
            BurstCluster(
                members=members, top_terms=["analyze"], cohesion_score=0.45,
            ),
            parent_model="claude-opus-4-7",
            parent_pricing=_OPUS,
            alt_pricing=_SONNET,
        )
        assert candidate.estimated_savings_usd < 0
        assert "cache" in candidate.cost_note.lower()

    def test_unknown_pricing_returns_zero_costs(self) -> None:
        candidate = generate_offload_candidate(
            self._cluster(),
            parent_model="claude-opus-4-7",
            parent_pricing=None,
            alt_pricing=None,
        )
        assert candidate.estimated_parent_cost_usd == 0.0
        assert candidate.estimated_savings_usd == 0.0

    def test_subagent_draft_tools_frequency_filtered(self) -> None:
        # 5/6 bursts use Read; 1/6 uses Bash. Threshold filters Bash out.
        members = [_burst(tools=["Read"]) for _ in range(5)] + [
            _burst(tools=["Read", "Bash"]),
        ]
        candidate = generate_offload_candidate(
            BurstCluster(members=members, top_terms=["t"], cohesion_score=0.45),
            parent_model="claude-opus-4-7",
            parent_pricing=_OPUS,
            alt_pricing=_SONNET,
        )
        assert candidate.subagent_draft is not None
        assert candidate.subagent_draft.tools == ["Read"]
        assert candidate.subagent_draft.tools_observed == ["Bash", "Read"]

    def test_yaml_draft_renders_offload_preamble(self) -> None:
        candidate = generate_offload_candidate(
            self._cluster(),
            parent_model="claude-opus-4-7",
            parent_pricing=_OPUS,
            alt_pricing=_SONNET,
        )
        yaml_text = candidate.yaml_draft
        # Offload-specific framing the architect required (Q-followup):
        assert "parent-thread offload candidate" in yaml_text
        assert "claude-opus-4-7" in yaml_text
        assert "claude-sonnet-4-6" in yaml_text
        # Frontmatter from subagent_draft still rendered:
        assert "description:" in yaml_text
        assert "model:" in yaml_text

    def test_yaml_draft_negative_savings_renders_cost_warning(self) -> None:
        members = [
            _burst(
                input_tokens=10, output_tokens=10,
                cache_read_input_tokens=100_000,
                tools=["Read"],
            ),
        ] * 6
        candidate = generate_offload_candidate(
            BurstCluster(members=members, top_terms=["x"], cohesion_score=0.45),
            parent_model="claude-opus-4-7",
            parent_pricing=_OPUS,
            alt_pricing=_SONNET,
        )
        yaml_text = candidate.yaml_draft
        assert "cost MORE" in yaml_text
        # The cost_note (from generate_offload_candidate) reaches the YAML.
        assert "cache" in yaml_text.lower()

    def test_yaml_draft_empty_when_no_subagent_draft(self) -> None:
        # Defensive: a hand-constructed candidate without subagent_draft
        # renders empty rather than blowing up. Production code always
        # populates subagent_draft, but JSON deserialization could yield
        # this shape.
        candidate = OffloadCandidate(
            name="x", description="x", confidence="low",
            cluster_size=0, cohesion_score=0.0,
            alternative_model="claude-sonnet-4-6",
            subagent_draft=None,
        )
        assert candidate.yaml_draft == ""


# ---------------------------------------------------------------------------
# JSON round-trip — sub-issue E will rely on this when wiring
# DiagnosticsResult.offload_candidates into the JSON envelope.
# ---------------------------------------------------------------------------


class TestJsonRoundTrip:
    def test_offload_candidate_round_trips(self) -> None:
        bursts = [_pytest_burst(i) for i in range(6)]
        clusters = cluster_bursts(bursts, min_cluster_size=5)
        assert clusters
        candidate = generate_offload_candidate(
            clusters[0],
            parent_model="claude-opus-4-7",
            parent_pricing=_OPUS,
            alt_pricing=_SONNET,
        )
        payload = candidate.model_dump(mode="json")
        # skill_draft is null in v0.5 — the contract for the v0.6 migration.
        assert payload["skill_draft"] is None
        # Round-trip through json.dumps + json.loads to catch any
        # non-JSON-native types slipping through computed_field.
        rehydrated = OffloadCandidate.model_validate(
            json.loads(json.dumps(payload)),
        )
        assert rehydrated.name == candidate.name
        assert rehydrated.estimated_savings_usd == candidate.estimated_savings_usd
        assert rehydrated.subagent_draft is not None
        assert isinstance(rehydrated.subagent_draft, DelegationSuggestion)

    def test_skill_scaffold_is_intentionally_empty(self) -> None:
        # Sentinel test: SkillScaffold has no fields in v0.5. If a v0.6
        # PR adds fields to it, this test breaks loudly so the migration
        # plan is reconsidered explicitly.
        scaffold = SkillScaffold()
        assert scaffold.model_dump() == {}


# ---------------------------------------------------------------------------
# Default constant lock — calibration sweep in sub-issue F may revisit.
# ---------------------------------------------------------------------------


def test_default_burst_cluster_size_is_five() -> None:
    assert DEFAULT_BURST_CLUSTER_SIZE == 5
