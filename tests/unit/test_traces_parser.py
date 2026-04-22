"""Tests for the subagent trace parser."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from agentfluent.traces.models import (
    INPUT_SUMMARY_MAX_CHARS,
    RESULT_SUMMARY_MAX_CHARS,
    UNKNOWN_AGENT_TYPE,
    SubagentTrace,
)
from agentfluent.traces.parser import parse_subagent_trace
from tests._builders import (
    assistant_message as _assistant,
)
from tests._builders import (
    tool_result_block as _tool_result,
)
from tests._builders import (
    tool_use_block as _tool_use,
)
from tests._builders import (
    user_message as _user,
)

WriteJSONL = Callable[[str, list[dict[str, Any]]], Path]


class TestParseSubagentTrace:
    def test_happy_path(self, write_jsonl: WriteJSONL) -> None:
        path = write_jsonl(
            "agent-uuid-1.jsonl",
            [
                _user("Review the backlog"),
                _assistant(
                    [_tool_use("t1", "Read", {"file_path": "/tmp/notes.md"})],
                    usage={"input_tokens": 10, "output_tokens": 5},
                ),
                _user([_tool_result("t1", "notes content")]),
            ],
        )
        trace = parse_subagent_trace(path)

        assert isinstance(trace, SubagentTrace)
        assert trace.agent_id == "uuid-1"
        assert trace.agent_type == UNKNOWN_AGENT_TYPE
        assert trace.delegation_prompt == "Review the backlog"
        assert len(trace.tool_calls) == 1
        assert trace.tool_calls[0].tool_name == "Read"
        assert trace.tool_calls[0].result_summary == "notes content"
        assert trace.total_errors == 0
        assert trace.total_retries == 0
        assert trace.retry_sequences == []
        assert trace.usage.input_tokens == 10

    def test_agent_id_from_filename(self, write_jsonl: WriteJSONL) -> None:
        path = write_jsonl("agent-abc-123-def.jsonl", [_user("hi")])
        assert parse_subagent_trace(path).agent_id == "abc-123-def"

    def test_agent_type_is_unknown(self, write_jsonl: WriteJSONL) -> None:
        path = write_jsonl("agent-x.jsonl", [_user("hi")])
        assert parse_subagent_trace(path).agent_type == UNKNOWN_AGENT_TYPE

    def test_source_file_resolved(self, write_jsonl: WriteJSONL) -> None:
        path = write_jsonl("agent-x.jsonl", [_user("hi")])
        trace = parse_subagent_trace(path)
        assert trace.source_file == path.resolve()
        assert trace.source_file.is_absolute()


class TestDelegationPrompt:
    def test_string_content(self, write_jsonl: WriteJSONL) -> None:
        path = write_jsonl("agent-x.jsonl", [_user("the prompt")])
        assert parse_subagent_trace(path).delegation_prompt == "the prompt"

    def test_list_of_blocks_content(self, write_jsonl: WriteJSONL) -> None:
        path = write_jsonl(
            "agent-x.jsonl",
            [_user([{"type": "text", "text": "line1"}, {"type": "text", "text": "line2"}])],
        )
        assert parse_subagent_trace(path).delegation_prompt == "line1\nline2"

    def test_no_user_messages(self, write_jsonl: WriteJSONL) -> None:
        path = write_jsonl(
            "agent-x.jsonl", [_assistant([{"type": "text", "text": "hi"}])],
        )
        assert parse_subagent_trace(path).delegation_prompt == ""


class TestToolCallPairing:
    def test_single_tool_use(self, write_jsonl: WriteJSONL) -> None:
        path = write_jsonl(
            "agent-x.jsonl",
            [
                _user("go"),
                _assistant([_tool_use("t1", "Bash", {"cmd": "ls"})]),
                _user([_tool_result("t1", "file.txt")]),
            ],
        )
        trace = parse_subagent_trace(path)
        assert len(trace.tool_calls) == 1
        assert trace.tool_calls[0].tool_name == "Bash"

    def test_multiple_tool_use_in_one_message(self, write_jsonl: WriteJSONL) -> None:
        path = write_jsonl(
            "agent-x.jsonl",
            [
                _user("go"),
                _assistant(
                    [
                        _tool_use("t1", "Read"),
                        _tool_use("t2", "Grep"),
                        _tool_use("t3", "Bash"),
                    ],
                ),
                _user(
                    [_tool_result("t1"), _tool_result("t2"), _tool_result("t3")],
                ),
            ],
        )
        trace = parse_subagent_trace(path)
        assert [tc.tool_name for tc in trace.tool_calls] == ["Read", "Grep", "Bash"]

    def test_orphan_tool_use(self, write_jsonl: WriteJSONL) -> None:
        path = write_jsonl(
            "agent-x.jsonl",
            [_user("go"), _assistant([_tool_use("t1", "Read")])],
        )
        trace = parse_subagent_trace(path)
        assert len(trace.tool_calls) == 1
        assert trace.tool_calls[0].tool_name == "Read"
        assert trace.tool_calls[0].result_summary == ""
        assert trace.tool_calls[0].is_error is False

    def test_orphan_tool_result_skipped(self, write_jsonl: WriteJSONL) -> None:
        path = write_jsonl(
            "agent-x.jsonl",
            [
                _user("go"),
                _user([_tool_result("t_nomatch", "stranded")]),
            ],
        )
        assert parse_subagent_trace(path).tool_calls == []

    def test_pairs_by_id_not_position(self, write_jsonl: WriteJSONL) -> None:
        # tool_results come in reverse order of tool_uses
        path = write_jsonl(
            "agent-x.jsonl",
            [
                _user("go"),
                _assistant([_tool_use("t1", "First"), _tool_use("t2", "Second")]),
                _user([_tool_result("t2", "result_two"), _tool_result("t1", "result_one")]),
            ],
        )
        trace = parse_subagent_trace(path)
        by_name = {tc.tool_name: tc.result_summary for tc in trace.tool_calls}
        assert by_name["First"] == "result_one"
        assert by_name["Second"] == "result_two"


class TestInputResultSummaries:
    def test_input_truncated(self, write_jsonl: WriteJSONL) -> None:
        long_value = "x" * (INPUT_SUMMARY_MAX_CHARS * 2)
        path = write_jsonl(
            "agent-x.jsonl",
            [
                _user("go"),
                _assistant([_tool_use("t1", "Bash", {"cmd": long_value})]),
                _user([_tool_result("t1")]),
            ],
        )
        trace = parse_subagent_trace(path)
        assert len(trace.tool_calls[0].input_summary) == INPUT_SUMMARY_MAX_CHARS

    def test_result_truncated(self, write_jsonl: WriteJSONL) -> None:
        long_result = "y" * (RESULT_SUMMARY_MAX_CHARS * 2)
        path = write_jsonl(
            "agent-x.jsonl",
            [
                _user("go"),
                _assistant([_tool_use("t1", "Bash")]),
                _user([_tool_result("t1", long_result)]),
            ],
        )
        trace = parse_subagent_trace(path)
        assert len(trace.tool_calls[0].result_summary) == RESULT_SUMMARY_MAX_CHARS

    def test_non_serializable_input_does_not_crash(self, write_jsonl: WriteJSONL) -> None:
        # Input dict from JSONL only contains JSON-native types, so a
        # non-serializable value can't actually round-trip through the
        # file; simulate by injecting on-disk-valid JSON that _truncate_input
        # will pass through json.dumps cleanly.
        path = write_jsonl(
            "agent-x.jsonl",
            [
                _user("go"),
                _assistant(
                    [_tool_use("t1", "Bash", {"nested": {"deep": [1, 2, 3]}})],
                ),
                _user([_tool_result("t1")]),
            ],
        )
        trace = parse_subagent_trace(path)
        assert '"nested"' in trace.tool_calls[0].input_summary

    def test_unicode_preserved(self, write_jsonl: WriteJSONL) -> None:
        path = write_jsonl(
            "agent-x.jsonl",
            [
                _user("go"),
                _assistant([_tool_use("t1", "Bash", {"q": "café ☕"})]),
                _user([_tool_result("t1", "résumé ✓")]),
            ],
        )
        trace = parse_subagent_trace(path)
        assert "café" in trace.tool_calls[0].input_summary
        assert "résumé" in trace.tool_calls[0].result_summary


class TestIsErrorDetection:
    def test_explicit_true(self, write_jsonl: WriteJSONL) -> None:
        path = write_jsonl(
            "agent-x.jsonl",
            [
                _user("go"),
                _assistant([_tool_use("t1", "Bash")]),
                _user([_tool_result("t1", "some text", is_error=True)]),
            ],
        )
        assert parse_subagent_trace(path).tool_calls[0].is_error is True

    def test_explicit_false_wins_over_keywords(self, write_jsonl: WriteJSONL) -> None:
        # Result text contains "failed" but is_error=False is authoritative.
        path = write_jsonl(
            "agent-x.jsonl",
            [
                _user("go"),
                _assistant([_tool_use("t1", "Bash")]),
                _user([_tool_result("t1", "no operation failed here", is_error=False)]),
            ],
        )
        assert parse_subagent_trace(path).tool_calls[0].is_error is False

    def test_keyword_fallback_fires(self, write_jsonl: WriteJSONL) -> None:
        path = write_jsonl(
            "agent-x.jsonl",
            [
                _user("go"),
                _assistant([_tool_use("t1", "Bash")]),
                _user([_tool_result("t1", "permission denied on /etc/shadow")]),
            ],
        )
        assert parse_subagent_trace(path).tool_calls[0].is_error is True

    def test_keyword_fallback_stays_false(self, write_jsonl: WriteJSONL) -> None:
        path = write_jsonl(
            "agent-x.jsonl",
            [
                _user("go"),
                _assistant([_tool_use("t1", "Bash")]),
                _user([_tool_result("t1", "all good, wrote 42 bytes")]),
            ],
        )
        assert parse_subagent_trace(path).tool_calls[0].is_error is False


class TestUsageAggregation:
    def test_sums_across_assistants(self, write_jsonl: WriteJSONL) -> None:
        path = write_jsonl(
            "agent-x.jsonl",
            [
                _user("go"),
                _assistant(
                    [_tool_use("t1", "Bash")],
                    message_id="msg_a",
                    usage={"input_tokens": 10, "output_tokens": 5},
                ),
                _user([_tool_result("t1")]),
                _assistant(
                    [{"type": "text", "text": "done"}],
                    message_id="msg_b",
                    usage={"input_tokens": 20, "output_tokens": 3, "cache_read_input_tokens": 100},
                ),
            ],
        )
        trace = parse_subagent_trace(path)
        assert trace.usage.input_tokens == 30
        assert trace.usage.output_tokens == 8
        assert trace.usage.cache_read_input_tokens == 100

    def test_user_messages_skipped(self, write_jsonl: WriteJSONL) -> None:
        path = write_jsonl("agent-x.jsonl", [_user("one"), _user("two")])
        trace = parse_subagent_trace(path)
        assert trace.usage.total_tokens == 0


class TestDurationMs:
    def test_span_calculated(self, write_jsonl: WriteJSONL) -> None:
        path = write_jsonl(
            "agent-x.jsonl",
            [
                _user("go", timestamp="2026-04-21T10:00:00.000Z"),
                _assistant(
                    [{"type": "text", "text": "done"}],
                    timestamp="2026-04-21T10:00:05.500Z",
                ),
            ],
        )
        # 5.5 seconds = 5500 ms
        assert parse_subagent_trace(path).duration_ms == 5500

    def test_single_message_returns_none(self, write_jsonl: WriteJSONL) -> None:
        path = write_jsonl("agent-x.jsonl", [_user("only one")])
        assert parse_subagent_trace(path).duration_ms is None

    def test_no_timestamps_returns_none(self, write_jsonl: WriteJSONL) -> None:
        path = write_jsonl(
            "agent-x.jsonl",
            [
                _user("go", timestamp=None),
                _assistant([{"type": "text", "text": "hi"}], timestamp=None),
            ],
        )
        assert parse_subagent_trace(path).duration_ms is None


class TestEdgeCases:
    def test_empty_file(self, tmp_path: Path) -> None:
        path = tmp_path / "agent-x.jsonl"
        path.write_text("")
        trace = parse_subagent_trace(path)
        assert trace.tool_calls == []
        assert trace.delegation_prompt == ""
        assert trace.usage.total_tokens == 0
        assert trace.duration_ms is None

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match="not found"):
            parse_subagent_trace(tmp_path / "agent-nope.jsonl")

    def test_malformed_filename_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "not-an-agent.jsonl"
        path.write_text("")
        with pytest.raises(ValueError, match="Malformed subagent filename"):
            parse_subagent_trace(path)

    def test_streaming_snapshots_deduplicated(self, write_jsonl: WriteJSONL) -> None:
        # Two assistant snapshots sharing message_id; dedup keeps the
        # one with higher output_tokens. Both carry the same tool_use_id,
        # so the pairing emits exactly one tool_call.
        now = datetime.now(UTC)
        path = write_jsonl(
            "agent-x.jsonl",
            [
                _user("go"),
                _assistant(
                    [_tool_use("t1", "Bash", {"cmd": "ls"})],
                    message_id="msg_shared",
                    timestamp=now.isoformat().replace("+00:00", "Z"),
                    usage={"input_tokens": 5, "output_tokens": 2},
                ),
                _assistant(
                    [_tool_use("t1", "Bash", {"cmd": "ls"})],
                    message_id="msg_shared",
                    timestamp=now.isoformat().replace("+00:00", "Z"),
                    usage={"input_tokens": 5, "output_tokens": 8},
                ),
                _user([_tool_result("t1", "file")]),
            ],
        )
        trace = parse_subagent_trace(path)
        assert len(trace.tool_calls) == 1
        # Dedup kept the snapshot with higher output_tokens (8).
        assert trace.usage.output_tokens == 8


class TestAggregateCounts:
    def test_total_errors_counts_is_error(self, write_jsonl: WriteJSONL) -> None:
        path = write_jsonl(
            "agent-x.jsonl",
            [
                _user("go"),
                _assistant(
                    [_tool_use("t1"), _tool_use("t2"), _tool_use("t3")],
                ),
                _user(
                    [
                        _tool_result("t1", is_error=True),
                        _tool_result("t2", is_error=False),
                        _tool_result("t3", is_error=True),
                    ],
                ),
            ],
        )
        trace = parse_subagent_trace(path)
        assert trace.total_errors == 2

    def test_total_retries_is_zero_for_now(self, write_jsonl: WriteJSONL) -> None:
        """Retry detection is deferred to a later story; parser emits zero."""
        path = write_jsonl(
            "agent-x.jsonl",
            [
                _user("go"),
                _assistant([_tool_use("t1", "Bash", {"cmd": "fails"})]),
                _user([_tool_result("t1", "error", is_error=True)]),
                _assistant([_tool_use("t2", "Bash", {"cmd": "fails"})]),
                _user([_tool_result("t2", "error", is_error=True)]),
            ],
        )
        trace = parse_subagent_trace(path)
        assert trace.total_retries == 0
        assert trace.retry_sequences == []
