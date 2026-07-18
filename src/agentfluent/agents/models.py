"""Data models for agent invocations extracted from session data."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, computed_field

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
    {"Write", "Edit", "MultiEdit", "Bash", "NotebookEdit"},
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
    """Raw ``toolUseResult.totalTokens`` for this invocation. **Two facts a
    consumer must know before aggregating (#595, D056):**

    1. **It excludes child agents.** A delegating agent's ``totalTokens`` does
       not contain the tokens of agents it spawned, so summing an invocation
       alongside its descendants does not double-count.
    2. **It is not cumulative spend.** It equals the agent's *final assistant
       turn* usage (``input + output + cache_creation + cache_read``), not the
       sum over its turns. Measured across 683 rollups: 575 (84%) match the
       final turn exactly, **0 match the sum of turns**, and the remainder sit
       within ~1-3% of the final turn while running 10-40x below the sum.
       Because ``cache_read`` is re-counted every turn, this is a *final-turn
       context-size proxy* -- neither tokens billed nor tokens processed.

    Fact 2 means summing this field across invocations does not yield token
    spend, which is a pre-existing defect in several aggregates rather than
    anything #595 introduced; see D056 and the issue it points to. The name is
    retained under D029 (it has shipped) -- prefer a subagent trace's per-turn
    ``usage`` when you need actual spend."""

    tool_uses: int | None = None
    duration_ms: int | None = None
    agent_id: str | None = None
    resolved_model: str | None = None
    """The concrete model this subagent resolved to, read from the parent
    tool-result's ``toolUseResult.resolvedModel`` (#593). Lets model-routing
    verify a subagent's model without a cross-file join into the child trace
    (#112 AC#4) — it's the authoritative source when neither an
    ``AgentConfig.model`` nor a linked ``trace.model`` is available (e.g. an
    SDK subagent defined in code, not in ``.claude/agents/*.md``). ``None``
    when the result carried no ``resolvedModel``."""
    tool_stats: dict[str, int] | None = None
    """Per-tool invocation counts from ``toolUseResult.toolStats`` (keyed
    by tool name). ``None`` when the result carried no ``toolStats``.
    The keys are observed tool diversity for this invocation; the
    ``tool_inventory_oversized`` audit (#372) unions them across an
    agent type's invocations to compute a utilization ratio."""

    # From tool_result content
    output_text: str = ""

    # Attached by trace linking when a matching subagent file exists; `None`
    # otherwise (e.g., older sessions predating trace capture). Serves as the
    # evidence layer for trace-level diagnostics.
    trace: SubagentTrace | None = None

    @property
    def observed_tool_names(self) -> set[str]:
        """Unique tool names this invocation called, from ``tool_stats``
        keys. Empty set when ``tool_stats`` is ``None`` or empty — callers
        that need to distinguish "unknown" from "none observed" should
        check ``tool_stats is None`` directly."""
        return set(self.tool_stats or {})

    @property
    def is_builtin(self) -> bool:
        """Whether this invocation's agent type is a built-in Claude Code
        agent. Derived on access from ``agent_type`` + ``BUILTIN_AGENT_TYPES``
        so the answer stays in sync if the set is updated."""
        return is_builtin_agent(self.agent_type)

    @property
    def invocation_id(self) -> str:
        """Stable identifier for this invocation. Prefers ``agent_id``
        (UUID linking to the subagent trace file); falls back to
        ``tool_use_id`` (always populated, links to the parent
        ``tool_use`` block in the session JSONL) when ``agent_id`` is
        absent (older sessions, interrupted runs)."""
        return self.agent_id or self.tool_use_id

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

    @property
    def idle_gap_ms(self) -> int | None:
        """Idle time deducted to compute ``active_duration_ms``. ``None``
        when no trace is linked or the trace couldn't compute it."""
        if self.trace is None:
            return None
        return self.trace.idle_gap_ms

    @property
    def active_duration_ms(self) -> int | None:
        """Wall-clock duration with detected idle gaps subtracted.

        ``None`` when no trace is linked or the trace lacked timestamp
        data; callers should fall back to ``duration_ms`` in that case.
        """
        if self.trace is None:
            return None
        return self.trace.active_duration_ms

    # mypy + pydantic ``@computed_field`` interaction (pydantic/pydantic#6709).
    @computed_field  # type: ignore[prop-decorator]
    @property
    def duration_reliable(self) -> bool:
        """Whether ``duration_ms`` represents active work rather than
        wall-clock-including-wait. ``True`` only when a subagent trace is
        linked: with a trace, the idle-gap heuristic can subtract user-
        wait time. Without a trace, the only available duration is the
        parent-side wall-clock, which silently includes any time the user
        spent away from the keyboard between tool calls. Consumers
        (formatter, ``duration_outlier`` signal, aggregate stats) should
        treat unreliable durations as wall-clock estimates, not active
        work, and either annotate or filter them out."""
        return self.trace is not None

    # mypy + pydantic ``@computed_field`` interaction (pydantic/pydantic#6709).
    @computed_field  # type: ignore[prop-decorator]
    @property
    def model_turns(self) -> int | None:
        """Number of model turns in this invocation's subagent trace --
        one merged, non-synthetic assistant message (#466; synthetic
        exclusion #507).

        ``None`` when no trace is linked (~20% of invocations in the
        dogfood corpus, see #468): turns can't be counted without the
        trace, and estimating from ``tool_uses`` would erase the
        turns-vs-tools distinction that makes the metric valuable, so we
        report the honest gap. Distinct from ``tool_uses`` — neither
        bounds the other (a turn can have zero or many tool calls).
        Emitted in ``model_dump`` output as a computed field."""
        if self.trace is None:
            return None
        return self.trace.model_turns

    @property
    def active_duration_per_tool_use(self) -> float | None:
        """Average active duration (ms) per tool call. Falls back to
        ``duration_per_tool_use`` when no trace is linked."""
        active = self.active_duration_ms
        if active is not None and self.tool_uses and self.tool_uses > 0:
            return active / self.tool_uses
        return self.duration_per_tool_use
