"""Tests for streaming snapshot deduplication."""

from pathlib import Path

from agentfluent.core.parser import deduplicate_messages, parse_session
from agentfluent.core.session import ContentBlock, SessionMessage, Usage


class TestDeduplicateFromFixture:
    def test_reduces_duplicate_count(self, streaming_dupes_session_path: Path) -> None:
        messages = parse_session(streaming_dupes_session_path)

        # 2 user + 2 deduplicated assistant + 1 tool_result = 5
        assert len(messages) == 5
        assistant_msgs = [m for m in messages if m.type == "assistant"]
        assert len(assistant_msgs) == 2

    def test_keeps_highest_output_tokens(self, streaming_dupes_session_path: Path) -> None:
        messages = parse_session(streaming_dupes_session_path)
        assistant_msgs = [m for m in messages if m.type == "assistant"]

        # First group (msg_abc123): output_tokens should be 25 (highest of 2, 10, 25)
        assert assistant_msgs[0].usage is not None
        assert assistant_msgs[0].usage.output_tokens == 25

        # Second group (msg_def456): output_tokens should be 40 (highest of 5, 40)
        assert assistant_msgs[1].usage is not None
        assert assistant_msgs[1].usage.output_tokens == 40

    def test_preserves_input_tokens(self, streaming_dupes_session_path: Path) -> None:
        messages = parse_session(streaming_dupes_session_path)
        assistant_msgs = [m for m in messages if m.type == "assistant"]

        # input_tokens identical across dupes, should be preserved
        assert assistant_msgs[0].usage is not None
        assert assistant_msgs[0].usage.input_tokens == 100
        assert assistant_msgs[0].usage.cache_creation_input_tokens == 500

    def test_preserves_message_order(self, streaming_dupes_session_path: Path) -> None:
        messages = parse_session(streaming_dupes_session_path)
        types = [m.type for m in messages]
        assert types == ["user", "assistant", "user", "assistant", "tool_result"]

    def test_message_id_captured(self, streaming_dupes_session_path: Path) -> None:
        messages = parse_session(streaming_dupes_session_path)
        assistant_msgs = [m for m in messages if m.type == "assistant"]

        assert assistant_msgs[0].message_id == "msg_abc123"
        assert assistant_msgs[1].message_id == "msg_def456"

    def test_without_dedup_keeps_all(self, streaming_dupes_session_path: Path) -> None:
        messages = parse_session(streaming_dupes_session_path, deduplicate=False)
        assistant_msgs = [m for m in messages if m.type == "assistant"]
        # 3 dupes for msg_abc123 + 2 dupes for msg_def456 = 5
        assert len(assistant_msgs) == 5

    def test_content_from_best_version(self, streaming_dupes_session_path: Path) -> None:
        messages = parse_session(streaming_dupes_session_path)
        assistant_msgs = [m for m in messages if m.type == "assistant"]

        # Best version (highest output_tokens) should have the fullest content
        assert "How can I help?" in assistant_msgs[0].text


class TestDeduplicateConstructed:
    def test_no_assistant_messages(self) -> None:
        messages = [
            SessionMessage(type="user", content_blocks=[ContentBlock(type="text", text="Hi")]),
            SessionMessage(type="tool_result", tool_use_id="t1"),
        ]
        result = deduplicate_messages(messages)
        assert len(result) == 2

    def test_assistant_without_message_id(self) -> None:
        messages = [
            SessionMessage(
                type="assistant",
                message_id=None,
                usage=Usage(output_tokens=10),
            ),
            SessionMessage(
                type="assistant",
                message_id=None,
                usage=Usage(output_tokens=20),
            ),
        ]
        result = deduplicate_messages(messages)
        # Both pass through — no ID to dedup on
        assert len(result) == 2

    def test_mixed_with_and_without_ids(self) -> None:
        messages = [
            SessionMessage(type="user"),
            SessionMessage(
                type="assistant",
                message_id="msg_1",
                usage=Usage(output_tokens=5),
            ),
            SessionMessage(
                type="assistant",
                message_id="msg_1",
                usage=Usage(output_tokens=50),
            ),
            SessionMessage(
                type="assistant",
                message_id=None,
                usage=Usage(output_tokens=30),
            ),
            SessionMessage(type="tool_result", tool_use_id="t1"),
        ]
        result = deduplicate_messages(messages)
        # user + 1 deduped assistant (msg_1) + 1 no-id assistant + tool_result
        assert len(result) == 4
        assistant_msgs = [m for m in result if m.type == "assistant"]
        assert len(assistant_msgs) == 2
        assert assistant_msgs[0].usage is not None
        assert assistant_msgs[0].usage.output_tokens == 50

    def test_empty_input(self) -> None:
        assert deduplicate_messages([]) == []

    def test_single_assistant_no_dupes(self) -> None:
        messages = [
            SessionMessage(
                type="assistant",
                message_id="msg_unique",
                usage=Usage(output_tokens=100),
            ),
        ]
        result = deduplicate_messages(messages)
        assert len(result) == 1
        assert result[0].usage is not None
        assert result[0].usage.output_tokens == 100
