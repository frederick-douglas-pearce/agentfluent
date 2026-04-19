"""Extract agent invocations from parsed session messages.

Identifies Agent tool_use blocks in assistant messages and matches them
to their corresponding tool_result blocks to build AgentInvocation objects.

Claude Code emits tool results as `tool_result` content blocks *inside*
user messages, with agent metadata on the containing user message's
`toolUseResult` key (surfaced by the parser on `SessionMessage.metadata`).
This extractor walks user messages' content blocks to index tool results
by `tool_use_id`, then resolves metadata from the outer user message.
"""

from __future__ import annotations

from agentfluent.agents.models import AgentInvocation, is_builtin_agent
from agentfluent.core.session import SessionMessage


def extract_agent_invocations(messages: list[SessionMessage]) -> list[AgentInvocation]:
    """Extract agent invocations from a list of parsed session messages.

    Scans for assistant messages containing Agent tool_use blocks (name == "Agent"),
    then matches each to its corresponding tool_result content block (which lives
    inside a user message) by tool_use_id. Metadata is pulled from the containing
    user message's `metadata` field (populated by the parser from `toolUseResult`).

    Also falls back to top-level `tool_result`-type SessionMessages if present —
    a legacy / alternate shape that the parser still tolerates.

    Args:
        messages: Parsed session messages from the JSONL parser.

    Returns:
        List of AgentInvocation objects in session order.
    """
    # Build lookups keyed by tool_use_id:
    #   - result_containers: the SessionMessage that holds the result (user message
    #     in the real shape; tool_result message in the legacy shape). Used to
    #     access metadata.
    #   - result_texts: the text content of the result block itself.
    result_containers: dict[str, SessionMessage] = {}
    result_texts: dict[str, str] = {}

    for msg in messages:
        if msg.type == "user":
            for block in msg.content_blocks:
                if block.type == "tool_result" and block.tool_use_id:
                    result_containers[block.tool_use_id] = msg
                    result_texts[block.tool_use_id] = block.text or ""
        elif msg.type == "tool_result" and msg.tool_use_id:
            # Legacy shape: tool_result as a top-level message.
            result_containers[msg.tool_use_id] = msg
            result_texts[msg.tool_use_id] = msg.text

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

            container = result_containers.get(tool_use.id)
            output_text = result_texts.get(tool_use.id, "")

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
