"""Integration tests for config assessment against real agent definitions.

Validates scanning and scoring of real agent files from ~/.claude/agents/.
Skipped in CI.
"""

from __future__ import annotations

import pytest

from agentfluent.config.scanner import DEFAULT_USER_AGENTS_DIR, scan_agents
from agentfluent.config.scoring import score_agent

has_real_agents = (
    DEFAULT_USER_AGENTS_DIR.exists()
    and any(DEFAULT_USER_AGENTS_DIR.iterdir())
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not has_real_agents, reason="No real agents at ~/.claude/agents/"),
]


class TestScanRealAgents:
    def test_finds_agents(self) -> None:
        agents = scan_agents("user")
        assert len(agents) >= 1

    def test_agents_have_names(self) -> None:
        agents = scan_agents("user")
        for a in agents:
            assert a.name, "Agent should have a name"

    def test_agents_parseable(self) -> None:
        agents = scan_agents("user")
        for a in agents:
            assert a.file_path.exists()


class TestScoreRealAgents:
    def test_all_agents_scoreable(self) -> None:
        agents = scan_agents("user")
        for a in agents:
            score = score_agent(a)
            assert 0 <= score.overall_score <= 100
            assert len(score.dimension_scores) == 4

    def test_scores_have_valid_dimensions(self) -> None:
        agents = scan_agents("user")
        expected_dims = {"description", "tool_restrictions", "model_selection", "prompt_body"}
        for a in agents:
            score = score_agent(a)
            assert set(score.dimension_scores.keys()) == expected_dims
