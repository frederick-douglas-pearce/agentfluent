"""Data models for parsed subagent trace JSONL files.

These models are the foundational contract for the v0.3 subagent trace
parser epic (E2). They are consumed by downstream stories across E3
(trace-level diagnostics), E4 (delegation pattern recognition), and E5
(model-routing diagnostics).

The parser (#103) produces ``SubagentTrace`` instances from files at
``~/.claude/projects/<slug>/<session-uuid>/subagents/agent-<agentId>.jsonl``.
The retry-sequence detector (#104) populates ``retry_sequences`` during
parsing. The linker (#105) attaches the trace to its parent
``AgentInvocation`` and overwrites ``agent_type`` with the parent's
reliably-populated value.
"""

from __future__ import annotations

from datetime import datetime
from functools import cached_property
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from agentfluent.core.session import Usage

INPUT_SUMMARY_MAX_CHARS = 200
RESULT_SUMMARY_MAX_CHARS = 500
UNKNOWN_AGENT_TYPE = "unknown"


class SubagentToolCall(BaseModel):
    """One internal tool invocation inside a subagent trace.

    A ``SubagentToolCall`` pairs the ``tool_use`` block with the
    subsequent ``tool_result`` block from the JSONL file. The parser
    (#103) truncates ``input_summary`` and ``result_summary`` to the
    module-level ``INPUT_SUMMARY_MAX_CHARS`` and
    ``RESULT_SUMMARY_MAX_CHARS`` constants; the model itself does not
    enforce those limits so fixtures and replay tools can construct
    untruncated instances.
    """

    model_config = ConfigDict(extra="ignore")

    tool_name: str
    input_summary: str
    result_summary: str
    is_error: bool = False
    usage: Usage = Field(default_factory=Usage)
    timestamp: datetime | None = None


class RetrySequence(BaseModel):
    """A group of consecutive same-tool calls with similar input.

    Produced by #104's detection algorithm during parse. ``tool_call_indices``
    reference entries in the owning ``SubagentTrace.tool_calls`` list so
    downstream consumers (#107) can cite specific calls as evidence without
    duplicating payloads. Index integrity is a #104 invariant: the model
    itself does not validate bounds against the parent trace.
    """

    model_config = ConfigDict(extra="ignore")

    tool_name: str
    attempts: int = Field(ge=1)
    first_error_message: str | None = None
    last_error_message: str | None = None
    eventual_success: bool = False
    tool_call_indices: list[int] = Field(default_factory=list)


class SubagentTrace(BaseModel):
    """A parsed subagent session — the full internal trace of one agent invocation.

    ``agent_type`` is set from three sources, in priority order:
    (1) overwritten by the linker (#105) from the parent
    ``AgentInvocation.agent_type`` after matching on ``agent_id``;
    (2) inferred by the parser (#103) from the delegation prompt when
    unlinked; (3) the default ``UNKNOWN_AGENT_TYPE`` for programmatically-
    constructed instances or traces whose agent type cannot be determined.

    ``unique_tool_names`` is a ``cached_property``, not a field. It does
    NOT appear in ``model_dump()`` or ``model_dump_json()`` output; the
    cache lives in the instance ``__dict__``. The trace is effectively
    write-once after the parser finalizes ``tool_calls`` — mutating
    ``tool_calls`` after first access leaves the cache stale.

    ``source_file`` may hold an absolute path that can leak usernames if
    serialized verbatim. The parser (#103) is responsible for normalizing
    the path before setting this field; the model stores whatever it
    receives.
    """

    model_config = ConfigDict(extra="ignore")

    agent_id: str
    agent_type: str = UNKNOWN_AGENT_TYPE
    delegation_prompt: str
    tool_calls: list[SubagentToolCall] = Field(default_factory=list)
    retry_sequences: list[RetrySequence] = Field(default_factory=list)
    total_errors: int = 0
    total_retries: int = 0
    usage: Usage = Field(default_factory=Usage)
    duration_ms: int | None = None
    source_file: Path | None = None

    model: str | None = None
    """Model observed on the first assistant message in the subagent's
    trace (e.g., ``'claude-sonnet-4-6'``). ``None`` when the trace has
    no assistant messages. Set at parse time by the trace parser;
    downstream (``diagnostics.model_routing``) uses this as a fallback
    when the agent's ``AgentConfig`` doesn't declare a model explicitly —
    which is the common case for Claude Code subagents that inherit the
    parent session's model."""

    @cached_property
    def unique_tool_names(self) -> set[str]:
        return {tc.tool_name for tc in self.tool_calls}
