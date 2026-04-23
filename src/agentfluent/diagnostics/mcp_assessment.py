"""MCP tool usage extraction and aggregation.

Observed-usage counterpart to ``config/mcp_discovery.py`` (which reads
configured servers). This module discovers what MCP tools the agent
*actually called*, from two sources:

- **Subagent traces** — ``SubagentTrace.tool_calls`` carry per-call
  ``tool_name`` and ``is_error``. Populated by #101's trace parser.
- **Parent-session assistant messages** — ``tool_use`` content blocks
  in raw ``SessionMessage`` data, paired with their ``tool_result``
  content blocks by ``tool_use_id`` to recover the error state.
  Extracted by ``extract_mcp_calls_from_messages`` during
  ``analyze_session`` and surfaced via ``SessionAnalysis.mcp_tool_calls``.

Audit logic (comparing observed usage to configured servers) lives in
#118 — this module does extraction only. The two outputs converge in
``extract_mcp_usage`` which aggregates per-server.

**Parsing.** MCP tool names follow the ``mcp__<server>__<tool>``
format. Server names can contain internal underscores
(e.g., ``claude_ai_Gmail``); tool names can start with an underscore
(e.g., ``_internal_sync``). Parsing uses ``rfind("__")`` on the
stripped suffix so the last ``__`` is the server/tool boundary.
A server name containing ``__`` is fundamentally ambiguous in this
format — not handled.
"""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from agentfluent.diagnostics.signals import ERROR_REGEX

if TYPE_CHECKING:
    from agentfluent.agents.models import AgentInvocation
    from agentfluent.core.session import SessionMessage


_MCP_PREFIX = "mcp__"


def parse_mcp_tool_name(name: str) -> tuple[str, str] | None:
    """Split an MCP tool name into ``(server_name, tool_name)``.

    Returns ``None`` for names that don't match ``mcp__<server>__<tool>``
    or that have an empty server or tool component. Splits at the
    FIRST ``__`` after the ``mcp__`` prefix — everything before it is
    the server (may contain single underscores), everything after is
    the tool (may start with underscore, may contain ``__``).

    Examples:

    - ``mcp__github__create_issue`` → ``("github", "create_issue")``
    - ``mcp__claude_ai_Gmail__authenticate`` →
      ``("claude_ai_Gmail", "authenticate")``
    - ``mcp__srv___internal_sync`` → ``("srv", "_internal_sync")``

    Limitation: server names containing ``__`` are fundamentally
    ambiguous in this format — the first ``__`` is treated as the
    server/tool boundary regardless. Not a real-world concern
    (observed Claude Code servers like ``github`` and
    ``claude_ai_Gmail`` don't use ``__``).
    """
    if not name.startswith(_MCP_PREFIX):
        return None
    rest = name[len(_MCP_PREFIX):]
    idx = rest.find("__")
    if idx <= 0:
        return None
    server, tool = rest[:idx], rest[idx + 2:]
    if not server or not tool:
        return None
    return server, tool


class McpToolCall(BaseModel):
    """A single observed MCP tool invocation.

    Produced by ``extract_mcp_calls_from_messages`` (parent-session
    source) and carried on ``SessionAnalysis.mcp_tool_calls``. The
    trace source keeps using ``SubagentToolCall`` directly — both
    flow into ``extract_mcp_usage`` for aggregation.
    """

    model_config = ConfigDict(frozen=True)

    server_name: str
    tool_name: str
    is_error: bool


class McpServerUsage(BaseModel):
    """Per-server aggregated MCP usage, output of ``extract_mcp_usage``."""

    model_config = ConfigDict(frozen=True)

    server_name: str
    total_calls: int
    unique_tools: list[str]
    """Sorted for deterministic output — derived from an internal set."""

    error_count: int


def extract_mcp_calls_from_messages(
    messages: list[SessionMessage],
) -> list[McpToolCall]:
    """Scan parent-session messages for MCP tool_use / tool_result pairs.

    Pairs each assistant ``tool_use`` with its corresponding user
    ``tool_result`` by ``tool_use_id`` to determine ``is_error``.
    Error detection priority:

    1. Explicit ``tool_result.is_error`` (True or False) — trust the
       field when present.
    2. Missing ``is_error`` + result text matches ``ERROR_REGEX`` →
       treat as error. Mirrors the metadata-layer ``ERROR_PATTERN``
       detection; carries the same false-positive risk and is tested.
    3. No paired ``tool_result`` at all (interrupted session) →
       treated as not-error. Absence of evidence is not evidence of
       failure.

    Non-MCP ``tool_use`` blocks are silently skipped via
    ``parse_mcp_tool_name`` returning ``None``.
    """
    # Index tool_result blocks by tool_use_id. Store (text, is_error)
    # so the second pass can apply the priority ladder above without
    # re-walking content_blocks.
    results_by_id: dict[str, tuple[str, bool | None]] = {}
    for msg in messages:
        if msg.type != "user":
            continue
        for block in msg.content_blocks:
            if block.type == "tool_result" and block.tool_use_id:
                results_by_id[block.tool_use_id] = (
                    block.text or "",
                    block.is_error,
                )

    calls: list[McpToolCall] = []
    for msg in messages:
        if msg.type != "assistant":
            continue
        for tu in msg.tool_use_blocks:
            parsed = parse_mcp_tool_name(tu.name)
            if parsed is None:
                continue
            server, tool = parsed
            result_text, explicit_error = results_by_id.get(tu.id, ("", None))
            if explicit_error is True:
                is_error = True
            elif explicit_error is False:
                is_error = False
            else:
                is_error = bool(ERROR_REGEX.search(result_text))
            calls.append(
                McpToolCall(
                    server_name=server, tool_name=tool, is_error=is_error,
                ),
            )
    return calls


class _Accumulator:
    """Mutable per-server accumulator used during aggregation.

    Not exposed — callers see ``McpServerUsage`` instances produced by
    ``build``.
    """

    def __init__(self) -> None:
        self.tools: set[str] = set()
        self.total_calls: int = 0
        self.error_count: int = 0

    def add(self, tool_name: str, *, is_error: bool) -> None:
        self.tools.add(tool_name)
        self.total_calls += 1
        if is_error:
            self.error_count += 1

    def build(self, server_name: str) -> McpServerUsage:
        return McpServerUsage(
            server_name=server_name,
            total_calls=self.total_calls,
            unique_tools=sorted(self.tools),
            error_count=self.error_count,
        )


def extract_mcp_usage(
    invocations: list[AgentInvocation],
    session_mcp_calls: list[McpToolCall] | None = None,
) -> dict[str, McpServerUsage]:
    """Aggregate observed MCP tool usage per server across both sources.

    - **Trace source**: walks ``inv.trace.tool_calls`` for each
      invocation with a linked ``SubagentTrace``. Each call's
      ``tool_name`` is parsed; non-MCP names are skipped.
    - **Parent-session source**: consumes the already-parsed
      ``McpToolCall`` list produced by
      ``extract_mcp_calls_from_messages``.

    Returns an empty dict when no MCP tools appear in either source.
    Duplicates across sources don't occur in practice — a trace is
    scoped to a subagent's internal work, while session-level calls
    happen outside any trace.
    """
    agg: dict[str, _Accumulator] = defaultdict(_Accumulator)

    for inv in invocations:
        trace = inv.trace
        if trace is None:
            continue
        for trace_call in trace.tool_calls:
            parsed = parse_mcp_tool_name(trace_call.tool_name)
            if parsed is None:
                continue
            server, tool = parsed
            agg[server].add(tool, is_error=trace_call.is_error)

    for session_call in session_mcp_calls or ():
        agg[session_call.server_name].add(
            session_call.tool_name, is_error=session_call.is_error,
        )

    return {server: a.build(server) for server, a in agg.items()}
