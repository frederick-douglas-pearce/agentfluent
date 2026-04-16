"""Data models for agent invocations extracted from session data."""

from __future__ import annotations

from dataclasses import dataclass

# Built-in agent types (case-insensitive matching).
# Update this set as Anthropic adds new built-in agents.
BUILTIN_AGENT_TYPES: frozenset[str] = frozenset(
    {
        "explore",
        "plan",
        "general-purpose",
        "code-reviewer",
        "statusline-setup",
        "claude-code-guide",
    }
)


def is_builtin_agent(agent_type: str) -> bool:
    """Check if an agent type is a built-in Claude Code agent."""
    return agent_type.lower() in BUILTIN_AGENT_TYPES


@dataclass
class AgentInvocation:
    """A single agent invocation extracted from a session.

    Combines data from the Agent tool_use block (in the assistant message)
    with the corresponding tool_result (including metadata).
    """

    agent_type: str
    """Agent type (e.g., 'pm', 'Explore', 'Plan')."""

    is_builtin: bool
    """Whether this is a built-in Claude Code agent."""

    description: str
    """The description passed to the Agent tool."""

    prompt: str
    """The delegation prompt sent to the agent."""

    tool_use_id: str
    """The tool_use ID linking this invocation to its tool_result."""

    # From tool_result metadata (may be None if no metadata or agent was interrupted)
    total_tokens: int | None = None
    tool_uses: int | None = None
    duration_ms: int | None = None
    agent_id: str | None = None

    # From tool_result content
    output_text: str = ""
    """The agent's final summary/output text."""

    @property
    def tokens_per_tool_use(self) -> float | None:
        """Average tokens per tool call. None if data unavailable."""
        if self.total_tokens is not None and self.tool_uses and self.tool_uses > 0:
            return self.total_tokens / self.tool_uses
        return None

    @property
    def duration_per_tool_use(self) -> float | None:
        """Average duration (ms) per tool call. None if data unavailable."""
        if self.duration_ms is not None and self.tool_uses and self.tool_uses > 0:
            return self.duration_ms / self.tool_uses
        return None
