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
        assert types == ["user", "assistant", "user", "assistant", "user"]

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
            SessionMessage(type="user"),
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
            SessionMessage(type="user"),
        ]
        result = deduplicate_messages(messages)
        # user + 1 deduped assistant (msg_1) + 1 no-id assistant + user
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


class TestBlockPerLineMerge:
    """Current Claude Code JSONL emits each content block on its own
    line, all sharing message_id and output_tokens. The merge path must
    UNION every content block rather than pick one. See #153."""

    def test_content_blocks_unioned_across_fragments(
        self, block_per_line_session_path: Path,
    ) -> None:
        messages = parse_session(block_per_line_session_path)
        assistant_msgs = [m for m in messages if m.type == "assistant"]
        # 5 JSONL lines fragment into one logical assistant message.
        assert len(assistant_msgs) == 1
        # Fixture contains: 1 thinking + 1 text + 2 tool_use blocks.
        blocks_by_type = {}
        for b in assistant_msgs[0].content_blocks:
            blocks_by_type.setdefault(b.type, []).append(b)
        assert len(blocks_by_type["thinking"]) == 1
        assert len(blocks_by_type["text"]) == 1
        assert len(blocks_by_type["tool_use"]) == 2

    def test_both_tool_use_ids_present(
        self, block_per_line_session_path: Path,
    ) -> None:
        messages = parse_session(block_per_line_session_path)
        assistant = next(m for m in messages if m.type == "assistant")
        # Tool-use blocks carry their id on `ContentBlock.id`.
        tool_use_ids = {
            b.id for b in assistant.content_blocks
            if b.type == "tool_use" and b.id
        }
        assert tool_use_ids == {"toolu_01PmCall", "toolu_02ExploreCall"}

    def test_usage_not_double_counted(
        self, block_per_line_session_path: Path,
    ) -> None:
        # All 5 fragments report output_tokens=420. The merge must carry
        # 420 forward, NOT 5*420=2100.
        messages = parse_session(block_per_line_session_path)
        assistant = next(m for m in messages if m.type == "assistant")
        assert assistant.usage is not None
        assert assistant.usage.output_tokens == 420
        assert assistant.usage.input_tokens == 1500

    def test_merge_preserves_message_order(
        self, block_per_line_session_path: Path,
    ) -> None:
        # Expected structure: user, merged assistant, user (pm result),
        # user (explore result). The merged assistant collapses 5 input
        # lines into 1, preserving its position at index 1.
        messages = parse_session(block_per_line_session_path)
        assert [m.type for m in messages] == ["user", "assistant", "user", "user"]

    def test_constructed_block_per_line_merge(self) -> None:
        # Explicit constructor form: three fragments of the same message,
        # each with one block, all with matching output_tokens.
        frags = [
            SessionMessage(
                type="assistant", message_id="m",
                content_blocks=[ContentBlock(type="text", text="hello")],
                usage=Usage(output_tokens=100),
            ),
            SessionMessage(
                type="assistant", message_id="m",
                content_blocks=[
                    ContentBlock(type="tool_use", id="t1", name="Bash"),
                ],
                usage=Usage(output_tokens=100),
            ),
            SessionMessage(
                type="assistant", message_id="m",
                content_blocks=[
                    ContentBlock(type="tool_use", tool_use_id="t2", name="Read"),
                ],
                usage=Usage(output_tokens=100),
            ),
        ]
        result = deduplicate_messages(frags)
        assert len(result) == 1
        merged = result[0]
        assert len(merged.content_blocks) == 3
        types = [b.type for b in merged.content_blocks]
        assert types == ["text", "tool_use", "tool_use"]
        assert merged.usage is not None
        assert merged.usage.output_tokens == 100

    def test_duplicate_tool_use_ids_in_fragments_are_deduped(self) -> None:
        # Defensive: if the same tool_use_id appears in two fragments of
        # the same message (shouldn't normally happen, but guard against
        # it), the merge keeps one.
        frags = [
            SessionMessage(
                type="assistant", message_id="m",
                content_blocks=[
                    ContentBlock(type="tool_use", id="t1", name="Bash"),
                ],
                usage=Usage(output_tokens=50),
            ),
            SessionMessage(
                type="assistant", message_id="m",
                content_blocks=[
                    ContentBlock(type="tool_use", id="t1", name="Bash"),
                ],
                usage=Usage(output_tokens=50),
            ),
        ]
        result = deduplicate_messages(frags)
        assert len(result) == 1
        assert len(result[0].content_blocks) == 1

    def test_fragments_with_missing_usage_still_merge(self) -> None:
        # Edge case: one fragment has no usage metadata. Should still
        # merge cleanly without crashing.
        frags = [
            SessionMessage(
                type="assistant", message_id="m",
                content_blocks=[ContentBlock(type="text", text="a")],
                usage=Usage(output_tokens=50),
            ),
            SessionMessage(
                type="assistant", message_id="m",
                content_blocks=[ContentBlock(type="text", text="b")],
                usage=None,
            ),
        ]
        result = deduplicate_messages(frags)
        assert len(result) == 1
        assert len(result[0].content_blocks) == 2
