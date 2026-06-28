"""Data models for parsed JSONL session messages.

These models are the contract between the parser and all downstream consumers
(analytics, agent extraction, diagnostics). They normalize the varying JSONL
formats into a consistent structure.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, model_validator


class Usage(BaseModel):
    """Token usage from an assistant message.

    ``cache_creation_input_tokens`` is the authoritative total of cache-write
    tokens (used by ``total_tokens`` and all display/efficiency math). The
    ``5m``/``1h`` fields are the TTL split of that total, supplied by the
    parser from ``usage.cache_creation`` for cost attribution (see #534).

    The invariant ``5m + 1h == cache_creation_input_tokens`` is enforced by
    ``_reconcile_cache_creation`` on *every* ``Usage``, regardless of how it
    was built — the parser, ``__add__``, a fixture, or a future caller. Cost
    attribution prices the split, so this guarantees the priced buckets can
    never silently diverge from the displayed total (e.g. a usage carrying a
    total with a zero split would otherwise price its cache writes at $0).
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_5m_input_tokens: int = 0
    cache_creation_1h_input_tokens: int = 0

    @model_validator(mode="after")
    def _reconcile_cache_creation(self) -> Usage:
        """Keep the TTL split consistent with the authoritative total.

        ``cache_creation_input_tokens`` and the ``5m``/``1h`` split may be
        supplied independently. This reconciles them so every ``Usage`` is
        internally consistent (#534):

        - ``total > split``: the unaccounted remainder — a legacy total-only
          usage, or a partial ``usage.cache_creation`` sub-object — is
          attributed to the cheaper 5-minute bucket.
        - ``total < split``: the split is more complete than the stated total
          (or the total was absent), so the total is raised to match. This
          guards against a negative 5m bucket when the sub-object reports more
          than the top-level sum.
        """
        split = (
            self.cache_creation_5m_input_tokens
            + self.cache_creation_1h_input_tokens
        )
        if self.cache_creation_input_tokens > split:
            self.cache_creation_5m_input_tokens += (
                self.cache_creation_input_tokens - split
            )
        elif self.cache_creation_input_tokens < split:
            self.cache_creation_input_tokens = split
        return self

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_creation_input_tokens
            + self.cache_read_input_tokens
        )

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_creation_input_tokens=(
                self.cache_creation_input_tokens + other.cache_creation_input_tokens
            ),
            cache_read_input_tokens=(
                self.cache_read_input_tokens + other.cache_read_input_tokens
            ),
            cache_creation_5m_input_tokens=(
                self.cache_creation_5m_input_tokens
                + other.cache_creation_5m_input_tokens
            ),
            cache_creation_1h_input_tokens=(
                self.cache_creation_1h_input_tokens
                + other.cache_creation_1h_input_tokens
            ),
        )

    __radd__ = __add__  # lets sum() with a Usage() start value work cleanly


class ToolUseBlock(BaseModel):
    """A tool_use content block from an assistant message."""

    id: str
    name: str
    input: dict[str, Any] = Field(default_factory=dict)


class ContentBlock(BaseModel):
    """A single content block (text, tool_use, or tool_result) from a message.

    The raw JSONL content can be either a plain string or an array of typed blocks.
    The parser normalizes both forms into a list of ContentBlock.
    """

    type: str  # "text", "tool_use", or "tool_result"
    text: str | None = None
    # tool_use fields (only present when type == "tool_use")
    id: str | None = None
    name: str | None = None
    input: dict[str, Any] | None = None
    # tool_result fields (only present when type == "tool_result")
    tool_use_id: str | None = None
    is_error: bool | None = None


class ToolResultMetadata(BaseModel):
    """Metadata from a user message's `toolUseResult` sibling (agent invocation results).

    Claude Code emits these fields as camelCase on the outer user message's
    `toolUseResult` key. Internal snake_case field names are preserved as the
    stable downstream contract; the camelCase aliases handle JSONL ingestion.

    `extra="ignore"` keeps parsing forward-compatible: additional fields on
    `toolUseResult` (e.g. `status`, `prompt`, `usage`, `toolStats`) are
    silently dropped rather than raising a ValidationError.
    """

    model_config = {"populate_by_name": True, "extra": "ignore"}

    total_tokens: int | None = Field(None, alias="totalTokens")
    tool_uses: int | None = Field(None, alias="totalToolUseCount")
    duration_ms: int | None = Field(None, alias="totalDurationMs")
    agent_id: str | None = Field(None, alias="agentId")
    tool_stats: dict[str, int] | None = Field(None, alias="toolStats")
    """Per-tool invocation counts keyed by tool name (e.g.
    ``{"Read": 3, "Bash": 1}``). ``None`` when ``toolStats`` is absent
    from the result — distinct from an empty dict so consumers can tell
    "no data" apart from "ran but recorded no tool calls". Keys are the
    source of observed tool *diversity* (vs ``tool_uses``, which is only
    a count). Used by the ``tool_inventory_oversized`` audit (#372)."""


class SessionMessage(BaseModel):
    """A single parsed message from a JSONL session file.

    This is the primary unit of parsed data. The parser produces a list of these,
    and all downstream consumers (analytics, extraction, diagnostics) work with them.
    """

    type: str
    """Message type: 'user' or 'assistant'."""

    timestamp: datetime | None = None
    """When the message was recorded."""

    content_blocks: list[ContentBlock] = Field(default_factory=list)
    """Normalized content: text, tool_use, and tool_result blocks."""

    message_id: str | None = None
    """Anthropic message ID (e.g., 'msg_...'). Used to deduplicate streaming snapshots.
    All snapshots for the same API call share the same message_id."""

    model: str | None = None
    """Model name (e.g., 'claude-opus-4-6'). Only on assistant messages."""

    usage: Usage | None = None
    """Token usage. Only on assistant messages."""

    metadata: ToolResultMetadata | None = None
    """Agent invocation metadata. Populated on `user`-type messages that carry
    a top-level `toolUseResult` key (the real Claude Code shape for Agent
    tool results)."""

    @property
    def text(self) -> str:
        """Extract concatenated text content from all text blocks."""
        parts = [b.text for b in self.content_blocks if b.type == "text" and b.text]
        return "\n".join(parts)

    @property
    def tool_use_blocks(self) -> list[ToolUseBlock]:
        """Extract tool_use blocks from content."""
        return [
            ToolUseBlock(id=b.id or "", name=b.name or "", input=b.input or {})
            for b in self.content_blocks
            if b.type == "tool_use" and b.name
        ]


def index_tool_results_by_id(
    messages: list[SessionMessage],
) -> dict[str, tuple[SessionMessage, str, bool | None]]:
    """Build a ``tool_use_id → (containing_message, text, is_error)`` index.

    Extracted as a shared helper because multiple consumers walk the
    same user-message → content-block → tool_result path:

    - ``agents/extractor.py`` pairs each Agent ``tool_use`` with its
      result to pull ``toolUseResult`` metadata off the container.
    - ``diagnostics/mcp_assessment.py`` pairs each MCP ``tool_use``
      with its result to determine ``is_error``.

    Returning the container alongside text and is_error lets each
    caller pick what it needs without re-walking messages.
    """
    results: dict[str, tuple[SessionMessage, str, bool | None]] = {}
    for msg in messages:
        if msg.type != "user":
            continue
        for block in msg.content_blocks:
            if block.type == "tool_result" and block.tool_use_id:
                results[block.tool_use_id] = (
                    msg, block.text or "", block.is_error,
                )
    return results


# Message types that the parser should skip
SKIP_TYPES: frozenset[str] = frozenset(
    {
        "file-history-snapshot",
        "progress",
        "hook_progress",
        "bash_progress",
        "system",
        "create",
    }
)
