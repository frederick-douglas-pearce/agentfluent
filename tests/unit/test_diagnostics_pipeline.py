"""Tests for the diagnostics orchestration pipeline.

Covers: metadata/trace signal dedup, subagent_trace_count semantics,
backward compatibility of the public `run_diagnostics` import path,
and v0.2 output-shape regression for trace-less sessions.
"""

import json
import logging
from pathlib import Path

import pytest

from agentfluent.agents.models import AgentInvocation
from agentfluent.config.mcp_discovery import _load_json
from agentfluent.config.models import AgentConfig, Scope, Severity
from agentfluent.core.session import ContentBlock, SessionMessage
from agentfluent.diagnostics import TRACE_SIGNAL_TYPES
from agentfluent.diagnostics.delegation import MODEL_OPUS
from agentfluent.diagnostics.mcp_assessment import McpToolCall
from agentfluent.diagnostics.models import (
    DelegationSuggestion,
    DiagnosticSignal,
    SignalType,
)
from agentfluent.diagnostics.pipeline import (
    _enrich_dedup_with_mismatches,
    run_diagnostics,
)
from agentfluent.diagnostics.quality_signals import _FILE_REWORK_THRESHOLD
from agentfluent.traces.models import (
    RetrySequence,
    SubagentToolCall,
    SubagentTrace,
)


def _inv(
    agent_type: str = "pm",
    output_text: str = "",
    trace: SubagentTrace | None = None,
) -> AgentInvocation:
    return AgentInvocation(
        agent_type=agent_type,
        description="test",
        prompt="do something",
        tool_use_id="toolu_01",
        output_text=output_text,
        trace=trace,
    )


def _stuck_trace(agent_type: str = "pm") -> SubagentTrace:
    calls = [
        SubagentToolCall(
            tool_name="Bash",
            input_summary="ls /missing",
            result_summary="not found",
            is_error=True,
        )
        for _ in range(5)
    ]
    return SubagentTrace(
        agent_id="agent-x",
        agent_type=agent_type,
        delegation_prompt="find",
        tool_calls=calls,
        retry_sequences=[
            RetrySequence(
                tool_name="Bash",
                attempts=5,
                tool_call_indices=[0, 1, 2, 3, 4],
                first_error_message="not found",
                eventual_success=False,
            ),
        ],
    )


class TestDedup:
    def test_metadata_error_pattern_suppressed_when_trace_signal_same_agent_type(
        self,
    ) -> None:
        inv = _inv(
            agent_type="pm",
            output_text="The operation failed with a permission denied error.",
            trace=_stuck_trace(agent_type="pm"),
        )
        result = run_diagnostics([inv])
        by_type = {s.signal_type for s in result.signals}
        assert SignalType.STUCK_PATTERN in by_type
        # ERROR_PATTERN for pm is suppressed.
        assert not any(
            s.signal_type == SignalType.ERROR_PATTERN and s.agent_type == "pm"
            for s in result.signals
        )

    def test_metadata_error_pattern_retained_for_other_agent_type(self) -> None:
        # agent A has a trace, agent B doesn't; B's metadata signals stay.
        inv_a = _inv(agent_type="pm", trace=_stuck_trace(agent_type="pm"))
        inv_b = _inv(agent_type="architect", output_text="permission denied")
        result = run_diagnostics([inv_a, inv_b])
        assert any(
            s.signal_type == SignalType.ERROR_PATTERN and s.agent_type == "architect"
            for s in result.signals
        )

    def test_no_trace_signals_all_metadata_retained(self) -> None:
        inv = _inv(output_text="failed to load the config")
        result = run_diagnostics([inv])
        error_signals = [
            s for s in result.signals if s.signal_type == SignalType.ERROR_PATTERN
        ]
        assert len(error_signals) >= 1

    def test_token_outlier_not_suppressed_by_trace_signal(self) -> None:
        # IQR-based detection (#186 P2) needs OUTLIER_MIN_SAMPLE peers
        # to compute Q3/IQR. One outlier carries a trace; TOKEN_OUTLIER
        # must survive the dedup pass alongside STUCK_PATTERN.
        peers = [
            AgentInvocation(
                agent_type="pm", description=f"p{i}", prompt="x",
                tool_use_id=f"t-peer-{i}", output_text="",
                total_tokens=100 + 10 * i, tool_uses=1,
            )
            for i in range(9)
        ]
        with_trace = AgentInvocation(
            agent_type="pm", description="a", prompt="a",
            tool_use_id="t-trace", output_text="",
            total_tokens=200, tool_uses=1, trace=_stuck_trace(agent_type="pm"),
        )
        outlier_inv = AgentInvocation(
            agent_type="pm", description="c", prompt="c",
            tool_use_id="t-outlier", output_text="",
            total_tokens=100_000, tool_uses=1,
        )
        result = run_diagnostics([*peers, with_trace, outlier_inv])
        assert any(s.signal_type == SignalType.TOKEN_OUTLIER for s in result.signals)
        assert any(s.signal_type == SignalType.STUCK_PATTERN for s in result.signals)

    def test_dedup_happens_before_correlation(self) -> None:
        # An ERROR_PATTERN "permission denied" signal normally triggers
        # AccessErrorRule. When suppressed by a trace signal, its
        # recommendation must also be absent.
        inv = _inv(
            agent_type="pm",
            output_text="permission denied when accessing file",
            trace=_stuck_trace(agent_type="pm"),
        )
        result = run_diagnostics([inv])
        # StuckPatternRule should produce a recommendation for the trace.
        stuck_recs = [
            r for r in result.recommendations
            if r.signal_types == [SignalType.STUCK_PATTERN]
        ]
        assert len(stuck_recs) == 1
        # AccessErrorRule's recommendation (from metadata ERROR_PATTERN)
        # should NOT appear.
        assert not any(
            r.signal_types == [SignalType.ERROR_PATTERN] and r.agent_type == "pm"
            for r in result.recommendations
        )


class TestSubagentTraceCount:
    def test_counts_parsed_linked_traces(self) -> None:
        inv_a = _inv(trace=_stuck_trace())
        inv_b = _inv(trace=_stuck_trace())
        result = run_diagnostics([inv_a, inv_b])
        assert result.subagent_trace_count == 2

    def test_zero_when_no_invocations(self) -> None:
        result = run_diagnostics([])
        assert result.subagent_trace_count == 0

    def test_mix_of_linked_and_unlinked(self) -> None:
        inv_linked = _inv(trace=_stuck_trace())
        inv_unlinked = _inv(trace=None)
        result = run_diagnostics([inv_linked, inv_unlinked])
        assert result.subagent_trace_count == 1


class TestBackwardCompatImport:
    def test_run_diagnostics_importable_from_diagnostics_package(self) -> None:
        from agentfluent.diagnostics import run_diagnostics as rd_from_pkg

        # Same callable as pipeline.run_diagnostics.
        assert rd_from_pkg is run_diagnostics

    def test_trace_signal_types_exported(self) -> None:
        assert SignalType.STUCK_PATTERN in TRACE_SIGNAL_TYPES
        assert SignalType.ERROR_PATTERN not in TRACE_SIGNAL_TYPES


class TestAgentConfigScanError:
    def test_oserror_in_scan_agents_is_swallowed(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """OSError from scan_agents must not crash the pipeline.

        A user with unreadable agent directories still needs diagnostics
        — the failure path is a debug log, not a raise.
        """
        def raise_oserror(*_a: object, **_kw: object) -> list[object]:
            raise OSError("simulated permission error")

        monkeypatch.setattr(
            "agentfluent.diagnostics.pipeline.scan_agents", raise_oserror,
        )
        # Pipeline completes without raising; correlator still runs,
        # but all recommendations lack a config_file reference since
        # configs=None.
        result = run_diagnostics([_inv(output_text="operation failed")])
        assert any(s.signal_type == SignalType.ERROR_PATTERN for s in result.signals)
        assert all(r.config_file == "" for r in result.recommendations)


class TestV02Regression:
    """Trace-less sessions must produce v0.2-shaped output.

    A session with no subagent traces (all `inv.trace is None`) exists in
    two scenarios: (1) older sessions predating trace capture, (2)
    sessions where no Agent tool was invoked. Both paths must yield
    metadata-only signals and `subagent_trace_count == 0` — no trace
    signal types, no regressions relative to pre-#107 behavior.
    """

    def test_no_trace_signals_when_invocations_lack_traces(self) -> None:
        inv = _inv(output_text="permission denied on /etc/passwd")
        result = run_diagnostics([inv])
        # Only metadata ERROR_PATTERN should appear; no trace-level types.
        assert not any(s.signal_type in TRACE_SIGNAL_TYPES for s in result.signals)
        assert any(s.signal_type == SignalType.ERROR_PATTERN for s in result.signals)

    def test_subagent_trace_count_zero_when_no_traces(self) -> None:
        invs = [_inv(output_text=""), _inv(output_text="failed")]
        result = run_diagnostics(invs)
        assert result.subagent_trace_count == 0

    def test_empty_invocations_produces_empty_result(self) -> None:
        result = run_diagnostics([])
        assert result.signals == []
        assert result.recommendations == []
        assert result.subagent_trace_count == 0


class TestDelegationSuggestions:
    """Pipeline wiring for the clustering feature. Real clustering
    behavior is covered in test_delegation.py; this class just confirms
    the suggestions surface on DiagnosticsResult and that the sklearn-
    unavailable path is silently skipped (not raised)."""

    # 10 general-purpose delegations representing a single recurring
    # pattern — reading + summarizing different files. Each has distinct
    # text (as a real parent agent would generate) but shares the
    # dominant content words. Combined length clears MIN_TEXT_TOKENS.
    _GP_INVS = [
        AgentInvocation(
            agent_type="general-purpose",
            description=f"read file {target} and summarize the public surface",
            prompt=(
                f"Read the file {target} from the repository and produce "
                "a concise summary of the main functions, classes, and "
                f"dependencies. Focus on the public surface of {target}, "
                "describe how callers use it, list any exported constants "
                "and types, note relationships to adjacent modules, and "
                "surface any documented invariants or preconditions that "
                "a consumer should be aware of when integrating with it."
            ),
            tool_use_id=f"tool_{i}",
        )
        for i, target in enumerate([
            "auth/tokens.py",
            "auth/sessions.py",
            "db/migrations.py",
            "db/schema.py",
            "api/handlers.py",
            "api/routes.py",
            "core/config.py",
            "core/logging.py",
            "utils/strings.py",
            "utils/paths.py",
        ])
    ]

    def test_pipeline_populates_delegation_suggestions(self) -> None:
        pytest.importorskip("sklearn")
        result = run_diagnostics(self._GP_INVS)
        assert len(result.delegation_suggestions) >= 1

    def test_skipped_reason_is_none_when_suggestions_present(self) -> None:
        pytest.importorskip("sklearn")
        result = run_diagnostics(self._GP_INVS)
        assert result.delegation_suggestions_skipped_reason is None

    def test_pipeline_empty_suggestions_when_sklearn_missing(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            "agentfluent.diagnostics.pipeline.SKLEARN_AVAILABLE", False,
        )
        # Silent skip — no raise, no suggestions, other output intact.
        result = run_diagnostics(self._GP_INVS)
        assert result.delegation_suggestions == []
        assert (
            result.delegation_suggestions_skipped_reason
            == "sklearn_not_installed"
        )

    def test_skipped_reason_insufficient_invocations(self) -> None:
        pytest.importorskip("sklearn")
        # Two GP invocations is well under DEFAULT_MIN_CLUSTER_SIZE.
        result = run_diagnostics(self._GP_INVS[:2])
        assert result.delegation_suggestions == []
        assert (
            result.delegation_suggestions_skipped_reason
            == "insufficient_invocations"
        )

    def test_skipped_reason_no_clusters_above_min_size(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # GP invocation count is above min_cluster_size, so the pipeline
        # commits to running suggest_delegations. Monkeypatch it to return
        # [] — this is the union of "no clusters formed" and "all drafts
        # were deduped against existing configs," both of which the
        # public reason field collapses to ``no_clusters_above_min_size``.
        pytest.importorskip("sklearn")
        monkeypatch.setattr(
            "agentfluent.diagnostics.pipeline.suggest_delegations",
            lambda *args, **kwargs: [],
        )
        result = run_diagnostics(self._GP_INVS)
        assert result.delegation_suggestions == []
        assert (
            result.delegation_suggestions_skipped_reason
            == "no_clusters_above_min_size"
        )


class TestOffloadCandidates:
    """Pipeline wiring for #189's parent-thread offload-candidate path.
    Real clustering + cost behavior is covered in
    test_parent_workload_cluster.py; this class just confirms candidates
    surface on DiagnosticsResult, ``parent_messages=None`` short-circuits
    cleanly, and the sklearn-unavailable path is silently skipped.
    """

    @staticmethod
    def _two_pattern_messages() -> list:
        # Two distinct burst patterns × 6 each: pytest-flavored bursts and
        # PR-review-flavored bursts. Constructed via SessionMessage so we
        # exercise the full extract → filter → cluster path. Long-tailed
        # text and multi-tool calls clear MIN_BURST_TOOLS and
        # MIN_BURST_TEXT_TOKENS for both patterns.
        from agentfluent.core.session import (
            ContentBlock,
            SessionMessage,
            Usage,
        )
        pytest_tail = (
            " collect coverage with pytest-cov and emit xunit results per "
            "pytest module; use pytest fixtures and parametrize markers "
            "to exercise the edge cases reported by pytest collectors."
        )
        pr_tail = (
            " summarize the pull request diff, list changed files, "
            "identify any regressions in the PR description, and "
            "highlight review comments from prior reviewers."
        )
        messages = []
        idx = 0
        for kind, tail, tools in [
            ("pytest run", pytest_tail, ["Bash", "Read", "Read"]),
            ("pull request", pr_tail, ["Bash", "Read", "Grep"]),
        ]:
            for i in range(6):
                messages.append(
                    SessionMessage(
                        type="user",
                        content_blocks=[
                            ContentBlock(
                                type="text",
                                text=f"do a {kind} cycle {i}{tail}",
                            ),
                        ],
                    ),
                )
                blocks = [
                    ContentBlock(
                        type="text",
                        text=f"running {kind} {i}.{tail}",
                    ),
                ]
                for j, name in enumerate(tools):
                    blocks.append(
                        ContentBlock(
                            type="tool_use",
                            id=f"toolu_{idx}_{j}",
                            name=name,
                            input={},
                        ),
                    )
                messages.append(
                    SessionMessage(
                        type="assistant",
                        content_blocks=blocks,
                        model="claude-opus-4-7",
                        usage=Usage(input_tokens=100, output_tokens=200),
                    ),
                )
                idx += 1
        return messages

    def test_no_parent_messages_yields_empty_offload_candidates(self) -> None:
        result = run_diagnostics([], parent_messages=None)
        assert result.offload_candidates == []

    def test_empty_parent_messages_yields_empty_offload_candidates(
        self,
    ) -> None:
        result = run_diagnostics([], parent_messages=[])
        assert result.offload_candidates == []

    def test_pipeline_populates_offload_candidates(self) -> None:
        pytest.importorskip("sklearn")
        messages = self._two_pattern_messages()
        result = run_diagnostics([], parent_messages=messages)
        assert len(result.offload_candidates) == 2
        # Subagent draft is populated end-to-end.
        for candidate in result.offload_candidates:
            assert candidate.subagent_draft is not None
            assert candidate.subagent_draft.cluster_size == 6
            assert candidate.parent_model == "claude-opus-4-7"
            assert candidate.skill_draft is None

    def test_pipeline_empty_offload_when_sklearn_missing(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            "agentfluent.diagnostics.pipeline.SKLEARN_AVAILABLE", False,
        )
        result = run_diagnostics(
            [], parent_messages=self._two_pattern_messages(),
        )
        assert result.offload_candidates == []
        # Delegation path is also gated by the same SKLEARN flag — confirm
        # the shared gate suppressed both, matching the docstring contract.
        assert result.delegation_suggestions == []


class TestModelRoutingWiring:
    """Sanity wiring check — the real model-routing logic is covered by
    test_model_routing.py. Here we just verify signals flow through
    run_diagnostics and become correlator recommendations."""

    def test_model_mismatch_signal_produces_target_model_recommendation(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # 5 "pm" invocations with simple workload but declaring Opus —
        # overspec case. Stub scan_agents to return a config that
        # declares model=Opus; otherwise the agent gets skipped.
        def fake_scan(_scope: str) -> list[AgentConfig]:
            return [
                AgentConfig(
                    name="pm",
                    file_path=Path("/home/user/.claude/agents/pm.md"),
                    scope=Scope.USER,
                    model=MODEL_OPUS,
                ),
            ]
        monkeypatch.setattr(
            "agentfluent.diagnostics.pipeline.scan_agents", fake_scan,
        )

        invs = [
            AgentInvocation(
                agent_type="pm",
                description="task",
                prompt="do it",
                tool_use_id=f"t{i}",
                total_tokens=500,
                tool_uses=2,
            )
            for i in range(5)
        ]
        result = run_diagnostics(invs)
        assert any(s.signal_type == SignalType.MODEL_MISMATCH for s in result.signals)
        assert any(r.target == "model" for r in result.recommendations)


class TestCrossReferenceEnrichment:
    """When a DelegationSuggestion's matched_agent has a live
    MODEL_MISMATCH signal, its dedup_note is enriched with the
    mismatch summary so the user sees one consolidated breadcrumb.
    The standalone MODEL_MISMATCH signal + recommendation are
    unaffected — they still surface independently in their own
    section (different user-facing context)."""

    def _mismatch_signal(
        self,
        agent_type: str = "pm",
        mismatch_type: str = "overspec",
        savings: float | None = 12.50,
    ) -> DiagnosticSignal:
        return DiagnosticSignal(
            signal_type=SignalType.MODEL_MISMATCH,
            severity=Severity.WARNING,
            agent_type=agent_type,
            message=f"{mismatch_type}'d model on {agent_type}",
            detail={
                "mismatch_type": mismatch_type,
                "current_model": "claude-opus-4-7",
                "recommended_model": "claude-haiku-4-5",
                "complexity_tier": "simple",
                "invocation_count": 8,
                "estimated_savings_usd": savings,
            },
        )

    def _suggestion(
        self,
        matched_agent: str = "pm",
        dedup_note: str = "suppressed — already covered by 'pm' (similarity 0.85)",
    ) -> DelegationSuggestion:
        return DelegationSuggestion(
            name="py-tests",
            description="Handles delegations related to: pytest, tests, run.",
            model="claude-sonnet-4-6",
            tools=["Read", "Grep"],
            prompt_template="You run pytest tests.",
            confidence="medium",
            cluster_size=5,
            cohesion_score=0.7,
            top_terms=["pytest"],
            dedup_note=dedup_note,
            matched_agent=matched_agent,
        )

    def test_dedup_match_plus_mismatch_enriches_dedup_note(self) -> None:
        suggestions = [self._suggestion(matched_agent="pm")]
        signals = [self._mismatch_signal(agent_type="pm")]
        _enrich_dedup_with_mismatches(suggestions, signals)
        note = suggestions[0].dedup_note
        # Original dedup prefix preserved.
        assert "suppressed" in note
        assert "pm" in note
        # Mismatch phrase appended.
        assert "overspec" in note
        assert "claude-haiku-4-5" in note
        assert "12.50" in note  # savings phrase present

    def test_mismatch_without_savings_omits_savings_clause(self) -> None:
        suggestions = [self._suggestion(matched_agent="pm")]
        signals = [self._mismatch_signal(agent_type="pm", savings=None)]
        _enrich_dedup_with_mismatches(suggestions, signals)
        note = suggestions[0].dedup_note
        assert "claude-haiku-4-5" in note
        assert "savings" not in note.lower()

    def test_dedup_match_without_mismatch_leaves_note_unchanged(self) -> None:
        suggestions = [self._suggestion(matched_agent="pm")]
        original = suggestions[0].dedup_note
        # Signal targets a different agent — no cross-reference.
        signals = [self._mismatch_signal(agent_type="architect")]
        _enrich_dedup_with_mismatches(suggestions, signals)
        assert suggestions[0].dedup_note == original

    def test_mismatch_without_matching_dedup_leaves_suggestion_untouched(
        self,
    ) -> None:
        # Suggestion wasn't deduped — matched_agent is "".
        sug = self._suggestion(matched_agent="", dedup_note="")
        signals = [self._mismatch_signal(agent_type="pm")]
        _enrich_dedup_with_mismatches([sug], signals)
        assert sug.dedup_note == ""

    def test_empty_inputs_are_safe(self) -> None:
        _enrich_dedup_with_mismatches([], [])
        _enrich_dedup_with_mismatches([self._suggestion()], [])
        _enrich_dedup_with_mismatches([], [self._mismatch_signal()])

    def test_case_insensitive_agent_matching(self) -> None:
        suggestions = [self._suggestion(matched_agent="PM")]
        signals = [self._mismatch_signal(agent_type="pm")]
        _enrich_dedup_with_mismatches(suggestions, signals)
        assert "claude-haiku-4-5" in suggestions[0].dedup_note

    def test_enrichment_strips_trailing_punctuation_from_dedup_note(self) -> None:
        # Regression guard: if the dedup_note format ever gains a
        # trailing period, the appended mismatch phrase should still
        # produce a well-formed single-sentence output, not "..). Note: ..".
        suggestions = [
            self._suggestion(
                matched_agent="pm",
                dedup_note="suppressed — already covered by 'pm'.",
            ),
        ]
        signals = [self._mismatch_signal(agent_type="pm")]
        _enrich_dedup_with_mismatches(suggestions, signals)
        note = suggestions[0].dedup_note
        assert ".." not in note  # no double-period from naive concat
        assert "pm" in note and "claude-haiku-4-5" in note


class TestMcpAuditWiring:
    """run_diagnostics MCP-audit gate: runs only when caller passes
    explicit MCP context. Prevents programmatic callers from silently
    picking up the user's real ~/.claude.json.
    """

    @pytest.fixture(autouse=True)
    def _isolate_home(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Redirect Path.home so audit can never accidentally read the
        # contributor's real ~/.claude.json. Clear _load_json's lru_cache
        # before and after so tmp_path reuse across tests doesn't return
        # a stale parsed dict from a prior test's file.
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        _load_json.cache_clear()
        yield
        _load_json.cache_clear()

    def test_no_mcp_context_skips_audit(self) -> None:
        # A caller that passes nothing should see no MCP audit output,
        # even if a real ~/.claude.json would return servers.
        result = run_diagnostics([_inv()])
        assert all(
            s.signal_type.value not in ("mcp_unused_server", "mcp_missing_server")
            for s in result.signals
        )

    def test_mcp_tool_calls_provided_triggers_audit(self) -> None:
        # Providing mcp_tool_calls with errors fires MCP_MISSING_SERVER
        # even with no configured servers.
        calls = [
            McpToolCall(
                server_name="unknown_server", tool_name="x", is_error=True,
            ),
            McpToolCall(
                server_name="unknown_server", tool_name="y", is_error=True,
            ),
        ]
        result = run_diagnostics([_inv()], mcp_tool_calls=calls)
        assert any(
            s.signal_type == SignalType.MCP_MISSING_SERVER
            for s in result.signals
        )

    def test_configured_servers_read_when_claude_config_dir_passed(
        self, tmp_path: Path,
    ) -> None:
        # Passing claude_config_dir is enough to trigger audit even
        # without mcp_tool_calls — caller opts in explicitly.
        claude_root = tmp_path / ".claude"
        claude_root.mkdir()
        claude_json = tmp_path / ".claude.json"
        claude_json.write_text(
            json.dumps({"mcpServers": {"unused-svc": {"command": "x"}}}),
        )

        result = run_diagnostics([_inv()], claude_config_dir=claude_root)

        unused = [
            s for s in result.signals
            if s.signal_type == SignalType.MCP_UNUSED_SERVER
        ]
        assert len(unused) == 1
        assert unused[0].detail["server_name"] == "unused-svc"


class TestQualitySignalsWiring:
    """``parent_messages`` opts the caller into quality-signal extraction.

    Programmatic callers that don't pass ``parent_messages`` (libraries,
    tests) silently skip. The CLI always passes it, so the user-visible
    path always gets quality signals.
    """

    @staticmethod
    def _correction_messages() -> list[SessionMessage]:
        return [
            SessionMessage(
                type="assistant",
                content_blocks=[
                    ContentBlock(type="text", text="Editing the file."),
                    ContentBlock(
                        type="tool_use",
                        id="toolu_w",
                        name="Edit",
                        input={"file_path": "/tmp/x.py"},
                    ),
                ],
            ),
            SessionMessage(
                type="user",
                content_blocks=[
                    ContentBlock(
                        type="text",
                        text="no, that's wrong, undo it",
                    ),
                ],
            ),
        ]

    def test_parent_messages_provided_emits_quality_signals(self) -> None:
        result = run_diagnostics(
            [_inv()], parent_messages=self._correction_messages(),
        )
        quality = [
            s for s in result.signals
            if s.signal_type == SignalType.USER_CORRECTION
        ]
        assert len(quality) == 1

    def test_parent_messages_none_quiet_skips_with_debug_log(
        self, caplog: pytest.LogCaptureFixture,
    ) -> None:
        with caplog.at_level(
            logging.DEBUG, logger="agentfluent.diagnostics.pipeline",
        ):
            result = run_diagnostics([_inv()])
        assert not any(
            s.signal_type == SignalType.USER_CORRECTION for s in result.signals
        )
        assert any(
            "quality signals skipped: parent_messages=None" in record.message
            for record in caplog.records
        )

    def test_parent_messages_empty_list_skips_extraction(self) -> None:
        """Empty parent_messages goes through the extractor (which returns
        ``[]``) without firing the quiet-skip log."""
        result = run_diagnostics([_inv()], parent_messages=[])
        assert not any(
            s.signal_type == SignalType.USER_CORRECTION for s in result.signals
        )

    def test_user_correction_and_file_rework_coexist(self) -> None:
        """Both quality signals fire on the same session when the
        evidence is present — they are independent observations
        (architect Q5 on #270)."""
        edit_block = ContentBlock(
            type="tool_use",
            id="toolu_e",
            name="Edit",
            input={"file_path": "/src/foo.py"},
        )
        # `_FILE_REWORK_THRESHOLD` assistant messages each with an Edit
        # on /src/foo.py — followed by a strong correction so
        # USER_CORRECTION fires too.
        messages: list[SessionMessage] = []
        for _ in range(_FILE_REWORK_THRESHOLD):
            messages.append(
                SessionMessage(type="assistant", content_blocks=[edit_block]),
            )
        messages.append(
            SessionMessage(
                type="user",
                content_blocks=[
                    ContentBlock(type="text", text="that's wrong, undo it"),
                ],
            ),
        )
        result = run_diagnostics([_inv()], parent_messages=messages)
        kinds = {s.signal_type for s in result.signals}
        assert SignalType.USER_CORRECTION in kinds
        assert SignalType.FILE_REWORK in kinds

    def test_all_three_quality_signals_coexist(self) -> None:
        """USER_CORRECTION + FILE_REWORK + REVIEWER_CAUGHT all fire on
        the same session when the evidence is present. Confirms #271
        plugged into the pipeline without breaking the existing
        cross-cutting detectors."""
        edit_block = ContentBlock(
            type="tool_use",
            id="toolu_e",
            name="Edit",
            input={"file_path": "/src/foo.py"},
        )
        substantive = (
            "I reviewed the change and found several blocker issues "
            "that must be addressed before merge. The function in "
            "src/foo.py does not handle the empty-input case and "
            "will raise an unexpected exception at runtime. Second "
            "concern: there is a security risk in the auth flow — "
            "credentials are logged at debug level which is a real "
            "vulnerability if log levels are misconfigured. Third "
            "warning: the test fixture in tests/test_foo.py mocks "
            "behavior that contradicts the production code path. "
            "Recommended fix: add input validation and redact "
            "credentials before logging."
        )
        review_inv = AgentInvocation(
            agent_type="architect",
            description="review",
            prompt="review the diff",
            tool_use_id="toolu_review",
            output_text=substantive,
        )

        messages: list[SessionMessage] = []
        for _ in range(_FILE_REWORK_THRESHOLD):
            messages.append(
                SessionMessage(type="assistant", content_blocks=[edit_block]),
            )
        messages.append(
            SessionMessage(
                type="user",
                content_blocks=[
                    ContentBlock(type="text", text="that's wrong, undo it"),
                ],
            ),
        )
        # The review's tool_result and the architect invocation must
        # both be present in the messages so the pipeline's own
        # extract_agent_invocations sees the review.
        messages.append(
            SessionMessage(
                type="assistant",
                content_blocks=[
                    ContentBlock(
                        type="tool_use",
                        id="toolu_review",
                        name="Agent",
                        input={
                            "subagent_type": "architect",
                            "description": "review",
                            "prompt": "review the diff",
                        },
                    ),
                ],
            ),
        )
        messages.append(
            SessionMessage(
                type="user",
                content_blocks=[
                    ContentBlock(
                        type="tool_result",
                        tool_use_id="toolu_review",
                        text=substantive,
                    ),
                ],
            ),
        )

        result = run_diagnostics(
            [_inv(), review_inv], parent_messages=messages,
        )
        kinds = {s.signal_type for s in result.signals}
        assert SignalType.USER_CORRECTION in kinds
        assert SignalType.FILE_REWORK in kinds
        assert SignalType.REVIEWER_CAUGHT in kinds
