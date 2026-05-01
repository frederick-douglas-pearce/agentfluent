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


class TestModelCapture:
    """The parser retains the first assistant message's `model` string
    on `SubagentTrace.model` so `diagnostics.model_routing` can fall
    back to it when an agent has no declared `AgentConfig.model`. See
    #142."""

    def test_populates_from_first_assistant_message(
        self, write_jsonl: WriteJSONL,
    ) -> None:
        path = write_jsonl(
            "agent-x.jsonl",
            [
                _user("go"),
                _assistant(
                    [{"type": "text", "text": "hi"}],
                    message_id="msg_01",
                ),
            ],
        )
        trace = parse_subagent_trace(path)
        # Default model on _assistant is `claude-opus-4-6`.
        assert trace.model == "claude-opus-4-6"

    def test_no_assistants_leaves_model_none(
        self, write_jsonl: WriteJSONL,
    ) -> None:
        path = write_jsonl("agent-x.jsonl", [_user("only user")])
        assert parse_subagent_trace(path).model is None

    def test_uses_first_when_multiple_assistants_present(
        self, write_jsonl: WriteJSONL,
    ) -> None:
        # Two assistant messages with different models (a data anomaly
        # — subagents don't switch models mid-run — but we still pick
        # the first deterministically).
        first = _assistant(
            [{"type": "text", "text": "a"}],
            message_id="msg_a",
            timestamp="2026-04-21T10:00:01.000Z",
        )
        first["message"]["model"] = "claude-haiku-4-5"
        second = _assistant(
            [{"type": "text", "text": "b"}],
            message_id="msg_b",
            timestamp="2026-04-21T10:00:02.000Z",
        )
        second["message"]["model"] = "claude-opus-4-7"

        path = write_jsonl("agent-x.jsonl", [_user("go"), first, second])
        assert parse_subagent_trace(path).model == "claude-haiku-4-5"


class TestEdgeCases:
    def test_empty_file(self, tmp_path: Path) -> None:
        path = tmp_path / "agent-x.jsonl"
        path.write_text("")
        trace = parse_subagent_trace(path)
        assert trace.tool_calls == []
        assert trace.delegation_prompt == ""
        assert trace.usage.total_tokens == 0
        assert trace.duration_ms is None
        assert trace.model is None

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

    # (Retry detection was deferred when this module was first written and
    # later shipped in #104. See `test_traces_retry.py` for comprehensive
    # retry-sequence tests; the obsolete "total_retries_is_zero" placeholder
    # was removed when the parser-merge fix for #153 exposed it.)


class TestResultTimestamp:
    """`SubagentToolCall.result_timestamp` captured from the user message
    that carried the matching `tool_result` block (#230)."""

    def test_paired_call_captures_both_timestamps(self, write_jsonl: WriteJSONL) -> None:
        path = write_jsonl(
            "agent-x.jsonl",
            [
                _user("go", timestamp="2026-04-21T10:00:00.000Z"),
                _assistant(
                    [_tool_use("t1", "Bash")],
                    timestamp="2026-04-21T10:00:01.000Z",
                ),
                _user(
                    [_tool_result("t1", "ok")],
                    timestamp="2026-04-21T10:00:01.500Z",
                ),
            ],
        )
        tc = parse_subagent_trace(path).tool_calls[0]
        assert tc.timestamp == datetime(2026, 4, 21, 10, 0, 1, tzinfo=UTC)
        assert tc.result_timestamp == datetime(2026, 4, 21, 10, 0, 1, 500_000, tzinfo=UTC)

    def test_orphan_tool_use_has_no_result_timestamp(self, write_jsonl: WriteJSONL) -> None:
        path = write_jsonl(
            "agent-x.jsonl",
            [_user("go"), _assistant([_tool_use("t1", "Read")])],
        )
        tc = parse_subagent_trace(path).tool_calls[0]
        assert tc.result_timestamp is None


class TestIdleGapDetection:
    """`SubagentTrace.idle_gap_ms` / `active_duration_ms` (#230).

    Heuristic: per-call gap > max(IDLE_GAP_K * median, IDLE_GAP_FLOOR_MS)
    counts as idle. The floor is 5 minutes (300_000 ms) and dominates
    in tests where median tool latency is small.
    """

    @staticmethod
    def _pair(
        tool_id: str,
        use_ts: str,
        result_ts: str,
    ) -> list[dict[str, Any]]:
        # Unique message_id per assistant message — the parser merges
        # snapshots that share an id, which would collapse these into one.
        return [
            _assistant(
                [_tool_use(tool_id, "Bash")],
                message_id=f"msg_{tool_id}",
                timestamp=use_ts,
            ),
            _user([_tool_result(tool_id, "ok")], timestamp=result_ts),
        ]

    def test_no_paired_calls_returns_none(self, write_jsonl: WriteJSONL) -> None:
        # Orphan tool_use with no result → no paired gap → cannot compute
        path = write_jsonl(
            "agent-x.jsonl",
            [_user("go"), _assistant([_tool_use("t1", "Read")])],
        )
        trace = parse_subagent_trace(path)
        assert trace.idle_gap_ms is None
        assert trace.active_duration_ms is None

    def test_single_paired_call_returns_none(self, write_jsonl: WriteJSONL) -> None:
        # 1 paired call < 2 minimum for per-trace median → indeterminate
        path = write_jsonl(
            "agent-x.jsonl",
            [
                _user("go", timestamp="2026-04-21T10:00:00.000Z"),
                *self._pair("t1", "2026-04-21T10:00:01.000Z", "2026-04-21T10:00:02.000Z"),
            ],
        )
        trace = parse_subagent_trace(path)
        assert trace.idle_gap_ms is None
        assert trace.active_duration_ms is None

    def test_short_gaps_no_idle(self, write_jsonl: WriteJSONL) -> None:
        # Several fast tool calls — every gap below the 5-min floor
        path = write_jsonl(
            "agent-x.jsonl",
            [
                _user("go", timestamp="2026-04-21T10:00:00.000Z"),
                *self._pair("t1", "2026-04-21T10:00:01.000Z", "2026-04-21T10:00:02.000Z"),
                *self._pair("t2", "2026-04-21T10:00:03.000Z", "2026-04-21T10:00:04.000Z"),
                *self._pair("t3", "2026-04-21T10:00:05.000Z", "2026-04-21T10:00:06.500Z"),
            ],
        )
        trace = parse_subagent_trace(path)
        assert trace.idle_gap_ms == 0
        assert trace.active_duration_ms == trace.duration_ms

    def test_long_gap_above_floor_flagged(self, write_jsonl: WriteJSONL) -> None:
        # Two short calls + one 10-minute gap — floor binds, k×median doesn't
        path = write_jsonl(
            "agent-x.jsonl",
            [
                _user("go", timestamp="2026-04-21T10:00:00.000Z"),
                *self._pair("t1", "2026-04-21T10:00:01.000Z", "2026-04-21T10:00:02.000Z"),
                *self._pair("t2", "2026-04-21T10:00:03.000Z", "2026-04-21T10:13:03.000Z"),
                *self._pair("t3", "2026-04-21T10:13:04.000Z", "2026-04-21T10:13:05.000Z"),
            ],
        )
        trace = parse_subagent_trace(path)
        # The 13-minute gap (780_000 ms) is the only one above the floor
        assert trace.idle_gap_ms == 780_000
        assert trace.duration_ms is not None
        assert trace.active_duration_ms is not None
        assert trace.active_duration_ms == trace.duration_ms - 780_000

    def test_clock_skew_negative_gap_ignored(self, write_jsonl: WriteJSONL) -> None:
        # tool_result timestamp before tool_use timestamp — clock skew or
        # a parsing artifact. Should not contribute synthetic work.
        path = write_jsonl(
            "agent-x.jsonl",
            [
                _user("go", timestamp="2026-04-21T10:00:00.000Z"),
                *self._pair("t1", "2026-04-21T10:00:10.000Z", "2026-04-21T10:00:09.000Z"),
                *self._pair("t2", "2026-04-21T10:00:11.000Z", "2026-04-21T10:00:12.000Z"),
            ],
        )
        # One valid gap remains → fewer than 2 → returns None
        trace = parse_subagent_trace(path)
        assert trace.idle_gap_ms is None

    def test_active_duration_clamped_at_zero(self, write_jsonl: WriteJSONL) -> None:
        # Defensive: if idle_gap_ms ever exceeds duration_ms (e.g., due
        # to overlapping calls or a future heuristic change), don't
        # report a negative active_duration.
        from agentfluent.traces.parser import _compute_idle_gap_ms

        # Real data here: assert the parser's clamp logic via a
        # constructed scenario — durations larger than the wall span
        # can't happen via real JSONL, so we just exercise the public
        # parser path and confirm the property holds in practice.
        path = write_jsonl(
            "agent-x.jsonl",
            [
                _user("go", timestamp="2026-04-21T10:00:00.000Z"),
                *self._pair("t1", "2026-04-21T10:00:01.000Z", "2026-04-21T10:00:02.000Z"),
                *self._pair("t2", "2026-04-21T10:00:03.000Z", "2026-04-21T10:13:03.000Z"),
            ],
        )
        trace = parse_subagent_trace(path)
        assert trace.active_duration_ms is not None
        assert trace.active_duration_ms >= 0
        # Sanity: helper is callable and returns an int when 2+ paired gaps exist.
        assert isinstance(_compute_idle_gap_ms(trace.tool_calls), int)
