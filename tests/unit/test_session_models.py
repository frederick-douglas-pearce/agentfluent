"""Tests for core session data models."""

from datetime import UTC, datetime

from agentfluent.core.session import (
    SKIP_TYPES,
    ContentBlock,
    SessionMessage,
    ToolResultMetadata,
    ToolUseBlock,
    Usage,
)


class TestUsage:
    def test_total_tokens(self) -> None:
        usage = Usage(
            input_tokens=150,
            output_tokens=25,
            cache_creation_input_tokens=5000,
            cache_read_input_tokens=0,
        )
        assert usage.total_tokens == 5175

    def test_defaults_to_zero(self) -> None:
        usage = Usage()
        assert usage.total_tokens == 0


class TestToolUseBlock:
    def test_basic(self) -> None:
        block = ToolUseBlock(id="toolu_01ABC", name="Read", input={"file_path": "/tmp/test"})
        assert block.id == "toolu_01ABC"
        assert block.name == "Read"
        assert block.input["file_path"] == "/tmp/test"

    def test_empty_input(self) -> None:
        block = ToolUseBlock(id="toolu_01", name="Bash")
        assert block.input == {}


class TestContentBlock:
    def test_text_block(self) -> None:
        block = ContentBlock(type="text", text="Hello world")
        assert block.text == "Hello world"
        assert block.name is None

    def test_tool_use_block(self) -> None:
        block = ContentBlock(
            type="tool_use",
            id="toolu_01",
            name="Agent",
            input={"subagent_type": "pm"},
        )
        assert block.type == "tool_use"
        assert block.name == "Agent"

    def test_tool_result_is_error_field(self) -> None:
        default = ContentBlock(type="tool_result", tool_use_id="toolu_01")
        assert default.is_error is None

        flagged = ContentBlock(
            type="tool_result", tool_use_id="toolu_01", is_error=True,
        )
        assert flagged.is_error is True

        ok = ContentBlock(
            type="tool_result", tool_use_id="toolu_01", is_error=False,
        )
        assert ok.is_error is False


class TestToolResultMetadata:
    def test_from_json_with_alias(self) -> None:
        """agentId in JSONL maps to agent_id in Python."""
        meta = ToolResultMetadata.model_validate(
            {"total_tokens": 31621, "tool_uses": 14, "duration_ms": 122963, "agentId": "abc123"}
        )
        assert meta.agent_id == "abc123"
        assert meta.total_tokens == 31621

    def test_all_optional(self) -> None:
        meta = ToolResultMetadata()
        assert meta.agent_id is None
        assert meta.total_tokens is None

    def test_python_field_name(self) -> None:
        """Can also construct with the Python field name."""
        meta = ToolResultMetadata(agent_id="xyz")
        assert meta.agent_id == "xyz"


class TestSessionMessage:
    def test_user_message_string_content(self) -> None:
        """User message with plain string content."""
        msg = SessionMessage(
            type="user",
            timestamp=datetime(2026, 4, 10, 10, 0, tzinfo=UTC),
            content_blocks=[ContentBlock(type="text", text="Hello")],
        )
        assert msg.type == "user"
        assert msg.text == "Hello"
        assert msg.tool_use_blocks == []

    def test_user_message_array_content(self) -> None:
        """User message with array-of-blocks content."""
        msg = SessionMessage(
            type="user",
            content_blocks=[
                ContentBlock(type="text", text="First part"),
                ContentBlock(type="text", text="Second part"),
            ],
        )
        assert msg.text == "First part\nSecond part"

    def test_assistant_message_with_tool_use(self) -> None:
        msg = SessionMessage(
            type="assistant",
            model="claude-opus-4-6",
            usage=Usage(input_tokens=3000, output_tokens=150),
            content_blocks=[
                ContentBlock(type="text", text="I'll delegate this."),
                ContentBlock(
                    type="tool_use",
                    id="toolu_01ABC",
                    name="Agent",
                    input={"subagent_type": "pm", "prompt": "Do the thing"},
                ),
            ],
        )
        assert msg.text == "I'll delegate this."
        assert len(msg.tool_use_blocks) == 1
        assert msg.tool_use_blocks[0].name == "Agent"
        assert msg.tool_use_blocks[0].input["subagent_type"] == "pm"
        assert msg.usage is not None
        assert msg.usage.input_tokens == 3000

    def test_user_message_carries_tool_use_result_metadata(self) -> None:
        """Real Claude Code shape: tool_result is a content block inside a
        user message, and `toolUseResult` metadata rides on the user message."""
        msg = SessionMessage(
            type="user",
            content_blocks=[
                ContentBlock(
                    type="tool_result",
                    tool_use_id="toolu_01ABC",
                    text="Created 5 issues.",
                ),
            ],
            metadata=ToolResultMetadata(
                total_tokens=31621,
                tool_uses=14,
                duration_ms=122963,
                agent_id="agent-abc123",
            ),
        )
        assert msg.type == "user"
        assert msg.text == ""  # tool_result blocks don't contribute to .text
        assert msg.content_blocks[0].tool_use_id == "toolu_01ABC"
        assert msg.content_blocks[0].text == "Created 5 issues."
        assert msg.metadata is not None
        assert msg.metadata.agent_id == "agent-abc123"

    def test_empty_content(self) -> None:
        msg = SessionMessage(type="user")
        assert msg.text == ""
        assert msg.tool_use_blocks == []

    def test_optional_fields_default(self) -> None:
        msg = SessionMessage(type="user")
        assert msg.timestamp is None
        assert msg.model is None
        assert msg.usage is None
        assert msg.metadata is None


class TestSkipTypes:
    def test_expected_types(self) -> None:
        expected = {
            "file-history-snapshot",
            "progress",
            "hook_progress",
            "bash_progress",
            "system",
            "create",
        }
        assert SKIP_TYPES == expected

    def test_analytical_types_not_skipped(self) -> None:
        assert "user" not in SKIP_TYPES
        assert "assistant" not in SKIP_TYPES
