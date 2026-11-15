"""Tests for the analytics pipeline orchestration."""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from agentfluent.analytics.agent_metrics import AgentMetrics, AgentTypeMetrics
from agentfluent.analytics.pipeline import (
    AnalysisResult,
    SessionAnalysis,
    _merge_agent_metrics,
    analyze_session,
    analyze_sessions,
)
from agentfluent.analytics.tokens import TokenMetrics
from agentfluent.analytics.tools import ToolMetrics
from tests._builders import assistant_message, user_message

WriteJSONL = Callable[[str, list[dict[str, Any]]], Path]


def _session(assistant_count: int, synthetic_count: int = 0) -> SessionAnalysis:
    """Build a minimal SessionAnalysis with given assistant + synthetic counts.

    ``assistant_count`` is the all-inclusive assistant-message count
    (including synthetics); ``synthetic_count`` is the subset netted out
    of ``model_turns`` (#507)."""
    return SessionAnalysis(
        session_path=Path("s.jsonl"),
        token_metrics=TokenMetrics(),
        tool_metrics=ToolMetrics(),
        agent_metrics=AgentMetrics(),
        assistant_message_count=assistant_count,
        synthetic_message_count=synthetic_count,
    )


class TestAnalyzeSession:
    def test_basic_session(self, basic_session_path: Path) -> None:
        result = analyze_session(basic_session_path)
        assert result.message_count > 0
        assert result.user_message_count > 0
        assert result.assistant_message_count > 0
        assert result.token_metrics.total_tokens > 0

    def test_agent_session(self, agent_session_path: Path) -> None:
        result = analyze_session(agent_session_path)
        assert result.agent_metrics.total_invocations > 0
        assert result.tool_metrics.total_tool_calls > 0

    def test_agent_filter(self, agent_session_path: Path) -> None:
        result_all = analyze_session(agent_session_path)
        result_filtered = analyze_session(agent_session_path, agent_filter="pm")
        # Filtered should have <= invocations
        assert result_filtered.agent_metrics.total_invocations <= (
            result_all.agent_metrics.total_invocations
        )

    def test_agent_filter_no_match(self, agent_session_path: Path) -> None:
        result = analyze_session(agent_session_path, agent_filter="nonexistent")
        assert result.agent_metrics.total_invocations == 0

    def test_empty_session(self, empty_session_path: Path) -> None:
        result = analyze_session(empty_session_path)
        assert result.message_count == 0
        assert result.token_metrics.total_tokens == 0
        assert result.tool_metrics.total_tool_calls == 0
        assert result.agent_metrics.total_invocations == 0

    def test_synthetic_excluded_from_model_turns(
        self, write_jsonl: WriteJSONL,
    ) -> None:
        # #507: a <synthetic> ghost response is counted in
        # assistant_message_count but excluded from model_turns and
        # tallied in synthetic_message_count.
        path = write_jsonl(
            "session_synthetic.jsonl",
            [
                user_message("do a thing"),
                assistant_message(
                    [{"type": "text", "text": "working"}],
                    message_id="m1",
                    usage={"input_tokens": 10, "output_tokens": 5},
                ),
                assistant_message(
                    [{"type": "text", "text": "done"}],
                    message_id="m2",
                    usage={"input_tokens": 8, "output_tokens": 4},
                ),
                assistant_message(
                    [{"type": "text", "text": "No response requested."}],
                    message_id="m3",
                    model="<synthetic>",
                ),
            ],
        )
        result = analyze_session(path)
        assert result.assistant_message_count == 3
        assert result.synthetic_message_count == 1
        assert result.model_turns == 2
        # Both metrics exclude synthetic, and the two real turns carry
        # usage, so model_turns == api_call_count (the common case).
        assert result.model_turns == result.token_metrics.api_call_count

    def test_streaming_dupes_session(self, streaming_dupes_session_path: Path) -> None:
        result = analyze_session(streaming_dupes_session_path)
        # Dedup should collapse 5 assistant snapshots into 2
        assert result.assistant_message_count == 2
        # Verify token metrics use deduplicated data
        assert result.token_metrics.api_call_count == 2

    def test_session_first_timestamp_reaches_pricing(
        self, write_jsonl: WriteJSONL, monkeypatch: Any,
    ) -> None:
        # #546 AC#2 end-to-end: analyze_session derives the first-message timestamp and
        # threads it into the cost path. Spy on tokens.get_pricing and assert it receives
        # the session's first-message datetime (not None / not today).
        from datetime import UTC, datetime

        from agentfluent.analytics import tokens

        path = write_jsonl(
            "session_dated.jsonl",
            [
                user_message("hi", timestamp="2026-04-21T10:00:00.000Z"),
                assistant_message(
                    [{"type": "text", "text": "hello"}],
                    message_id="m1",
                    usage={"input_tokens": 10, "output_tokens": 5},
                    timestamp="2026-04-21T10:00:01.000Z",
                ),
            ],
        )
        seen: list[Any] = []
        real = tokens.get_pricing

        def _spy(model: str, timestamp: Any = None):  # type: ignore[no-untyped-def]
            seen.append(timestamp)
            return real(model, timestamp)

        monkeypatch.setattr(tokens, "get_pricing", _spy)
        analyze_session(path)
        assert seen == [datetime(2026, 4, 21, 10, 0, 0, tzinfo=UTC)]

    def test_messages_field_in_memory_but_excluded_from_json(
        self, agent_session_path: Path,
    ) -> None:
        # In-memory: populated for downstream diagnostics (e.g., #189's
        # parent-thread offload-candidate pipeline reads from it).
        result = analyze_session(agent_session_path)
        assert result.messages, "messages must populate for diagnostics consumers"
        # JSON: excluded so `analyze --format json` doesn't dump
        # ToolUseBlock.input payloads (file contents, bash output)
        # the user never asked for.
        assert "messages" not in result.model_dump(mode="json")
        assert "messages" not in result.model_dump()


class TestAnalyzeSessions:
    def test_multiple_sessions(
        self, basic_session_path: Path, agent_session_path: Path,
    ) -> None:
        result = analyze_sessions([basic_session_path, agent_session_path])
        assert result.session_count == 2
        assert len(result.sessions) == 2
        # Aggregated totals should be >= any single session
        assert result.token_metrics.total_tokens >= result.sessions[0].token_metrics.total_tokens

    def test_empty_paths(self) -> None:
        result = analyze_sessions([])
        assert result.session_count == 0
        assert result.token_metrics.total_tokens == 0

    def test_agent_filter_across_sessions(
        self, basic_session_path: Path, agent_session_path: Path,
    ) -> None:
        result = analyze_sessions(
            [basic_session_path, agent_session_path], agent_filter="pm",
        )
        # basic_session has no agents, so only agent_session contributes
        for session in result.sessions:
            for _key, m in session.agent_metrics.by_agent_type.items():
                assert m.agent_type.lower() == "pm"


class TestModelTurns:
    """#465 + #507: model_turns = assistant_message_count - synthetic; the
    synthetic subset is excluded and tallied separately; totals sum."""

    def test_model_turns_no_synthetic_equals_assistant_count(self) -> None:
        assert _session(5).model_turns == 5

    def test_model_turns_excludes_synthetic(self) -> None:
        # #507: 5 assistant messages, 2 of them <synthetic> ghosts -> 3 turns.
        assert _session(5, synthetic_count=2).model_turns == 3

    def test_model_turns_empty_session(self) -> None:
        assert _session(0).model_turns == 0

    def test_model_turns_matches_pipeline_count(
        self, basic_session_path: Path,
    ) -> None:
        result = analyze_session(basic_session_path)
        assert result.model_turns == (
            result.assistant_message_count - result.synthetic_message_count
        )

    def test_total_model_turns_sums_sessions(self) -> None:
        result = AnalysisResult(sessions=[_session(5), _session(3), _session(0)])
        assert result.total_model_turns == 8

    def test_total_model_turns_nets_out_synthetic(self) -> None:
        # 5+3 assistant messages, 1+2 synthetic -> (5-1)+(3-2) = 5 turns.
        result = AnalysisResult(
            sessions=[_session(5, synthetic_count=1), _session(3, synthetic_count=2)],
        )
        assert result.total_model_turns == 5
        assert result.total_synthetic_messages == 3

    def test_total_model_turns_empty_result(self) -> None:
        assert AnalysisResult().total_model_turns == 0
        assert AnalysisResult().total_synthetic_messages == 0

    def test_total_model_turns_via_analyze_sessions(
        self, basic_session_path: Path, agent_session_path: Path,
    ) -> None:
        result = analyze_sessions([basic_session_path, agent_session_path])
        assert result.total_model_turns == sum(
            s.model_turns for s in result.sessions
        )

    def test_model_turns_in_json_dump(self) -> None:
        dumped = AnalysisResult(
            sessions=[_session(5, synthetic_count=2)],
        ).model_dump(mode="json")
        assert dumped["total_model_turns"] == 3
        assert dumped["total_synthetic_messages"] == 2
        assert dumped["sessions"][0]["model_turns"] == 3
        assert dumped["sessions"][0]["synthetic_message_count"] == 2
        # Backing field stays all-inclusive for backward compat.
        assert dumped["sessions"][0]["assistant_message_count"] == 5


class TestMergeAgentMetricsTurns:
    """#467: _merge_agent_metrics carries turn totals across sessions and
    recomputes the turn ratios from the merged totals."""

    @staticmethod
    def _metrics(total_turns: int, with_turns: int, tool_uses: int) -> AgentMetrics:
        t = AgentTypeMetrics(
            agent_type="pm",
            is_builtin=False,
            invocation_count=with_turns,
            total_tool_uses=tool_uses,
            total_model_turns=total_turns,
            invocations_with_turns=with_turns,
        )
        return AgentMetrics(by_agent_type={"pm": t}, total_invocations=with_turns)

    def test_turn_fields_summed_and_ratios_recomputed(self) -> None:
        # Session A: 6 turns over 2 invocations, 12 tool calls.
        # Session B: 4 turns over 1 invocation, 8 tool calls.
        merged = _merge_agent_metrics(
            [self._metrics(6, 2, 12), self._metrics(4, 1, 8)],
        )
        pm = merged.by_agent_type["pm"]
        assert pm.total_model_turns == 10
        assert pm.invocations_with_turns == 3
        assert merged.total_model_turns == 10
        # Ratios recomputed from merged totals, not averaged.
        assert pm.avg_turns_per_invocation == 10 / 3
        assert pm.avg_tool_calls_per_turn == 20 / 10

    def test_first_seen_branch_preserves_turn_fields(self) -> None:
        # A single-session merge exercises only the first-seen
        # constructor branch — turn fields must survive it.
        pm = _merge_agent_metrics([self._metrics(6, 2, 12)]).by_agent_type["pm"]
        assert pm.total_model_turns == 6
        assert pm.invocations_with_turns == 2


class TestMergeAgentMetricsActiveDuration:
    """#480: _merge_agent_metrics carries active-duration aggregates
    across sessions through both the first-seen and accumulate branches."""

    @staticmethod
    def _metrics(active_ms: int, wall_linked_ms: int, linked: int) -> AgentMetrics:
        t = AgentTypeMetrics(
            agent_type="pm",
            is_builtin=False,
            invocation_count=linked,
            total_active_duration_ms=active_ms,
            total_wallclock_ms_trace_linked=wall_linked_ms,
            active_duration_invocation_count=linked,
        )
        return AgentMetrics(by_agent_type={"pm": t}, total_invocations=linked)

    def test_active_fields_summed_across_sessions(self) -> None:
        merged = _merge_agent_metrics(
            [self._metrics(4000, 60000, 1), self._metrics(8000, 120000, 2)],
        ).by_agent_type["pm"]
        assert merged.total_active_duration_ms == 12000
        assert merged.total_wallclock_ms_trace_linked == 180000
        assert merged.active_duration_invocation_count == 3
        # Ratio computed from merged totals.
        assert merged.wallclock_active_ratio == 15.0

    def test_first_seen_branch_preserves_active_fields(self) -> None:
        pm = _merge_agent_metrics(
            [self._metrics(4000, 60000, 1)],
        ).by_agent_type["pm"]
        assert pm.total_active_duration_ms == 4000
        assert pm.total_wallclock_ms_trace_linked == 60000
        assert pm.active_duration_invocation_count == 1


_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
_NESTED_MAIN = _FIXTURES / "nested_session" / "nested-session-1.jsonl"
_SDK_MAIN = _FIXTURES / "sdk_session" / "sdk-main-1.jsonl"


def _subagent_origin(tm: TokenMetrics) -> tuple[float, int]:
    rows = [r for r in tm.by_model if r.origin == "subagent"]
    return sum(r.cost for r in rows), sum(r.total_tokens for r in rows)


def _per_agent(am: AgentMetrics) -> tuple[float, int]:
    return (
        sum(m.estimated_total_cost_usd for m in am.by_agent_type.values()),
        sum(m.spend_tokens for m in am.by_agent_type.values()),
    )


class TestAgentSpendReconciliation:
    """#646 AC8: Σ per-agent cost/spend == the subagent-origin rows of the session fold.

    This is the internal stand-in for the blocked Admin-API reconciliation (D059), and
    the tripwire for anything that breaks pricing linearity (#682 tiers, #537-#539
    per-request levers -- see ``analytics.tokens._cost_of``).

    **Scope: the depth-1 fold set.** Both sides cover only the depth-1 traces that
    ``analyze_session``'s non-folding gate admits. **#648 AC2** (folding depth->=2 spend
    into ``token_metrics``) is the change that must update this test deliberately --
    either by attributing that spend to an ancestor agent type or by narrowing the
    right-hand side to depth-1 rows.
    """

    def test_mixed_models_reconcile_by_construction(self) -> None:
        from agentfluent.agents.models import AgentInvocation
        from agentfluent.analytics.agent_metrics import compute_agent_metrics
        from agentfluent.analytics.tokens import compute_subagent_token_metrics
        from agentfluent.core.session import Usage
        from agentfluent.traces.models import SubagentTrace

        def inv(agent_type: str, model: str | None, n: int) -> AgentInvocation:
            i = AgentInvocation(
                agent_type=agent_type, description="d", prompt="p", tool_use_id=f"t{n}",
            )
            i.trace = SubagentTrace(
                agent_id=f"a{n}",
                delegation_prompt="p",
                model=model,
                model_turns=3,
                usage=Usage(
                    input_tokens=1000 * n,
                    output_tokens=300 * n,
                    cache_creation_input_tokens=500 * n,
                    cache_read_input_tokens=20_000 * n,
                ),
            )
            return i

        invocations = [
            inv("pm", "claude-opus-4-6", 1),
            inv("pm", "claude-haiku-4-5-20251001", 2),
            inv("explore", "claude-haiku-4-5-20251001", 3),
            inv("explore", "claude-sonnet-4-6", 4),
            inv("ghost", "claude-nonexistent-9", 5),  # unpriced: tokens, no cost
            inv("ghost", None, 6),  # excluded from both sides
        ]
        traces = [i.trace for i in invocations if i.trace is not None]
        rows = compute_subagent_token_metrics(traces)
        metrics = compute_agent_metrics(invocations)

        fold_cost = sum(r.cost for r in rows)
        fold_tokens = sum(r.total_tokens for r in rows)
        agent_cost, agent_tokens = _per_agent(metrics)
        assert fold_cost > 0
        assert agent_cost == pytest.approx(fold_cost)
        assert agent_tokens == fold_tokens

    @pytest.mark.parametrize("path", [_NESTED_MAIN, _SDK_MAIN], ids=["nested", "sdk"])
    def test_fixture_session_reconciles(self, path: Path) -> None:
        result = analyze_session(path)
        fold_cost, fold_tokens = _subagent_origin(result.token_metrics)
        agent_cost, agent_tokens = _per_agent(result.agent_metrics)
        assert fold_tokens > 0
        assert agent_cost == pytest.approx(fold_cost)
        assert agent_tokens == fold_tokens

    @pytest.mark.parametrize("agent_filter", ["worker", "no-such-agent"])
    def test_agent_filter_reconciles(self, agent_filter: str) -> None:
        # ``--agent`` filters invocations before linking and folding, so both sides
        # see the same filtered set.
        result = analyze_session(_NESTED_MAIN, agent_filter=agent_filter)
        fold_cost, fold_tokens = _subagent_origin(result.token_metrics)
        agent_cost, agent_tokens = _per_agent(result.agent_metrics)
        assert agent_cost == pytest.approx(fold_cost)
        assert agent_tokens == fold_tokens

    def test_agent_token_percentage_is_spend_share(self) -> None:
        result = analyze_session(_SDK_MAIN)
        am = result.agent_metrics
        expected = round(am.total_agent_spend_tokens / result.token_metrics.total_tokens * 100, 1)
        assert am.total_agent_spend_tokens > 0
        assert am.agent_token_percentage == expected
        assert 0 < am.agent_token_percentage <= 100

    def test_multi_session_merge_reconciles(self) -> None:
        result = analyze_sessions([_NESTED_MAIN, _SDK_MAIN])
        fold_cost, fold_tokens = _subagent_origin(result.token_metrics)
        agent_cost, agent_tokens = _per_agent(result.agent_metrics)
        assert agent_cost == pytest.approx(fold_cost)
        assert agent_tokens == fold_tokens


class TestMergeAgentMetricsSpend:
    """#646 AC7: spend and priced-coverage fields sum across sessions; the
    percentage and cost averages are recomputed from merged totals."""

    @staticmethod
    def _metrics(spend: int, spend_n: int, priced_n: int, turns: int, cost: float) -> AgentMetrics:
        t = AgentTypeMetrics(
            agent_type="pm",
            is_builtin=False,
            invocation_count=spend_n + 1,
            spend_tokens=spend,
            spend_invocation_count=spend_n,
            priced_invocation_count=priced_n,
            priced_model_turns=turns,
            total_model_turns=turns,
            estimated_total_cost_usd=cost,
        )
        return AgentMetrics(by_agent_type={"pm": t}, total_invocations=spend_n + 1)

    def test_spend_fields_summed_and_ratios_recomputed(self) -> None:
        merged = _merge_agent_metrics(
            [self._metrics(1000, 2, 2, 10, 0.4), self._metrics(3000, 3, 1, 5, 0.2)],
            session_total_tokens=8000,
        )
        pm = merged.by_agent_type["pm"]
        assert pm.spend_tokens == 4000
        assert pm.spend_invocation_count == 5
        assert pm.priced_invocation_count == 3
        assert pm.priced_model_turns == 15
        assert merged.total_agent_spend_tokens == 4000
        assert merged.agent_token_percentage == 50.0
        assert pm.estimated_avg_cost_per_invocation_usd == pytest.approx(0.6 / 3)
        assert pm.estimated_avg_cost_per_turn_usd == pytest.approx(0.6 / 15)
        assert pm.avg_tokens_per_turn == pytest.approx(4000 / 15)

    def test_first_seen_branch_preserves_spend_fields(self) -> None:
        pm = _merge_agent_metrics([self._metrics(1000, 2, 1, 10, 0.4)]).by_agent_type["pm"]
        assert (pm.spend_tokens, pm.spend_invocation_count) == (1000, 2)
        assert (pm.priced_invocation_count, pm.priced_model_turns) == (1, 10)

    def test_percentage_ignores_rollup_proxy(self) -> None:
        m = self._metrics(0, 0, 0, 0, 0.0)
        m.by_agent_type["pm"].total_tokens = 5000
        merged = _merge_agent_metrics([m], session_total_tokens=10000)
        assert merged.total_agent_tokens == 5000
        assert merged.agent_token_percentage == 0.0
