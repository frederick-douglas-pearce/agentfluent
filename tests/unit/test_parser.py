"""Tests for JSONL session parser."""

import json
from pathlib import Path

import pytest

from agentfluent.core.parser import iter_raw_messages, parse_session
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


class TestParseWarningsToStderr:
    """Issue #206: parse warnings must reach stderr with a `WARNING:` prefix
    and include the truncated offending line so the user can decide whether
    to investigate. Stdout must stay clean for piping/redirection.
    """

    def test_malformed_json_writes_to_stderr_with_prefix(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        path = tmp_path / "session-uuid.jsonl"
        path.write_text(
            '{"type": "user"}\n'
            'this line is not valid json at all\n'
            '{"type": "assistant"}\n',
        )

        parse_session(path)
        captured = capsys.readouterr()

        assert captured.out == ""
        assert captured.err.startswith("WARNING:")
        assert "Malformed JSON" in captured.err
        assert "session-uuid.jsonl:2" in captured.err
        assert "this line is not valid json at all" in captured.err

    def test_non_object_json_writes_to_stderr_with_prefix(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        path = tmp_path / "arr.jsonl"
        path.write_text('{"type": "user"}\n["not", "an", "object"]\n')

        parse_session(path)
        captured = capsys.readouterr()

        assert captured.out == ""
        assert "WARNING: Non-object JSON" in captured.err
        assert "arr.jsonl:2" in captured.err
        assert '["not", "an", "object"]' in captured.err

    def test_long_line_truncated_to_100_chars(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        long_garbage = "x" * 500
        path = tmp_path / "long.jsonl"
        path.write_text(long_garbage + "\n")

        parse_session(path)
        captured = capsys.readouterr()

        assert "..." in captured.err
        # Header text plus 100 chars of payload plus the ellipsis; the raw
        # 500-char line must not appear in full.
        assert long_garbage not in captured.err
        assert ("x" * 100) in captured.err
        assert ("x" * 101) not in captured.err

    def test_no_warning_for_clean_file(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        path = tmp_path / "clean.jsonl"
        path.write_text('{"type": "user"}\n{"type": "assistant"}\n')

        parse_session(path)
        captured = capsys.readouterr()

        assert captured.out == ""
        assert captured.err == ""


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

    def test_is_error_flows_from_normalize_content(self, tmp_path: Path) -> None:
        """The `is_error` field on a tool_result JSONL block propagates to ContentBlock."""
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
                                "tool_use_id": "toolu_err",
                                "content": "permission denied",
                                "is_error": True,
                            },
                            {
                                "type": "tool_result",
                                "tool_use_id": "toolu_ok",
                                "content": "42",
                                "is_error": False,
                            },
                            {
                                "type": "tool_result",
                                "tool_use_id": "toolu_missing",
                                "content": "no flag",
                            },
                        ],
                    },
                    "timestamp": "2026-04-14T08:00:00.000Z",
                },
            ],
        )
        messages = parse_session(path)
        blocks = messages[0].content_blocks
        by_id = {b.tool_use_id: b for b in blocks}
        assert by_id["toolu_err"].is_error is True
        assert by_id["toolu_ok"].is_error is False
        assert by_id["toolu_missing"].is_error is None


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


class TestIterRawMessages:
    """Direct tests for the shared iter_raw_messages iterator.

    The subagent trace parser (#103) consumes this iterator, so its
    contract is part of the public surface.
    """

    def test_missing_file_yields_nothing(self, tmp_path: Path) -> None:
        assert list(iter_raw_messages(tmp_path / "nope.jsonl")) == []

    def test_empty_file_yields_nothing(self, tmp_path: Path) -> None:
        path = tmp_path / "empty.jsonl"
        path.write_text("")
        assert list(iter_raw_messages(path)) == []

    def test_skips_empty_lines(self, tmp_path: Path) -> None:
        path = tmp_path / "s.jsonl"
        path.write_text('\n{"type": "user"}\n\n{"type": "assistant"}\n\n')
        types = [d["type"] for _, d in iter_raw_messages(path)]
        assert types == ["user", "assistant"]

    def test_skips_malformed_json(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.jsonl"
        path.write_text('{"type": "user"}\nnot json\n{"type": "assistant"}\n')
        types = [d["type"] for _, d in iter_raw_messages(path)]
        assert types == ["user", "assistant"]

    def test_skips_non_object_json(self, tmp_path: Path) -> None:
        path = tmp_path / "arr.jsonl"
        path.write_text('{"type": "user"}\n["array"]\n42\n{"type": "assistant"}\n')
        types = [d["type"] for _, d in iter_raw_messages(path)]
        assert types == ["user", "assistant"]

    def test_filters_skip_types(self, tmp_path: Path) -> None:
        path = tmp_path / "skip.jsonl"
        lines = [
            {"type": "user"},
            {"type": "file-history-snapshot"},
            {"type": "progress"},
            {"type": "hook_progress"},
            {"type": "bash_progress"},
            {"type": "system"},
            {"type": "create"},
            {"type": "assistant"},
        ]
        path.write_text("\n".join(json.dumps(ln) for ln in lines) + "\n")
        types = [d["type"] for _, d in iter_raw_messages(path)]
        assert types == ["user", "assistant"]

    def test_skips_missing_type(self, tmp_path: Path) -> None:
        path = tmp_path / "notype.jsonl"
        path.write_text('{"foo": "bar"}\n{"type": ""}\n{"type": "user"}\n')
        types = [d["type"] for _, d in iter_raw_messages(path)]
        assert types == ["user"]

    def test_yields_raw_dicts_with_all_fields(self, tmp_path: Path) -> None:
        path = tmp_path / "raw.jsonl"
        entry = {
            "type": "user",
            "timestamp": "2026-04-20T10:00:00Z",
            "message": {"role": "user", "content": "hello"},
            "toolUseResult": {"status": "success", "custom_field": 42},
        }
        path.write_text(json.dumps(entry) + "\n")
        [(line_num, result)] = list(iter_raw_messages(path))
        assert result == entry
        assert line_num == 1

    def test_line_num_reflects_raw_line_position(self, tmp_path: Path) -> None:
        """line_num is the raw file position; skipped lines still advance it."""
        path = tmp_path / "mixed.jsonl"
        path.write_text(
            "\n"                            # line 1: empty, skipped
            "not json\n"                    # line 2: malformed, skipped
            '{"type": "progress"}\n'        # line 3: SKIP_TYPES, skipped
            '{"type": "user"}\n'            # line 4: yielded
            '{"type": "assistant"}\n',      # line 5: yielded
        )
        pairs = list(iter_raw_messages(path))
        assert [line_num for line_num, _ in pairs] == [4, 5]
