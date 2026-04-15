"""Tests for JSONL session parser."""

from pathlib import Path

from agentfluent.core.parser import parse_session


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
        messages = parse_session(agent_session_path)

        tool_results = [m for m in messages if m.type == "tool_result"]
        assert len(tool_results) == 2

        # First tool_result has agent metadata
        assert tool_results[0].metadata is not None
        assert tool_results[0].metadata.total_tokens == 31621
        assert tool_results[0].metadata.tool_uses == 14
        assert tool_results[0].metadata.duration_ms == 122963
        assert tool_results[0].metadata.agent_id == "agent-abc123"

    def test_tool_result_content(self, agent_session_path: Path) -> None:
        messages = parse_session(agent_session_path)
        tool_results = [m for m in messages if m.type == "tool_result"]

        assert "Created 5 issues" in tool_results[0].text

    def test_tool_use_id_captured(self, agent_session_path: Path) -> None:
        messages = parse_session(agent_session_path)
        tool_results = [m for m in messages if m.type == "tool_result"]

        assert tool_results[0].tool_use_id == "toolu_01ABC123"
        assert tool_results[1].tool_use_id == "toolu_01DEF456"


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


class TestTimestamps:
    def test_timestamps_parsed(self, basic_session_path: Path) -> None:
        messages = parse_session(basic_session_path)

        assert messages[0].timestamp is not None
        assert messages[0].timestamp.year == 2026
        assert messages[0].timestamp.month == 4

    def test_tool_result_no_timestamp(self, agent_session_path: Path) -> None:
        messages = parse_session(agent_session_path)
        tool_results = [m for m in messages if m.type == "tool_result"]

        # Our fixture tool_results don't have timestamps
        assert tool_results[0].timestamp is None
