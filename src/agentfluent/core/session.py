"""Data models for parsed JSONL session messages.

These models are the contract between the parser and all downstream consumers
(analytics, agent extraction, diagnostics). They normalize the varying JSONL
formats into a consistent structure.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class Usage(BaseModel):
    """Token usage from an assistant message."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_creation_input_tokens
            + self.cache_read_input_tokens
        )


class ToolUseBlock(BaseModel):
    """A tool_use content block from an assistant message."""

    id: str
    name: str
    input: dict[str, Any] = Field(default_factory=dict)


class ContentBlock(BaseModel):
    """A single content block (text or tool_use) from a message.

    The raw JSONL content can be either a plain string or an array of typed blocks.
    The parser normalizes both forms into a list of ContentBlock.
    """

    type: str  # "text" or "tool_use"
    text: str | None = None
    # tool_use fields (only present when type == "tool_use")
    id: str | None = None
    name: str | None = None
    input: dict[str, Any] | None = None


class ToolResultMetadata(BaseModel):
    """Metadata from a tool_result message (present on agent invocation results)."""

    model_config = {"populate_by_name": True}

    total_tokens: int | None = None
    tool_uses: int | None = None
    duration_ms: int | None = None
    agent_id: str | None = Field(None, alias="agentId")


class SessionMessage(BaseModel):
    """A single parsed message from a JSONL session file.

    This is the primary unit of parsed data. The parser produces a list of these,
    and all downstream consumers (analytics, extraction, diagnostics) work with them.
    """

    type: str
    """Message type: 'user', 'assistant', or 'tool_result'."""

    timestamp: datetime | None = None
    """When the message was recorded. May be absent on tool_result messages."""

    # Content fields
    content_blocks: list[ContentBlock] = Field(default_factory=list)
    """Normalized content. For user/assistant messages, text and tool_use blocks.
    For tool_result, a single text block with the result content."""

    # Assistant-specific fields
    message_id: str | None = None
    """Anthropic message ID (e.g., 'msg_...'). Used to deduplicate streaming snapshots.
    All snapshots for the same API call share the same message_id."""

    model: str | None = None
    """Model name (e.g., 'claude-opus-4-6'). Only on assistant messages."""

    usage: Usage | None = None
    """Token usage. Only on assistant messages."""

    # tool_result-specific fields
    tool_use_id: str | None = None
    """The tool_use ID this result corresponds to. Only on tool_result messages."""

    is_error: bool = False
    """Whether the tool result is an error. Only on tool_result messages."""

    metadata: ToolResultMetadata | None = None
    """Agent invocation metadata. Only on tool_result messages from Agent calls."""

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
