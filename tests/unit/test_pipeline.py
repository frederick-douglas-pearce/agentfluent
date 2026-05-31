"""Tests for the analytics pipeline orchestration."""

from pathlib import Path

from agentfluent.analytics.agent_metrics import AgentMetrics
from agentfluent.analytics.pipeline import (
    AnalysisResult,
    SessionAnalysis,
    analyze_session,
    analyze_sessions,
)
from agentfluent.analytics.tokens import TokenMetrics
from agentfluent.analytics.tools import ToolMetrics


def _session(assistant_count: int) -> SessionAnalysis:
    """Build a minimal SessionAnalysis with a given assistant-message count."""
    return SessionAnalysis(
        session_path=Path("s.jsonl"),
        token_metrics=TokenMetrics(),
        tool_metrics=ToolMetrics(),
        agent_metrics=AgentMetrics(),
        assistant_message_count=assistant_count,
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

    def test_streaming_dupes_session(self, streaming_dupes_session_path: Path) -> None:
        result = analyze_session(streaming_dupes_session_path)
        # Dedup should collapse 5 assistant snapshots into 2
        assert result.assistant_message_count == 2
        # Verify token metrics use deduplicated data
        assert result.token_metrics.api_call_count == 2

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
    """#465: model_turns aliases assistant_message_count; total_model_turns sums."""

    def test_model_turns_aliases_assistant_count(self) -> None:
        assert _session(5).model_turns == 5

    def test_model_turns_empty_session(self) -> None:
        assert _session(0).model_turns == 0

    def test_model_turns_matches_pipeline_count(
        self, basic_session_path: Path,
    ) -> None:
        result = analyze_session(basic_session_path)
        assert result.model_turns == result.assistant_message_count

    def test_total_model_turns_sums_sessions(self) -> None:
        result = AnalysisResult(sessions=[_session(5), _session(3), _session(0)])
        assert result.total_model_turns == 8

    def test_total_model_turns_empty_result(self) -> None:
        assert AnalysisResult().total_model_turns == 0

    def test_total_model_turns_via_analyze_sessions(
        self, basic_session_path: Path, agent_session_path: Path,
    ) -> None:
        result = analyze_sessions([basic_session_path, agent_session_path])
        assert result.total_model_turns == sum(
            s.model_turns for s in result.sessions
        )

    def test_model_turns_in_json_dump(self) -> None:
        dumped = AnalysisResult(sessions=[_session(5)]).model_dump(mode="json")
        assert dumped["total_model_turns"] == 5
        assert dumped["sessions"][0]["model_turns"] == 5
        # Backing field stays for backward compat.
        assert dumped["sessions"][0]["assistant_message_count"] == 5
