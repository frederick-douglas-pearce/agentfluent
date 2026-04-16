"""Extract agent invocations from parsed session messages.

Identifies Agent tool_use blocks in assistant messages and matches them
to their corresponding tool_result blocks to build AgentInvocation objects.
"""

from __future__ import annotations

from agentfluent.agents.models import AgentInvocation, is_builtin_agent
from agentfluent.core.session import SessionMessage


def extract_agent_invocations(messages: list[SessionMessage]) -> list[AgentInvocation]:
    """Extract agent invocations from a list of parsed session messages.

    Scans for assistant messages containing Agent tool_use blocks (name == "Agent"),
    then matches each to its corresponding tool_result by tool_use_id.

    Args:
        messages: Parsed session messages from the JSONL parser.

    Returns:
        List of AgentInvocation objects in session order.
    """
    # Build a lookup of tool_result messages by tool_use_id
    tool_results: dict[str, SessionMessage] = {}
    for msg in messages:
        if msg.type == "tool_result" and msg.tool_use_id:
            tool_results[msg.tool_use_id] = msg

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

            # Match to tool_result
            result = tool_results.get(tool_use.id)

            total_tokens = None
            tool_uses_count = None
            duration_ms = None
            agent_id = None
            output_text = ""

            if result is not None:
                output_text = result.text

                if result.metadata is not None:
                    total_tokens = result.metadata.total_tokens
                    tool_uses_count = result.metadata.tool_uses
                    duration_ms = result.metadata.duration_ms
                    agent_id = result.metadata.agent_id

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
