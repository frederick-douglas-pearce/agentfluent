"""JSONL session file parser.

Reads session files line by line and produces typed SessionMessage objects.
Handles both string and array content formats, skips non-analytical message types,
and gracefully handles malformed lines.
"""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import Iterator
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

_MAX_WARN_LINE_CHARS = 100


def _emit_parse_warning(path: Path, line_num: int, reason: str, line: str) -> None:
    """Print a parse-time warning to stderr and mirror to the logger.

    Stderr emission uses a ``WARNING:`` prefix and truncates the offending
    line to ``_MAX_WARN_LINE_CHARS`` so the user can decide whether to
    investigate without flooding the terminal. Flushed before the caller
    writes to stdout so the warning never lands inside a Rich table.
    """
    snippet = line.strip()
    if len(snippet) > _MAX_WARN_LINE_CHARS:
        snippet = snippet[:_MAX_WARN_LINE_CHARS] + "..."
    snippet = snippet.replace("\x00", "\\x00")
    message = f"WARNING: {reason} at {path.name}:{line_num}: {snippet}"
    print(message, file=sys.stderr, flush=True)
    logger.warning("%s at %s:%d", reason, path.name, line_num)


def iter_raw_messages(path: Path) -> Iterator[tuple[int, dict[str, Any]]]:
    """Iterate a JSONL file, yielding (line_num, data) for analytical messages.

    Encapsulates the line-reading, JSON-decoding, and SKIP_TYPES filtering
    that every session-file parser needs. Callers dispatch on ``data["type"]``
    and build their own typed objects. Used by both the main-session parser
    (``parse_session``) and the subagent trace parser.

    ``line_num`` is 1-indexed and refers to the raw line in the file (not
    the post-filter position), so downstream error logs can pinpoint the
    originating line even though some lines were skipped. Callers that
    don't need it can unpack as ``for _, data in ...``.

    Silently skips: missing files, empty files, empty lines, and any
    message whose ``type`` is in ``SKIP_TYPES`` or is missing. Malformed
    JSON and non-object JSON lines emit a ``WARNING:``-prefixed line to
    stderr (with the offending content truncated to
    ``_MAX_WARN_LINE_CHARS``) and continue.
    """
    if not path.exists():
        logger.warning("Session file not found: %s", path)
        return

    if path.stat().st_size == 0:
        return

    with path.open() as f:
        for line_num, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue

            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                _emit_parse_warning(path, line_num, "Malformed JSON", line)
                continue

            if not isinstance(data, dict):
                _emit_parse_warning(path, line_num, "Non-object JSON", line)
                continue

            msg_type = data.get("type")
            if not msg_type or msg_type in SKIP_TYPES:
                continue

            yield line_num, data


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
                # ``content`` is either a string (single-line tool output) or
                # a list of sub-blocks. Agent results in particular use the
                # list form ``[{"type": "text", "text": "..."}]``; the prior
                # implementation dropped them as None, leaving downstream
                # consumers (e.g. REVIEWER_CAUGHT detection) with no input.
                # Concatenate the text-typed sub-blocks; non-text shapes
                # remain unsupported but are preserved as a fallback.
                result_content = item.get("content")
                if isinstance(result_content, str):
                    result_text: str | None = result_content
                elif isinstance(result_content, list):
                    parts = [
                        sub.get("text", "")
                        for sub in result_content
                        if isinstance(sub, dict) and sub.get("type") == "text"
                    ]
                    result_text = "\n".join(p for p in parts if p) or None
                else:
                    result_text = None
                blocks.append(
                    ContentBlock(
                        type="tool_result",
                        tool_use_id=item.get("tool_use_id"),
                        text=result_text,
                        is_error=item.get("is_error"),
                    )
                )
            else:
                # Preserve unknown block types as text with the type field
                blocks.append(ContentBlock(type=block_type, text=item.get("text")))
        return blocks

    return []


def parse_timestamp(raw: str | None) -> datetime | None:
    """Parse an ISO 8601 timestamp string.

    Tolerates the trailing ``Z`` UTC marker that Claude Code emits and
    returns ``None`` for missing or malformed input rather than raising,
    so callers can iterate JSONL safely without per-line guards.
    """
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
        timestamp=parse_timestamp(data.get("timestamp")),
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
        timestamp=parse_timestamp(data.get("timestamp")),
        message_id=message.get("id"),
        model=message.get("model"),
        content_blocks=_normalize_content(message.get("content")),
        usage=usage,
    )


def _pick_max_tokens_fragment(fragments: list[SessionMessage]) -> SessionMessage:
    """Return the fragment with the highest ``usage.output_tokens``.

    Treats missing usage as 0 tokens. Ties resolve to the first
    fragment with that value. Used by ``_merge_fragment_group`` for
    both the streaming-path block selection and usage-metadata lift.
    """
    return max(
        fragments,
        key=lambda f: f.usage.output_tokens if f.usage else 0,
    )


def _merge_fragment_group(fragments: list[SessionMessage]) -> SessionMessage:
    """Merge a list of assistant-message fragments that share a `message_id`.

    Two JSONL shapes appear in real data under the same message_id:

    **Block-per-line** (the current Claude Code shape — ~100% of our
    observed data). Each fragment carries one content block (text,
    thinking, or tool_use). All fragments share the same ``output_tokens``
    because it reports the full message's total, not a per-block count.
    Merge: union every content block across fragments so nothing is
    dropped.

    **Classical streaming snapshots** (legacy; no longer observed but
    covered for robustness). Each fragment is a cumulative snapshot of
    the growing response. ``output_tokens`` increases monotonically from
    partial → final. Merge: keep the max-``output_tokens`` fragment's
    blocks intact; earlier snapshots are strict prefixes of the final.

    Detection uses a single invariant: all fragments sharing the same
    ``output_tokens`` → block-per-line; varying tokens → streaming.
    Both paths dedup ``tool_use`` blocks by ``tool_use_id`` so
    re-observations can't double-emit a tool call. Fragments with
    missing ``usage`` metadata collapse the distinct-token set toward
    empty and route through the block-per-line path; this is the
    conservative choice (union is safer than drop).
    """
    if len(fragments) == 1:
        return fragments[0]

    output_tokens = {
        f.usage.output_tokens for f in fragments if f.usage is not None
    }
    is_block_per_line = len(output_tokens) <= 1
    winner = _pick_max_tokens_fragment(fragments)

    if is_block_per_line:
        # Union content blocks. Dedup tool_use by `ContentBlock.id` (the
        # field carries the tool_use_id for tool_use blocks); keep all
        # text/thinking blocks (they're disjoint in this shape).
        blocks: list[ContentBlock] = []
        seen_tool_ids: set[str] = set()
        for f in fragments:
            for b in f.content_blocks:
                if b.type == "tool_use" and b.id:
                    if b.id in seen_tool_ids:
                        continue
                    seen_tool_ids.add(b.id)
                blocks.append(b)
    else:
        # Streaming: the max-tokens fragment is the final cumulative
        # snapshot. Use its blocks as-is.
        blocks = list(winner.content_blocks)

    # Timestamp: first fragment's (matches JSONL insertion order; for
    # block-per-line all fragments are adjacent lines, so there's no
    # meaningful earlier/later distinction).
    return SessionMessage(
        type="assistant",
        timestamp=fragments[0].timestamp,
        message_id=fragments[0].message_id,
        model=fragments[0].model,
        content_blocks=blocks,
        usage=winner.usage,
    )


def merge_assistant_fragments(
    messages: list[SessionMessage],
) -> list[SessionMessage]:
    """Merge assistant-message fragments sharing a `message_id`.

    Claude Code emits a single assistant response as multiple JSONL lines
    — one per content block — all sharing the message's id. Legacy
    Claude Code versions emitted streaming snapshots (same shape, but
    each fragment is a cumulative snapshot with increasing
    ``output_tokens``). This function detects which shape a group
    follows and merges accordingly; see ``_merge_fragment_group`` for
    the shape-specific rules.

    Non-assistant messages and assistant messages without a
    ``message_id`` pass through unchanged.
    """
    # Build an ordered result list with ``None`` placeholders at each
    # position where a merged message will land; track fragments per
    # message_id in a parallel dict keyed by first-appearance order.
    result: list[SessionMessage | None] = []
    fragments_by_id: dict[str, list[SessionMessage]] = {}
    placeholder_ids: list[str] = []

    for msg in messages:
        if msg.type != "assistant" or not msg.message_id:
            result.append(msg)
            continue
        mid = msg.message_id
        if mid not in fragments_by_id:
            fragments_by_id[mid] = []
            result.append(None)
            placeholder_ids.append(mid)
        fragments_by_id[mid].append(msg)

    merged = [
        _merge_fragment_group(fragments_by_id[mid]) for mid in placeholder_ids
    ]
    merged_iter = iter(merged)
    return [next(merged_iter) if item is None else item for item in result]


# Backward-compatible alias; prefer ``merge_assistant_fragments`` at new
# call sites. The old name reflects the pre-#153 behavior when this
# function was a pick-one-per-message_id dedup.
deduplicate_messages = merge_assistant_fragments


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

    for line_num, data in iter_raw_messages(path):
        msg_type = data["type"]
        try:
            if msg_type == "user":
                messages.append(_parse_user_message(data))
            elif msg_type == "assistant":
                messages.append(_parse_assistant_message(data))
            else:
                logger.debug(
                    "Unknown message type '%s' at %s:%d",
                    msg_type, path.name, line_num,
                )
        except Exception:
            logger.warning(
                "Failed to parse message at %s:%d",
                path.name, line_num, exc_info=True,
            )
            continue

    if deduplicate:
        messages = merge_assistant_fragments(messages)

    return messages
