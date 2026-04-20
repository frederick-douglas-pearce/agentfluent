"""JSONL session file parser.

Reads session files line by line and produces typed SessionMessage objects.
Handles both string and array content formats, skips non-analytical message types,
and gracefully handles malformed lines.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from agentfluent.core.session import (
    SKIP_TYPES,
    ContentBlock,
    SessionMessage,
    ToolResultMetadata,
    Usage,
)

logger = logging.getLogger(__name__)


def _normalize_content(raw_content: str | list[dict[str, Any]] | None) -> list[ContentBlock]:
    """Normalize message content to a list of ContentBlock.

    Handles:
    - Plain string -> single text ContentBlock
    - Array of typed blocks -> list of ContentBlock
    - None/missing -> empty list
    """
    if raw_content is None:
        return []

    if isinstance(raw_content, str):
        return [ContentBlock(type="text", text=raw_content)] if raw_content else []

    if isinstance(raw_content, list):
        blocks: list[ContentBlock] = []
        for item in raw_content:
            if not isinstance(item, dict):
                continue
            block_type = item.get("type", "text")
            if block_type == "text":
                blocks.append(ContentBlock(type="text", text=item.get("text", "")))
            elif block_type == "tool_use":
                blocks.append(
                    ContentBlock(
                        type="tool_use",
                        id=item.get("id"),
                        name=item.get("name"),
                        input=item.get("input"),
                    )
                )
            elif block_type == "tool_result":
                # `content` can be a string or a list of sub-blocks; richer
                # sub-block shapes are captured as None pending a use case.
                result_content = item.get("content")
                result_text = (
                    result_content if isinstance(result_content, str) else None
                )
                blocks.append(
                    ContentBlock(
                        type="tool_result",
                        tool_use_id=item.get("tool_use_id"),
                        text=result_text,
                    )
                )
            else:
                # Preserve unknown block types as text with the type field
                blocks.append(ContentBlock(type=block_type, text=item.get("text")))
        return blocks

    return []


def _parse_timestamp(raw: str | None) -> datetime | None:
    """Parse an ISO 8601 timestamp string."""
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _parse_user_message(data: dict[str, Any]) -> SessionMessage:
    """Parse a 'user' type message.

    Agent tool results arrive as a `toolUseResult` sibling to `message`;
    when present it lands on `SessionMessage.metadata`. See CLAUDE.md's
    JSONL format section for the shape.
    """
    message = data.get("message", {})

    metadata = None
    raw_tool_use_result = data.get("toolUseResult")
    if raw_tool_use_result and isinstance(raw_tool_use_result, dict):
        metadata = ToolResultMetadata.model_validate(raw_tool_use_result)

    return SessionMessage(
        type="user",
        timestamp=_parse_timestamp(data.get("timestamp")),
        content_blocks=_normalize_content(message.get("content")),
        metadata=metadata,
    )


def _parse_assistant_message(data: dict[str, Any]) -> SessionMessage:
    """Parse an 'assistant' type message."""
    message = data.get("message", {})
    usage_data = message.get("usage")

    usage = None
    if usage_data and isinstance(usage_data, dict):
        usage = Usage(
            input_tokens=usage_data.get("input_tokens", 0),
            output_tokens=usage_data.get("output_tokens", 0),
            cache_creation_input_tokens=usage_data.get("cache_creation_input_tokens", 0),
            cache_read_input_tokens=usage_data.get("cache_read_input_tokens", 0),
        )

    return SessionMessage(
        type="assistant",
        timestamp=_parse_timestamp(data.get("timestamp")),
        message_id=message.get("id"),
        model=message.get("model"),
        content_blocks=_normalize_content(message.get("content")),
        usage=usage,
    )


def deduplicate_messages(messages: list[SessionMessage]) -> list[SessionMessage]:
    """Deduplicate streaming snapshot assistant messages by message_id.

    Claude Code writes multiple assistant messages per API call as streaming
    snapshots. All snapshots share the same message.id. Within a group:
    - input_tokens/cache tokens are identical across snapshots
    - output_tokens increases with each snapshot (partial -> final)

    This function keeps the entry with the highest output_tokens per message_id.
    Non-assistant messages and assistant messages without a message_id pass
    through unchanged.
    """
    # Track best assistant message per message_id
    best_by_id: dict[str, SessionMessage] = {}
    # Track insertion order for stable output
    id_order: list[str] = []

    result: list[SessionMessage] = []

    for msg in messages:
        if msg.type != "assistant" or not msg.message_id:
            result.append(msg)
            continue

        mid = msg.message_id
        existing = best_by_id.get(mid)
        if existing is None:
            best_by_id[mid] = msg
            id_order.append(mid)
            # Insert a placeholder — we'll replace later
            result.append(msg)
        else:
            # Keep the one with higher output_tokens
            existing_output = existing.usage.output_tokens if existing.usage else 0
            new_output = msg.usage.output_tokens if msg.usage else 0
            if new_output > existing_output:
                best_by_id[mid] = msg

    # Replace placeholders with the best version
    seen: set[str] = set()
    final: list[SessionMessage] = []
    for msg in result:
        if msg.type == "assistant" and msg.message_id:
            mid = msg.message_id
            if mid not in seen:
                seen.add(mid)
                final.append(best_by_id[mid])
            # Skip duplicates (placeholders for IDs we've already emitted)
        else:
            final.append(msg)

    return final


def parse_session(path: Path, *, deduplicate: bool = True) -> list[SessionMessage]:
    """Parse a JSONL session file into a list of SessionMessage objects.

    Reads the file line by line. Skips non-analytical message types and
    malformed lines (with a warning log). Returns an empty list for
    empty files or files that contain no analytical messages.

    Streaming snapshot deduplication is applied by default: assistant
    messages sharing the same message.id are collapsed, keeping the
    entry with the highest output_tokens.

    Args:
        path: Path to the .jsonl session file.
        deduplicate: Whether to deduplicate streaming snapshots. Default True.

    Returns:
        List of parsed SessionMessage objects in file order.
    """
    messages: list[SessionMessage] = []

    if not path.exists():
        logger.warning("Session file not found: %s", path)
        return messages

    if path.stat().st_size == 0:
        return messages

    with path.open() as f:
        for line_num, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue

            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("Malformed JSON at %s:%d", path.name, line_num)
                continue

            if not isinstance(data, dict):
                logger.warning("Non-object JSON at %s:%d", path.name, line_num)
                continue

            msg_type = data.get("type")
            if not msg_type or msg_type in SKIP_TYPES:
                continue

            try:
                if msg_type == "user":
                    messages.append(_parse_user_message(data))
                elif msg_type == "assistant":
                    messages.append(_parse_assistant_message(data))
                else:
                    logger.debug(
                        "Unknown message type '%s' at %s:%d", msg_type, path.name, line_num
                    )
            except Exception:
                logger.warning(
                    "Failed to parse message at %s:%d", path.name, line_num, exc_info=True
                )
                continue

    if deduplicate:
        messages = deduplicate_messages(messages)

    return messages
