"""Agent configuration assessment package."""

from __future__ import annotations

from agentfluent.config.models import ConfigScore
from agentfluent.config.scanner import scan_agents
from agentfluent.config.scoring import score_agent


def assess_agents(
    scope: str = "all",
    *,
    agent_filter: str | None = None,
) -> list[ConfigScore]:
    """Scan and score agent definitions.

    Reusable orchestration function for CLI, diagnostics, and tests.

    Args:
        scope: Which locations to scan -- "user", "project", or "all".
        agent_filter: If set, only score this agent name (case-insensitive).

    Returns:
        List of ConfigScore results.
    """
    agents = scan_agents(scope)

    if agent_filter:
        agents = [a for a in agents if a.name.lower() == agent_filter.lower()]

    return [score_agent(a) for a in agents]
