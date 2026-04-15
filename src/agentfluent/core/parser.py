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
    """Parse a 'user' type message."""
    message = data.get("message", {})
    return SessionMessage(
        type="user",
        timestamp=_parse_timestamp(data.get("timestamp")),
        content_blocks=_normalize_content(message.get("content")),
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
        model=message.get("model"),
        content_blocks=_normalize_content(message.get("content")),
        usage=usage,
    )


def _parse_tool_result(data: dict[str, Any]) -> SessionMessage:
    """Parse a 'tool_result' type message."""
    raw_content = data.get("content")
    content_blocks = _normalize_content(raw_content)

    metadata = None
    raw_metadata = data.get("metadata")
    if raw_metadata and isinstance(raw_metadata, dict):
        metadata = ToolResultMetadata.model_validate(raw_metadata)

    return SessionMessage(
        type="tool_result",
        timestamp=_parse_timestamp(data.get("timestamp")),
        tool_use_id=data.get("tool_use_id"),
        is_error=bool(data.get("is_error", False)),
        content_blocks=content_blocks,
        metadata=metadata,
    )


def parse_session(path: Path) -> list[SessionMessage]:
    """Parse a JSONL session file into a list of SessionMessage objects.

    Reads the file line by line. Skips non-analytical message types and
    malformed lines (with a warning log). Returns an empty list for
    empty files or files that contain no analytical messages.

    Args:
        path: Path to the .jsonl session file.

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
                elif msg_type == "tool_result":
                    messages.append(_parse_tool_result(data))
                else:
                    logger.debug(
                        "Unknown message type '%s' at %s:%d", msg_type, path.name, line_num
                    )
            except Exception:
                logger.warning(
                    "Failed to parse message at %s:%d", path.name, line_num, exc_info=True
                )
                continue

    return messages
