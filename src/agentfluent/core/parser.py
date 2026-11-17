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
        entrypoint=data.get("entrypoint"),
        metadata=metadata,
    )


def _extract_cache_ttl_split(usage_data: dict[str, Any]) -> tuple[int, int]:
    """Extract ``(5m, 1h)`` cache-write tokens from ``usage.cache_creation``.

    Returns ``(0, 0)`` when the optional sub-object is absent (older
    sessions). When it is present but its TTL buckets do not sum to the
    authoritative ``cache_creation_input_tokens`` total, that is a genuine
    data anomaly (a partial sub-object or a Claude Code format change that
    moved where 1h writes are reported) — log it at WARNING, because the
    silent fallback would re-bill those tokens at the cheaper 5m rate, the
    exact under-reporting #534 set out to fix. ``Usage`` itself reconciles
    the split against the total (see ``Usage._reconcile_cache_creation``).
    """
    sub = usage_data.get("cache_creation")
    if not isinstance(sub, dict):
        return 0, 0
    tokens_5m = sub.get("ephemeral_5m_input_tokens", 0) or 0
    tokens_1h = sub.get("ephemeral_1h_input_tokens", 0) or 0
    total = usage_data.get("cache_creation_input_tokens", 0) or 0
    if tokens_5m + tokens_1h != total:
        logger.warning(
            "cache_creation TTL split (5m=%d + 1h=%d) disagrees with "
            "cache_creation_input_tokens=%d; Usage will reconcile to keep "
            "5m + 1h == total (see #534)",
            tokens_5m, tokens_1h, total,
        )
    return tokens_5m, tokens_1h


def _parse_assistant_message(data: dict[str, Any]) -> SessionMessage:
    """Parse an 'assistant' type message."""
    message = data.get("message", {})
    usage_data = message.get("usage")

    usage = None
    if usage_data and isinstance(usage_data, dict):
        cache_5m, cache_1h = _extract_cache_ttl_split(usage_data)
        usage = Usage(
            input_tokens=usage_data.get("input_tokens", 0),
            output_tokens=usage_data.get("output_tokens", 0),
            cache_creation_input_tokens=usage_data.get(
                "cache_creation_input_tokens", 0,
            ),
            cache_read_input_tokens=usage_data.get("cache_read_input_tokens", 0),
            cache_creation_5m_input_tokens=cache_5m,
            cache_creation_1h_input_tokens=cache_1h,
        )

    return SessionMessage(
        type="assistant",
        timestamp=parse_timestamp(data.get("timestamp")),
        message_id=message.get("id"),
        model=message.get("model"),
        content_blocks=_normalize_content(message.get("content")),
        entrypoint=data.get("entrypoint"),
        usage=usage,
    )


def _pick_max_tokens_fragment(fragments: list[SessionMessage]) -> SessionMessage:
    """Return the fragment with the highest ``usage.output_tokens``.

    Treats missing usage as 0 tokens. Ties resolve to the first
    fragment with that value. ``_merge_fragment_group`` takes the merged
    message's ``usage`` from this fragment on both merge paths.
    """
    return max(
        fragments,
        key=lambda f: f.usage.output_tokens if f.usage else 0,
    )


def _block_extends(earlier: ContentBlock, later: ContentBlock) -> bool:
    """Whether ``later`` is a later snapshot of the same block as ``earlier``.

    Only two block types can match: ``text`` whose earlier text is non-empty
    and a prefix of the later text (a snapshot only grows), and ``tool_use``
    with the same non-null id. Every other type -- ``thinking`` (the parser
    stores no text for it), redacted thinking, unknown types -- never
    matches, so a group containing one at a compared position is unioned.
    """
    if earlier.type != later.type:
        return False
    if earlier.type == "text":
        return bool(earlier.text) and (later.text or "").startswith(
            earlier.text or "",
        )
    if earlier.type == "tool_use":
        return earlier.id is not None and earlier.id == later.id
    return False


def _is_cumulative_snapshot_group(fragments: list[SessionMessage]) -> bool:
    """Whether every fragment is a positional prefix-superset of the one before.

    True only for legacy streaming snapshots, where each line repeats the
    whole response so far. ``output_tokens`` plays no part: current
    block-per-line output also varies it from line to line (#694).
    """
    for earlier, later in zip(fragments, fragments[1:], strict=False):
        if len(later.content_blocks) < len(earlier.content_blocks):
            return False
        for earlier_block, later_block in zip(
            earlier.content_blocks, later.content_blocks, strict=False,
        ):
            if not _block_extends(earlier_block, later_block):
                return False
    return True


def _union_content_blocks(fragments: list[SessionMessage]) -> list[ContentBlock]:
    """Every block of every fragment in JSONL order; ``tool_use`` deduped by id.

    A repeated ``tool_use`` id keeps its first position but takes the latest
    block, so a later, more complete ``input`` wins -- the same "final input
    wins" outcome as the cumulative path. Text and thinking blocks are all
    kept (they are distinct blocks in the block-per-line shape).
    """
    blocks: list[ContentBlock] = []
    position_by_tool_id: dict[str, int] = {}
    for fragment in fragments:
        for block in fragment.content_blocks:
            if block.type == "tool_use" and block.id:
                if block.id in position_by_tool_id:
                    blocks[position_by_tool_id[block.id]] = block
                    continue
                position_by_tool_id[block.id] = len(blocks)
            blocks.append(block)
    return blocks


def _merge_fragment_group(fragments: list[SessionMessage]) -> SessionMessage:
    """Merge a list of assistant-message fragments that share a `message_id`.

    Two JSONL shapes appear under one message_id:

    **Block-per-line** (the current Claude Code shape; every multi-line
    message in the four corpora surveyed for #694). Each line carries one
    content block (thinking, text, or tool_use) and a running ``usage``
    snapshot, so ``output_tokens`` may be equal across the lines or may
    grow from line to line. Merge: union every content block in JSONL
    order, deduplicating ``tool_use`` by id (first position, latest block).
    Several parallel ``tool_use`` blocks commonly share one message.

    **Cumulative streaming snapshots** (legacy; not observed in current
    data, kept for robustness). Each line repeats the response so far.
    Detected by content shape, never by ``output_tokens``: every line must
    be a positional prefix-superset of the one before (see
    ``_block_extends``). Merge: keep the last line's blocks, the final
    snapshot, so a growing ``tool_use`` input keeps its final value.

    Anything that is not a clean cumulative chain is unioned -- dropping a
    block loses data, while a rare duplicate does not. Two accepted
    consequences of that rule (neither seen in the #694 corpus survey):

    - A legacy cumulative chain with a ``thinking`` block at a compared
      position is unioned, so its text and thinking blocks repeat once per
      snapshot. Its ``tool_use`` blocks still come out once each, with
      their final input.
    - Block-per-line ``text`` lines where each line's text is a prefix of
      the next look exactly like a legacy text snapshot, so only the last
      line's block is kept. No characters are lost (the dropped text is the
      start of the kept text); only the block boundary is.

    ``usage`` is taken whole from the max-``output_tokens`` fragment on
    both paths (all four fields from one record), so the merge cannot
    move a token or cost total. Timestamp, model and entrypoint come from
    the first fragment, so every merged block -- including parallel tool
    calls written on later lines -- carries the first line's timestamp.
    """
    if len(fragments) == 1:
        return fragments[0]

    if _is_cumulative_snapshot_group(fragments):
        blocks = list(fragments[-1].content_blocks)
    else:
        blocks = _union_content_blocks(fragments)

    return SessionMessage(
        type="assistant",
        timestamp=fragments[0].timestamp,
        message_id=fragments[0].message_id,
        model=fragments[0].model,
        content_blocks=blocks,
        entrypoint=fragments[0].entrypoint,
        usage=_pick_max_tokens_fragment(fragments).usage,
    )


def merge_assistant_fragments(
    messages: list[SessionMessage],
) -> list[SessionMessage]:
    """Merge assistant-message fragments sharing a `message_id`.

    Claude Code emits a single assistant response as multiple JSONL lines
    — one per content block — all sharing the message's id, with a
    running ``usage`` snapshot on each line. Legacy Claude Code versions
    emitted cumulative streaming snapshots instead, each line repeating
    the response so far. This function detects which shape a group
    follows from its content (never from ``output_tokens``) and merges
    accordingly; see ``_merge_fragment_group`` for the shape-specific
    rules.

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

    Fragment merging is applied by default: assistant lines sharing the
    same message.id are merged into one message. Its content is the union
    of the lines' blocks for block-per-line output, or the final snapshot's
    blocks for legacy cumulative snapshots; its usage is the record of the
    line with the highest output_tokens (see ``merge_assistant_fragments``).

    Args:
        path: Path to the .jsonl session file.
        deduplicate: Whether to merge assistant fragments sharing a
            message.id. Default True. False returns one message per line.

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
