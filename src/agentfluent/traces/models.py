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
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, computed_field

from agentfluent.core.session import Usage

INPUT_SUMMARY_MAX_CHARS = 200
RESULT_SUMMARY_MAX_CHARS = 500
# Cap on the serialized size of ``SubagentToolCall.input_data``. The full
# raw input dict is captured for paste-ready ``input_examples`` extraction
# (#405), but tool inputs can be large (a Bash heredoc or a Write payload
# runs to tens of KB), so we only retain the dict when its JSON form fits
# this budget. Oversized inputs store ``None`` -- the PARAMETER_RETRY
# paste-ready section is omitted rather than emitting a truncated,
# invalid-JSON example. ``input_keys`` is always captured regardless of
# size, since shape comparison only needs the keys.
INPUT_DATA_MAX_CHARS = 2000
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

    ``timestamp`` is the assistant ``tool_use`` message timestamp;
    ``result_timestamp`` is the matching user ``tool_result`` message
    timestamp. Both Optional because: pre-trace-capture sessions, the
    pairing miss case, and programmatically-constructed instances.
    """

    model_config = ConfigDict(extra="ignore")

    tool_name: str
    input_summary: str
    result_summary: str
    is_error: bool = False
    usage: Usage = Field(default_factory=Usage)
    timestamp: datetime | None = None
    result_timestamp: datetime | None = None

    input_keys: list[str] = Field(default_factory=list)
    """Top-level keys of the ``tool_use`` input dict, in source order.
    The primary evidence for PARAMETER_RETRY (#405) input-shape comparison:
    keys added/removed between consecutive same-tool calls signal that the
    agent is guessing at the parameter shape. Always captured (cheap and
    size-independent), unlike ``input_data``. Empty when the call had no
    input or the input wasn't a dict."""

    input_data: dict[str, Any] | None = None
    """The full ``tool_use`` input dict, JSON-normalized (``json.dumps``
    round-trip with ``default=str``) so it holds only plain JSON types and
    is safe to re-serialize. ``None`` when the input was absent OR its
    serialized form exceeded ``INPUT_DATA_MAX_CHARS`` -- see that constant
    for the rationale. Used by PARAMETER_RETRY (#405) to extract a
    paste-ready ``input_examples`` entry from a successful call. Carries
    raw tool arguments, which may include paths or other user content;
    consumers render it under the D002 "informational, never auto-applied"
    caveat."""


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

    model_turns: int = 0
    """Number of model turns in this subagent trace -- one merged,
    non-synthetic assistant message. Set at parse time by counting
    ``type == "assistant"`` messages (excluding ``<synthetic>`` ghost
    responses, #507) after fragment-merging (#466). Independent of
    ``tool_calls``: a single turn can carry zero
    ``tool_use`` blocks (text/thinking/refusal) or many (parallel tool
    use), so neither count bounds the other. Always definitive for a
    trace (``0`` for an empty trace); ``AgentInvocation.model_turns`` is
    ``int | None`` because the trace itself may be absent."""

    usage: Usage = Field(default_factory=Usage)
    duration_ms: int | None = None
    idle_gap_ms: int | None = None
    """Sum of per-call gaps flagged as idle by ``traces.parser._compute_idle_gap_ms``.
    ``None`` when fewer than two paired tool calls have both timestamps;
    ``0`` when computable but no gap met the threshold."""

    source_file: Path | None = None

    parent_invocation_id: str | None = None
    """Invocation that spawned this agent, or ``None`` at depth 1 (#595 PR B).

    Identity domain is ``AgentInvocation.invocation_id`` (``agent_id or
    tool_use_id``), and **every non-``None`` value here joins against
    ``SessionAnalysis.invocations``** -- an invariant, not a coincidence:
    attachment is capped at depth 2 (#595 AC2 as amended), so the only agent
    that can be named as a parent is one that owns an invocation row. Lifting
    that cap (#659) makes the domain "parent invocation id *or* parent agent
    id" and requires relaxing this sentence with it.

    **``None`` is the depth-1 value, not a missing value, and there is
    deliberately no sentinel id.** A synthetic "root" id would join to nothing
    and force every consumer to special-case it; ``None`` says the same thing
    in the type. ``None`` also covers the unattributable case: a trace
    whose sidecar is absent, or whose ``toolUseId`` resolves to no emitter.
    The linker leaves this ``None`` rather than guessing a parent.

    **Such a trace is NOT distinguishable from a genuine depth-1 trace by
    these two fields.** Every degradation branch yields ``(None, 1)``, which is
    exactly what a real depth-1 trace carries. That is deliberate -- guessing a
    parent would be worse -- but it is why the orphan cohort needs its own
    disclosure (#648 AC3) rather than being derivable from ``depth``.

    **Public JSON API** (D055): serialized via ``AgentInvocation.trace`` ->
    ``SessionAnalysis.invocations`` -> ``analyze --format json``. Additive, so
    the envelope stays ``"2"`` (D029)."""

    depth: int = 1
    """Delegation depth: 1 for an agent spawned by the main session, 2 for one
    spawned by a depth-1 agent (#595 PR B).

    **In emitted output this is only ever 1 or 2** while attachment is capped
    at depth 2 (#595 AC2 as amended): a deeper trace is never attached, so no
    object carrying ``depth >= 3`` reaches ``analyze --format json``.
    ``resolve_lineage`` itself computes arbitrary depth; #659 is what would
    surface it.

    Derived from the cross-file ``toolUseId`` join, never from path shape --
    the on-disk layout is **flat at all depths** (every trace is a sibling in
    ``subagents/``), so depth is not recoverable from the filesystem.

    ``1`` is also the fallback when depth cannot be established (no sidecar,
    or a ``toolUseId`` that resolves to no emitter). That biases toward
    under-reporting nesting rather than inventing it, and pairs with
    ``parent_invocation_id = None`` so no consumer sees a depth it cannot
    trace to a parent.

    **Public JSON API** (D055), additive; envelope stays ``"2"`` (D029)."""

    children: list[SubagentTrace] = Field(default_factory=list)
    """Traces spawned *by this agent* -- the depth+1 tier (#595 PR B).

    This is how a depth->=2 trace reaches public JSON at all. It deliberately
    does **not** become an ``AgentInvocation`` row: ``SessionAnalysis.invocations``
    means "one row = one main-session delegation", and synthesizing rows for
    deeper agents would silently move every published agent aggregate
    (``total_invocations``, ``by_agent_type``, ``builtin``/``custom`` counts)
    while minting rows whose ``total_tokens`` could only be scraped from a
    prose trailer -- indistinguishable, to every consumer, from JSON-sourced
    rows. Hanging the child off its parent keeps invalid states
    unrepresentable.

    **Size caveat.** This inlines the full child payload (``tool_calls``,
    ``retry_sequences``) into ``analyze --format json``. Accepted for KISS and
    for consistency with the existing ``invocation.trace`` nesting; if payload
    size bites, the escape hatch is a flat adjacency map on ``SessionAnalysis``
    keyed by ``invocation_id``, which does not change these two fields.

    Empty for the overwhelming majority of traces -- 1.2% of a real 1333-trace
    corpus had any child at all."""

    model: str | None = None
    """Model observed on the first assistant message in the subagent's
    trace (e.g., ``'claude-sonnet-4-6'``). ``None`` when the trace has
    no assistant messages. Set at parse time by the trace parser;
    downstream (``diagnostics.model_routing``) uses this as a fallback
    when the agent's ``AgentConfig`` doesn't declare a model explicitly —
    which is the common case for Claude Code subagents that inherit the
    parent session's model."""

    @computed_field  # type: ignore[prop-decorator]
    @property
    def active_duration_ms(self) -> int | None:
        """Wall-clock duration with detected idle gaps subtracted.

        ``None`` when ``duration_ms`` or ``idle_gap_ms`` is ``None``.
        Clamped at zero to guard against any pathological case where
        summed idle gaps exceed wall-clock span (overlapping calls,
        clock skew, future heuristic changes).
        """
        if self.duration_ms is None or self.idle_gap_ms is None:
            return None
        return max(self.duration_ms - self.idle_gap_ms, 0)

    @cached_property
    def unique_tool_names(self) -> set[str]:
        return {tc.tool_name for tc in self.tool_calls}
