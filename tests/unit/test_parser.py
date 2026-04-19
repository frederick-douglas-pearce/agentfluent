"""Tests for JSONL session parser."""

import json
from pathlib import Path

from agentfluent.core.parser import parse_session
from agentfluent.core.session import SessionMessage


def _agent_result_messages(messages: list[SessionMessage]) -> list[SessionMessage]:
    return [
        m
        for m in messages
        if m.type == "user"
        and any(b.type == "tool_result" for b in m.content_blocks)
    ]


class TestParseBasicSession:
    def test_parses_user_and_assistant(self, basic_session_path: Path) -> None:
        messages = parse_session(basic_session_path)
        assert len(messages) == 4

        # First message: user with string content
        assert messages[0].type == "user"
        assert messages[0].text == "Analyze the project structure"

        # Second message: assistant with array content
        assert messages[1].type == "assistant"
        assert "look at the project structure" in messages[1].text
        assert messages[1].model == "claude-sonnet-4-20250514"
        assert messages[1].usage is not None
        assert messages[1].usage.input_tokens == 150

    def test_string_and_array_content(self, basic_session_path: Path) -> None:
        messages = parse_session(basic_session_path)

        # First user message has string content
        assert messages[0].text == "Analyze the project structure"

        # Second user message has array-of-blocks content
        assert messages[2].text == "Now check the config files"

    def test_cache_tokens_captured(self, basic_session_path: Path) -> None:
        messages = parse_session(basic_session_path)

        # First assistant has cache_creation
        assert messages[1].usage is not None
        assert messages[1].usage.cache_creation_input_tokens == 5000
        assert messages[1].usage.cache_read_input_tokens == 0

        # Second assistant has cache_read
        assert messages[3].usage is not None
        assert messages[3].usage.cache_read_input_tokens == 5000


class TestParseAgentSession:
    def test_extracts_agent_tool_use(self, agent_session_path: Path) -> None:
        messages = parse_session(agent_session_path)

        # Find assistant messages with Agent tool_use
        assistant_msgs = [m for m in messages if m.type == "assistant"]
        assert len(assistant_msgs) == 2

        # First assistant has an Agent tool_use
        tool_uses = assistant_msgs[0].tool_use_blocks
        assert len(tool_uses) == 1
        assert tool_uses[0].name == "Agent"
        assert tool_uses[0].input["subagent_type"] == "pm"

    def test_extracts_tool_result_metadata(self, agent_session_path: Path) -> None:
        agent_result_msgs = _agent_result_messages(parse_session(agent_session_path))
        assert len(agent_result_msgs) == 2

        first = agent_result_msgs[0]
        assert first.metadata is not None
        assert first.metadata.total_tokens == 31621
        assert first.metadata.tool_uses == 14
        assert first.metadata.duration_ms == 122963
        assert first.metadata.agent_id == "agent-abc123"

    def test_tool_result_content(self, agent_session_path: Path) -> None:
        agent_result_msgs = _agent_result_messages(parse_session(agent_session_path))
        first_result_block = next(
            b for b in agent_result_msgs[0].content_blocks if b.type == "tool_result"
        )
        assert first_result_block.text is not None
        assert "Created 5 issues" in first_result_block.text

    def test_tool_use_id_captured(self, agent_session_path: Path) -> None:
        agent_result_msgs = _agent_result_messages(parse_session(agent_session_path))
        first_block = next(
            b for b in agent_result_msgs[0].content_blocks if b.type == "tool_result"
        )
        second_block = next(
            b for b in agent_result_msgs[1].content_blocks if b.type == "tool_result"
        )
        assert first_block.tool_use_id == "toolu_01ABC123"
        assert second_block.tool_use_id == "toolu_01DEF456"


class TestParseToolCalls:
    def test_regular_tool_calls(self, tool_calls_session_path: Path) -> None:
        messages = parse_session(tool_calls_session_path)

        assistant_msgs = [m for m in messages if m.type == "assistant"]
        assert len(assistant_msgs) == 2

        # First assistant: Read tool
        tool_uses = assistant_msgs[0].tool_use_blocks
        assert len(tool_uses) == 1
        assert tool_uses[0].name == "Read"

        # Second assistant: Edit tool
        tool_uses = assistant_msgs[1].tool_use_blocks
        assert len(tool_uses) == 1
        assert tool_uses[0].name == "Edit"

    def test_tool_result_without_metadata(self, tool_calls_session_path: Path) -> None:
        messages = parse_session(tool_calls_session_path)
        tool_results = [m for m in messages if m.type == "tool_result"]

        # Regular tool results have no agent metadata
        for tr in tool_results:
            assert tr.metadata is None


class TestSkipTypes:
    def test_filters_non_analytical_types(self, skip_types_session_path: Path) -> None:
        messages = parse_session(skip_types_session_path)

        # Only user and assistant messages should survive
        types = {m.type for m in messages}
        assert types == {"user", "assistant"}

        # Should be exactly 2 messages (1 user + 1 assistant)
        assert len(messages) == 2

    def test_skipped_types_not_present(self, skip_types_session_path: Path) -> None:
        messages = parse_session(skip_types_session_path)
        types = {m.type for m in messages}

        assert "system" not in types
        assert "progress" not in types
        assert "hook_progress" not in types
        assert "bash_progress" not in types
        assert "file-history-snapshot" not in types
        assert "create" not in types


class TestMalformedInput:
    def test_skips_bad_lines(self, malformed_session_path: Path) -> None:
        messages = parse_session(malformed_session_path)

        # Should get 2 valid messages, skipping the malformed line
        assert len(messages) == 2
        assert messages[0].type == "user"
        assert messages[1].type == "assistant"

    def test_empty_file(self, empty_session_path: Path) -> None:
        messages = parse_session(empty_session_path)
        assert messages == []

    def test_nonexistent_file(self, tmp_path: Path) -> None:
        messages = parse_session(tmp_path / "does-not-exist.jsonl")
        assert messages == []


class TestToolUseResultOnUserMessage:
    """Parsing the real Claude Code shape: `toolUseResult` sibling to `message`
    on a user-type line, with camelCase fields."""

    def _write(self, tmp_path: Path, lines: list[dict]) -> Path:
        path = tmp_path / "session.jsonl"
        with path.open("w") as f:
            for line in lines:
                f.write(json.dumps(line) + "\n")
        return path

    def test_populates_metadata_from_tool_use_result(self, tmp_path: Path) -> None:
        """Positive test: all four camelCase fields map to snake_case internals."""
        path = self._write(
            tmp_path,
            [
                {
                    "type": "user",
                    "message": {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "toolu_real",
                                "content": "agent summary",
                            }
                        ],
                    },
                    "toolUseResult": {
                        "agentId": "uuid-123",
                        "agentType": "general-purpose",
                        "totalDurationMs": 122963,
                        "totalTokens": 31621,
                        "totalToolUseCount": 14,
                    },
                    "timestamp": "2026-04-14T08:02:13.000Z",
                },
            ],
        )
        messages = parse_session(path)
        assert len(messages) == 1
        msg = messages[0]
        assert msg.type == "user"
        assert msg.metadata is not None
        assert msg.metadata.total_tokens == 31621
        assert msg.metadata.tool_uses == 14
        assert msg.metadata.duration_ms == 122963
        assert msg.metadata.agent_id == "uuid-123"

    def test_tool_result_block_tool_use_id_captured(self, tmp_path: Path) -> None:
        """The tool_result content block keeps its tool_use_id for matching."""
        path = self._write(
            tmp_path,
            [
                {
                    "type": "user",
                    "message": {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "toolu_keepme",
                                "content": "result text",
                            }
                        ],
                    },
                    "toolUseResult": {"agentId": "u", "totalTokens": 1},
                },
            ],
        )
        messages = parse_session(path)
        block = messages[0].content_blocks[0]
        assert block.type == "tool_result"
        assert block.tool_use_id == "toolu_keepme"
        assert block.text == "result text"

    def test_extra_fields_ignored(self, tmp_path: Path) -> None:
        """Forward compatibility: unknown fields on toolUseResult do not raise."""
        path = self._write(
            tmp_path,
            [
                {
                    "type": "user",
                    "message": {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "toolu_future",
                                "content": "ok",
                            }
                        ],
                    },
                    "toolUseResult": {
                        "agentId": "u",
                        "totalTokens": 100,
                        "totalToolUseCount": 2,
                        "totalDurationMs": 500,
                        # Fields that don't exist in the model today but might
                        # be added by Claude Code in the future:
                        "futureField": "x",
                        "status": "success",
                        "prompt": "...",
                        "usage": {"input_tokens": 10},
                        "toolStats": {"Read": 1},
                    },
                },
            ],
        )
        # Should parse without raising ValidationError
        messages = parse_session(path)
        assert len(messages) == 1
        assert messages[0].metadata is not None
        assert messages[0].metadata.total_tokens == 100

    def test_user_message_without_tool_use_result(self, tmp_path: Path) -> None:
        """Regular user messages (no toolUseResult) have metadata=None."""
        path = self._write(
            tmp_path,
            [
                {
                    "type": "user",
                    "message": {"role": "user", "content": "hello"},
                    "timestamp": "2026-04-14T08:00:00.000Z",
                },
            ],
        )
        messages = parse_session(path)
        assert messages[0].metadata is None


class TestTimestamps:
    def test_timestamps_parsed(self, basic_session_path: Path) -> None:
        messages = parse_session(basic_session_path)

        assert messages[0].timestamp is not None
        assert messages[0].timestamp.year == 2026
        assert messages[0].timestamp.month == 4

    def test_all_messages_have_timestamps(self, agent_session_path: Path) -> None:
        """Real-shape agent results (user messages with toolUseResult) carry
        timestamps just like any other user/assistant message."""
        messages = parse_session(agent_session_path)
        for msg in messages:
            assert msg.timestamp is not None, f"{msg.type} message missing timestamp"
