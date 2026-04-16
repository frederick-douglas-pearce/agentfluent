"""Integration tests for analytics pipeline against real session data.

Validates that the full analytics pipeline handles real-world JSONL
variations and produces structurally valid results. Skipped in CI.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentfluent.analytics.pipeline import AnalysisResult, analyze_session, analyze_sessions
from agentfluent.analytics.tokens import TokenMetrics
from agentfluent.analytics.tools import ToolMetrics
from agentfluent.core.discovery import DEFAULT_PROJECTS_DIR, discover_projects

has_real_data = DEFAULT_PROJECTS_DIR.exists() and any(DEFAULT_PROJECTS_DIR.iterdir())

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not has_real_data, reason="No real session data at ~/.claude/projects/"),
]


@pytest.fixture()
def real_session_path() -> Path:
    """Find a real session file with some content."""
    projects = discover_projects()
    for p in projects:
        for s in p.sessions:
            if s.size_bytes > 1000:
                return s.path
    pytest.skip("No parseable session files found")


@pytest.fixture()
def real_project_sessions() -> list[Path]:
    """Get session paths from the smallest project with sessions."""
    projects = discover_projects()
    with_sessions = [p for p in projects if p.session_count > 0]
    if not with_sessions:
        pytest.skip("No projects with sessions found")
    smallest = min(with_sessions, key=lambda p: p.total_size_bytes)
    return [s.path for s in smallest.sessions[:5]]  # cap at 5 for speed


class TestAnalyzeSessionReal:
    def test_produces_valid_token_metrics(self, real_session_path: Path) -> None:
        result = analyze_session(real_session_path)
        tm = result.token_metrics
        assert isinstance(tm, TokenMetrics)
        assert tm.total_tokens >= 0
        assert tm.total_cost >= 0.0
        assert 0.0 <= tm.cache_efficiency <= 100.0

    def test_produces_valid_tool_metrics(self, real_session_path: Path) -> None:
        result = analyze_session(real_session_path)
        tlm = result.tool_metrics
        assert isinstance(tlm, ToolMetrics)
        assert tlm.total_tool_calls >= 0
        assert tlm.unique_tool_count >= 0
        assert tlm.unique_tool_count <= tlm.total_tool_calls

    def test_produces_valid_agent_metrics(self, real_session_path: Path) -> None:
        result = analyze_session(real_session_path)
        am = result.agent_metrics
        assert am.total_invocations >= 0
        assert am.builtin_invocations + am.custom_invocations == am.total_invocations

    def test_message_counts_consistent(self, real_session_path: Path) -> None:
        result = analyze_session(real_session_path)
        assert result.message_count >= result.user_message_count + result.assistant_message_count

    def test_cost_non_negative(self, real_session_path: Path) -> None:
        result = analyze_session(real_session_path)
        assert result.token_metrics.total_cost >= 0.0
        for breakdown in result.token_metrics.by_model.values():
            assert breakdown.cost >= 0.0


class TestAnalyzeMultipleSessionsReal:
    def test_aggregate_results(self, real_project_sessions: list[Path]) -> None:
        result = analyze_sessions(real_project_sessions)
        assert isinstance(result, AnalysisResult)
        assert result.session_count == len(real_project_sessions)
        assert result.session_count == len(result.sessions)

    def test_aggregate_tokens_gte_individual(
        self, real_project_sessions: list[Path],
    ) -> None:
        result = analyze_sessions(real_project_sessions)
        individual_total = sum(s.token_metrics.total_tokens for s in result.sessions)
        assert result.token_metrics.total_tokens == individual_total
