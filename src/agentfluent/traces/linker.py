"""Link parsed subagent traces to their parent ``AgentInvocation`` instances.

The parser produces a ``SubagentTrace`` with ``agent_type = UNKNOWN_AGENT_TYPE``
because the JSONL content alone isn't a reliable source. The parent session's
``Agent`` tool_use block carries the authoritative ``agent_type`` (as
``subagent_type`` in the tool input, extracted into ``AgentInvocation.agent_type``).
The linker matches traces to invocations by ``agent_id`` and overwrites the
trace's ``agent_type`` with the parent's value. Trace-level diagnostics read
``invocation.trace.agent_type`` as authoritative post-linking.

The loader is a ``Callable`` rather than a pre-parsed dict so that trace
files are only parsed on demand. With 350+ subagent files per project —
some >1MB — lazy parsing materially affects startup time on ``analyze``.
"""

from __future__ import annotations

from collections.abc import Callable

from agentfluent.agents.models import AgentInvocation
from agentfluent.traces.models import SubagentTrace


def link_traces(
    invocations: list[AgentInvocation],
    trace_loader: Callable[[str], SubagentTrace | None],
) -> list[AgentInvocation]:
    """Attach matching subagent traces to each invocation, in place.

    For every invocation with a non-``None`` ``agent_id``, call
    ``trace_loader(agent_id)``. When the loader returns a trace, overwrite
    its ``agent_type`` with the invocation's ``agent_type`` (parent is
    authoritative) and assign it to ``invocation.trace``. Invocations
    without an ``agent_id`` or with an unmatched loader return keep
    ``trace = None``.

    Returns the same list object it received, with ``trace`` fields
    mutated. Callers that want to track orphan traces (files whose
    ``agent_id`` doesn't match any invocation) should diff against the
    set of keys they passed into the loader's closure.
    """
    for invocation in invocations:
        if invocation.agent_id is None:
            continue
        trace = trace_loader(invocation.agent_id)
        if trace is None:
            continue
        trace.agent_type = invocation.agent_type
        invocation.trace = trace
    return invocations
