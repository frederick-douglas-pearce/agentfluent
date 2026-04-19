"""Extract agent invocations from parsed session messages."""

from __future__ import annotations

from agentfluent.agents.models import AgentInvocation, is_builtin_agent
from agentfluent.core.session import SessionMessage


def extract_agent_invocations(messages: list[SessionMessage]) -> list[AgentInvocation]:
    """Match Agent tool_use blocks to their tool_result content blocks by
    tool_use_id and pull metadata from the result-carrying message.
    """
    results: dict[str, tuple[SessionMessage, str]] = {}

    for msg in messages:
        if msg.type == "user":
            for block in msg.content_blocks:
                if block.type == "tool_result" and block.tool_use_id:
                    results[block.tool_use_id] = (msg, block.text or "")
        elif msg.type == "tool_result" and msg.tool_use_id:
            results[msg.tool_use_id] = (msg, msg.text)

    invocations: list[AgentInvocation] = []

    for msg in messages:
        if msg.type != "assistant":
            continue

        for tool_use in msg.tool_use_blocks:
            if tool_use.name != "Agent":
                continue

            agent_type = tool_use.input.get("subagent_type", "unknown")
            description = tool_use.input.get("description", "")
            prompt = tool_use.input.get("prompt", "")

            entry = results.get(tool_use.id)
            container, output_text = entry if entry else (None, "")

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
                    is_builtin=is_builtin_agent(agent_type),
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
