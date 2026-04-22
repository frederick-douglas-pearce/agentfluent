"""Data models for agent invocations extracted from session data."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from agentfluent.traces.models import SubagentTrace

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


GENERAL_PURPOSE_AGENT_TYPE = "general-purpose"

# Tools that cause state changes to the host environment. Used by
# diagnostics modules (delegation, model_routing) to classify task
# complexity — presence of any of these signals a write workload that
# typically needs a higher-tier model or different routing.
WRITE_TOOLS: frozenset[str] = frozenset(
    {"Write", "Edit", "Bash", "NotebookEdit"},
)


def is_builtin_agent(agent_type: str) -> bool:
    """Check if an agent type is a built-in Claude Code agent."""
    return agent_type.lower() in BUILTIN_AGENT_TYPES


def is_general_purpose(agent_type: str) -> bool:
    """Check if an agent type is the built-in ``general-purpose`` agent."""
    return agent_type.lower() == GENERAL_PURPOSE_AGENT_TYPE


class AgentInvocation(BaseModel):
    """A single agent invocation extracted from a session.

    Combines data from the Agent tool_use block (in the assistant message)
    with the corresponding tool_result (including metadata).
    """

    model_config = ConfigDict(extra="ignore")

    agent_type: str
    """Agent type (e.g., 'pm', 'explore', 'plan'). Case varies in real
    data; ``is_builtin_agent`` normalizes for comparison."""

    description: str
    prompt: str

    tool_use_id: str
    """Links this invocation back to the assistant message's ``tool_use``
    block so downstream code can join the delegation call with its
    result. Required; always populated by the extractor."""

    # From tool_result metadata (may be None if no metadata or agent was interrupted)
    total_tokens: int | None = None
    tool_uses: int | None = None
    duration_ms: int | None = None
    agent_id: str | None = None

    # From tool_result content
    output_text: str = ""

    # Attached by trace linking when a matching subagent file exists; `None`
    # otherwise (e.g., older sessions predating trace capture). Serves as the
    # evidence layer for trace-level diagnostics.
    trace: SubagentTrace | None = None

    @property
    def is_builtin(self) -> bool:
        """Whether this invocation's agent type is a built-in Claude Code
        agent. Derived on access from ``agent_type`` + ``BUILTIN_AGENT_TYPES``
        so the answer stays in sync if the set is updated."""
        return is_builtin_agent(self.agent_type)

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
