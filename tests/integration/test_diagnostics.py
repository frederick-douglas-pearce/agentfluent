"""Integration tests for diagnostics pipeline against real session data.

Validates that signal extraction and correlation work on real-world
agent invocations. Skipped in CI.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentfluent.agents.extractor import extract_agent_invocations
from agentfluent.core.discovery import DEFAULT_PROJECTS_DIR, discover_projects
from agentfluent.core.parser import parse_session
from agentfluent.diagnostics import run_diagnostics
from agentfluent.diagnostics.models import DiagnosticsResult

has_real_data = DEFAULT_PROJECTS_DIR.exists() and any(DEFAULT_PROJECTS_DIR.iterdir())

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not has_real_data, reason="No real session data at ~/.claude/projects/"),
]


@pytest.fixture()
def real_session_with_agents() -> Path:
    """Find a real session that has agent invocations."""
    projects = discover_projects()
    for p in projects:
        for s in p.sessions:
            if s.size_bytes > 5000:
                messages = parse_session(s.path)
                invocations = extract_agent_invocations(messages)
                if invocations:
                    return s.path
    pytest.skip("No sessions with agent invocations found")


class TestDiagnosticsPipelineReal:
    def test_produces_valid_result(self, real_session_with_agents: Path) -> None:
        messages = parse_session(real_session_with_agents)
        invocations = extract_agent_invocations(messages)
        result = run_diagnostics(invocations)
        assert isinstance(result, DiagnosticsResult)
        assert isinstance(result.signals, list)
        assert isinstance(result.recommendations, list)

    def test_signals_have_valid_types(self, real_session_with_agents: Path) -> None:
        messages = parse_session(real_session_with_agents)
        invocations = extract_agent_invocations(messages)
        result = run_diagnostics(invocations)
        for sig in result.signals:
            assert sig.signal_type
            assert sig.severity
            assert sig.agent_type
            assert sig.message

    def test_recommendations_have_structure(self, real_session_with_agents: Path) -> None:
        messages = parse_session(real_session_with_agents)
        invocations = extract_agent_invocations(messages)
        result = run_diagnostics(invocations)
        for rec in result.recommendations:
            assert rec.target
            assert rec.severity
            assert rec.message
            assert rec.observation
            assert rec.action
