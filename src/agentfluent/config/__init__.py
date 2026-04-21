"""Agent configuration assessment package."""

from __future__ import annotations

from pathlib import Path

from agentfluent.config.models import ConfigScore
from agentfluent.config.scanner import scan_agents
from agentfluent.config.scoring import score_agent


def assess_agents(
    scope: str = "all",
    *,
    agent_filter: str | None = None,
    user_path: Path | None = None,
) -> list[ConfigScore]:
    """Scan and score agent definitions.

    Reusable orchestration function for CLI, diagnostics, and tests.

    Args:
        scope: Which locations to scan -- "user", "project", or "all".
        agent_filter: If set, only score this agent name (case-insensitive).
        user_path: Override for the user agents directory. Forwarded to
            ``scan_agents``. Defaults to ``~/.claude/agents/``.

    Returns:
        List of ConfigScore results.
    """
    agents = scan_agents(scope, user_path=user_path)

    if agent_filter:
        agents = [a for a in agents if a.name.lower() == agent_filter.lower()]

    return [score_agent(a) for a in agents]
