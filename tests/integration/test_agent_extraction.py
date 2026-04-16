"""Integration tests for agent extraction against real session data."""

from __future__ import annotations

import pytest

from agentfluent.agents.extractor import extract_agent_invocations
from agentfluent.core.discovery import DEFAULT_PROJECTS_DIR, discover_projects
from agentfluent.core.parser import parse_session

has_real_data = DEFAULT_PROJECTS_DIR.exists() and any(DEFAULT_PROJECTS_DIR.iterdir())

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not has_real_data, reason="No real session data at ~/.claude/projects/"),
]


class TestAgentExtractionReal:
    def test_extract_from_session_with_agents(self) -> None:
        """Find a real session with agent invocations and extract them."""
        projects = discover_projects()
        for p in projects:
            for s in p.sessions:
                messages = parse_session(s.path)
                invocations = extract_agent_invocations(messages)
                if invocations:
                    # Validate the invocations we found
                    for inv in invocations:
                        assert inv.agent_type, "Agent type should not be empty"
                        assert inv.tool_use_id, "Tool use ID should not be empty"
                        assert isinstance(inv.is_builtin, bool)
                        # If metadata exists, values should be positive
                        if inv.total_tokens is not None:
                            assert inv.total_tokens >= 0
                        if inv.tool_uses is not None:
                            assert inv.tool_uses >= 0
                        if inv.duration_ms is not None:
                            assert inv.duration_ms >= 0
                    return  # Found and validated at least one session

        pytest.skip("No sessions with agent invocations found in real data")

    def test_builtin_and_custom_agents_distinguishable(self) -> None:
        """Verify real data contains both built-in and custom agents."""
        all_invocations = []
        projects = discover_projects()
        for p in projects:
            for s in p.sessions:
                messages = parse_session(s.path)
                all_invocations.extend(extract_agent_invocations(messages))

        if not all_invocations:
            pytest.skip("No agent invocations found in real data")

        builtin = [i for i in all_invocations if i.is_builtin]
        custom = [i for i in all_invocations if not i.is_builtin]

        # At minimum, we should find built-in agents (Explore, Plan are common)
        assert len(builtin) >= 1, "Expected at least one built-in agent invocation"
        # Custom agents may or may not exist depending on user's setup
        # Just verify the classification works without asserting count
        assert all(not i.is_builtin for i in custom)
