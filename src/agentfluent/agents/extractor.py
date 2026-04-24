"""Extract agent invocations from parsed session messages."""

from __future__ import annotations

from agentfluent.agents.models import AgentInvocation
from agentfluent.core.session import SessionMessage, index_tool_results_by_id


def extract_agent_invocations(messages: list[SessionMessage]) -> list[AgentInvocation]:
    """Match Agent tool_use blocks to their tool_result content blocks by
    tool_use_id and pull metadata from the result-carrying message.
    """
    results = index_tool_results_by_id(messages)

    invocations: list[AgentInvocation] = []

    for msg in messages:
        if msg.type != "assistant":
            continue

        for tool_use in msg.tool_use_blocks:
            if tool_use.name != "Agent":
                continue

            # Claude Code's Agent tool defaults to ``general-purpose`` when
            # ``subagent_type`` is omitted. Some older Claude Code versions
            # and some caller-side skills invoked Agent without specifying
            # the field; the resulting tool_use blocks have no
            # ``subagent_type`` key even though the invocation ran as
            # general-purpose. Match the tool's own default rather than
            # labeling those invocations "unknown" and excluding them from
            # the general-purpose delegation bucket (#169).
            agent_type = tool_use.input.get("subagent_type", "general-purpose")
            description = tool_use.input.get("description", "")
            prompt = tool_use.input.get("prompt", "")

            entry = results.get(tool_use.id)
            if entry is not None:
                container, output_text, _ = entry
            else:
                container, output_text = None, ""

            total_tokens = None
            tool_uses_count = None
            duration_ms = None
            agent_id = None

            if container is not None and container.metadata is not None:
                total_tokens = container.metadata.total_tokens
                tool_uses_count = container.metadata.tool_uses
                duration_ms = container.metadata.duration_ms
                agent_id = container.metadata.agent_id

            invocations.append(
                AgentInvocation(
                    agent_type=agent_type,
                    description=description,
                    prompt=prompt,
                    tool_use_id=tool_use.id,
                    total_tokens=total_tokens,
                    tool_uses=tool_uses_count,
                    duration_ms=duration_ms,
                    agent_id=agent_id,
                    output_text=output_text,
                )
            )

    return invocations
