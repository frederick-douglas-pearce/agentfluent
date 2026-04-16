"""Integration tests against real session data from ~/.claude/projects/.

These tests validate that the parser and discovery modules handle real-world
JSONL variations beyond the anonymized fixtures. They are skipped in CI
(no real session data available) and do not depend on specific project names
or session contents.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentfluent.core.discovery import DEFAULT_PROJECTS_DIR, discover_projects, discover_sessions
from agentfluent.core.parser import parse_session

has_real_data = DEFAULT_PROJECTS_DIR.exists() and any(DEFAULT_PROJECTS_DIR.iterdir())

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not has_real_data, reason="No real session data at ~/.claude/projects/"),
]


class TestDiscoverProjectsReal:
    def test_returns_at_least_one_project(self) -> None:
        projects = discover_projects()
        assert len(projects) >= 1

    def test_projects_have_valid_fields(self) -> None:
        projects = discover_projects()
        for p in projects:
            assert p.slug, "Project slug should not be empty"
            assert p.display_name, "Display name should not be empty"
            assert p.path.is_dir(), f"Project path should exist: {p.path}"
            assert p.session_count >= 0

    def test_at_least_one_project_has_sessions(self) -> None:
        projects = discover_projects()
        projects_with_sessions = [p for p in projects if p.session_count > 0]
        assert len(projects_with_sessions) >= 1, "Expected at least one project with sessions"


class TestDiscoverSessionsReal:
    @pytest.fixture()
    def project_with_sessions(self) -> Path:
        """Find a real project that has at least one session."""
        projects = discover_projects()
        for p in projects:
            if p.session_count > 0:
                return p.path
        pytest.skip("No projects with sessions found")

    def test_returns_sessions(self, project_with_sessions: Path) -> None:
        sessions = discover_sessions(project_with_sessions)
        assert len(sessions) >= 1

    def test_sessions_have_valid_metadata(self, project_with_sessions: Path) -> None:
        sessions = discover_sessions(project_with_sessions)
        for s in sessions:
            assert s.filename.endswith(".jsonl")
            assert s.size_bytes > 0
            assert s.modified is not None
            assert s.path.exists()

    def test_sessions_sorted_newest_first(self, project_with_sessions: Path) -> None:
        sessions = discover_sessions(project_with_sessions)
        if len(sessions) >= 2:
            for i in range(len(sessions) - 1):
                assert sessions[i].modified >= sessions[i + 1].modified


class TestParseSessionReal:
    @pytest.fixture()
    def real_session_path(self) -> Path:
        """Find a real session file to parse."""
        projects = discover_projects()
        for p in projects:
            for s in p.sessions:
                if s.size_bytes > 100:  # skip trivially small files
                    return s.path
        pytest.skip("No parseable session files found")

    def test_parses_without_error(self, real_session_path: Path) -> None:
        messages = parse_session(real_session_path)
        assert len(messages) >= 1

    def test_all_messages_have_valid_type(self, real_session_path: Path) -> None:
        messages = parse_session(real_session_path)
        valid_types = {"user", "assistant", "tool_result"}
        for m in messages:
            assert m.type in valid_types, f"Unexpected type: {m.type}"

    def test_user_messages_have_content(self, real_session_path: Path) -> None:
        messages = parse_session(real_session_path)
        user_msgs = [m for m in messages if m.type == "user"]
        if user_msgs:
            # At least the first user message should have content
            assert user_msgs[0].text, "First user message should have text content"

    def test_assistant_messages_have_model(self, real_session_path: Path) -> None:
        messages = parse_session(real_session_path)
        assistant_msgs = [m for m in messages if m.type == "assistant"]
        for m in assistant_msgs:
            assert m.model, "Assistant message should have model"

    def test_assistant_messages_have_usage(self, real_session_path: Path) -> None:
        messages = parse_session(real_session_path)
        assistant_msgs = [m for m in messages if m.type == "assistant"]
        # Most assistant messages have usage, but synthetic messages may have zero tokens
        msgs_with_usage = [m for m in assistant_msgs if m.usage and m.usage.total_tokens > 0]
        assert len(msgs_with_usage) >= 1, "Expected at least one assistant message with token usage"

    def test_timestamps_present_on_user_and_assistant(self, real_session_path: Path) -> None:
        messages = parse_session(real_session_path)
        for m in messages:
            if m.type in ("user", "assistant"):
                assert m.timestamp is not None, f"{m.type} message should have timestamp"


class TestParseMultipleSessionsReal:
    def test_parse_all_sessions_in_a_project(self) -> None:
        """Parse every session in the smallest project to stress-test the parser."""
        projects = discover_projects()
        # Pick the project with fewest sessions to keep test fast
        projects_with_sessions = [p for p in projects if p.session_count > 0]
        if not projects_with_sessions:
            pytest.skip("No projects with sessions found")

        smallest = min(projects_with_sessions, key=lambda p: p.total_size_bytes)
        for s in smallest.sessions:
            messages = parse_session(s.path)
            # Should parse without exceptions; may have 0 messages if all skipped
            assert isinstance(messages, list)
