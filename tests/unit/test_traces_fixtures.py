"""End-to-end fixture-driven tests for the trace parser + retry detector.

Each test class targets one on-disk JSONL fixture in
``tests/fixtures/subagents/`` and asserts the full chain —
``parse_subagent_trace`` + ``detect_retry_sequences`` (implicitly, via
the parser's integration with the retry detector) — produces the
expected ``SubagentTrace``. Inline-JSONL tests in
``test_traces_parser.py`` / ``test_traces_retry.py`` cover the narrow
unit-level behaviors; this file covers the realistic "parse a file you
didn't construct in the test body" path.
"""

from __future__ import annotations

from pathlib import Path

from agentfluent.traces.models import UNKNOWN_AGENT_TYPE
from agentfluent.traces.parser import parse_subagent_trace


class TestBasicTrace:
    def test_parses_three_tool_calls(self, subagent_basic_path: Path) -> None:
        trace = parse_subagent_trace(subagent_basic_path)
        assert [tc.tool_name for tc in trace.tool_calls] == ["Glob", "Grep", "Read"]

    def test_no_errors_no_retries(self, subagent_basic_path: Path) -> None:
        trace = parse_subagent_trace(subagent_basic_path)
        assert trace.total_errors == 0
        assert trace.total_retries == 0
        assert trace.retry_sequences == []

    def test_delegation_prompt_captured(self, subagent_basic_path: Path) -> None:
        trace = parse_subagent_trace(subagent_basic_path)
        assert "deprecated API" in trace.delegation_prompt

    def test_usage_aggregated(self, subagent_basic_path: Path) -> None:
        trace = parse_subagent_trace(subagent_basic_path)
        # 4 assistants × 150-250 input, 15-40 output
        assert trace.usage.input_tokens > 0
        assert trace.usage.output_tokens > 0
        assert trace.usage.cache_read_input_tokens > 0

    def test_duration_ms_spans_timestamps(
        self, subagent_basic_path: Path,
    ) -> None:
        trace = parse_subagent_trace(subagent_basic_path)
        # 12:00:00 -> 12:00:07 = 7000ms
        assert trace.duration_ms == 7000

    def test_agent_id_from_filename(self, subagent_basic_path: Path) -> None:
        assert parse_subagent_trace(subagent_basic_path).agent_id == "basic"

    def test_agent_type_is_unknown(self, subagent_basic_path: Path) -> None:
        # Linker overwrites; parser leaves UNKNOWN.
        assert parse_subagent_trace(subagent_basic_path).agent_type == UNKNOWN_AGENT_TYPE


class TestErrorTrace:
    def test_detects_explicit_is_error(self, subagent_errors_path: Path) -> None:
        trace = parse_subagent_trace(subagent_errors_path)
        assert trace.total_errors == 1

    def test_error_call_is_write(self, subagent_errors_path: Path) -> None:
        trace = parse_subagent_trace(subagent_errors_path)
        erroring = [tc for tc in trace.tool_calls if tc.is_error]
        assert len(erroring) == 1
        assert erroring[0].tool_name == "Write"

    def test_no_retry_sequence_for_different_tools(
        self, subagent_errors_path: Path,
    ) -> None:
        # Write (error) then Edit (ok) — different tools, no retry.
        trace = parse_subagent_trace(subagent_errors_path)
        assert trace.retry_sequences == []


class TestRetryTrace:
    def test_detects_retry_sequence(self, subagent_retry_path: Path) -> None:
        trace = parse_subagent_trace(subagent_retry_path)
        assert len(trace.retry_sequences) == 1
        seq = trace.retry_sequences[0]
        assert seq.tool_name == "Bash"
        assert seq.attempts == 3
        assert seq.tool_call_indices == [0, 1, 2]

    def test_retry_was_all_errors(self, subagent_retry_path: Path) -> None:
        trace = parse_subagent_trace(subagent_retry_path)
        seq = trace.retry_sequences[0]
        assert seq.eventual_success is False
        assert seq.first_error_message is not None
        assert "Permission denied" in seq.first_error_message

    def test_total_retries_counts_retry_portion(
        self, subagent_retry_path: Path,
    ) -> None:
        # 3 attempts -> 2 retries (one original + two retries).
        trace = parse_subagent_trace(subagent_retry_path)
        assert trace.total_retries == 2

    def test_total_errors_counts_all_three(
        self, subagent_retry_path: Path,
    ) -> None:
        assert parse_subagent_trace(subagent_retry_path).total_errors == 3


class TestStuckPattern:
    def test_detects_long_sequence(self, subagent_stuck_path: Path) -> None:
        trace = parse_subagent_trace(subagent_stuck_path)
        assert len(trace.retry_sequences) == 1
        assert trace.retry_sequences[0].attempts == 5

    def test_stuck_pattern_qualifies_for_retry_loop_threshold(
        self, subagent_stuck_path: Path,
    ) -> None:
        # #107's RETRY_LOOP signal filters to attempts >= 3; confirm the
        # stuck fixture sails over that threshold.
        trace = parse_subagent_trace(subagent_stuck_path)
        assert trace.retry_sequences[0].attempts >= 3

    def test_stuck_tool_call_indices_cover_entire_run(
        self, subagent_stuck_path: Path,
    ) -> None:
        trace = parse_subagent_trace(subagent_stuck_path)
        assert trace.retry_sequences[0].tool_call_indices == [0, 1, 2, 3, 4]


class TestEmptyFile:
    def test_parses_to_empty_trace(self, subagent_empty_path: Path) -> None:
        trace = parse_subagent_trace(subagent_empty_path)
        assert trace.tool_calls == []
        assert trace.retry_sequences == []
        assert trace.delegation_prompt == ""
        assert trace.usage.total_tokens == 0
        assert trace.duration_ms is None

    def test_agent_id_still_from_filename(
        self, subagent_empty_path: Path,
    ) -> None:
        assert parse_subagent_trace(subagent_empty_path).agent_id == "empty"


class TestMalformedTrace:
    def test_skips_malformed_lines_and_keeps_valid(
        self, subagent_malformed_path: Path,
    ) -> None:
        trace = parse_subagent_trace(subagent_malformed_path)
        # Valid messages in the file: 1 user (delegation), 1 assistant
        # (tool_use Glob), 1 user (tool_result), 1 assistant (text).
        # So exactly one tool_call.
        assert len(trace.tool_calls) == 1
        assert trace.tool_calls[0].tool_name == "Glob"

    def test_delegation_prompt_survives_malformed_noise(
        self, subagent_malformed_path: Path,
    ) -> None:
        trace = parse_subagent_trace(subagent_malformed_path)
        assert "test files" in trace.delegation_prompt


class TestLargeTrace:
    def test_parses_all_tool_calls(self, subagent_large_path: Path) -> None:
        trace = parse_subagent_trace(subagent_large_path)
        assert len(trace.tool_calls) == 22

    def test_usage_aggregates_across_many_assistants(
        self, subagent_large_path: Path,
    ) -> None:
        trace = parse_subagent_trace(subagent_large_path)
        # Roughly 23 assistants (22 tool_uses + 1 final text) with input_tokens
        # 110..320 and the final 500 -> clearly >1000 aggregate.
        assert trace.usage.input_tokens > 1000

    def test_one_error_detected(self, subagent_large_path: Path) -> None:
        # Fixture places an is_error on one of the 22 calls.
        assert parse_subagent_trace(subagent_large_path).total_errors == 1

    def test_no_retry_sequences_for_heterogeneous_tools(
        self, subagent_large_path: Path,
    ) -> None:
        # Tools rotate (Read, Grep, Glob, Bash); no consecutive same-tool
        # chain qualifies as a retry.
        trace = parse_subagent_trace(subagent_large_path)
        assert trace.retry_sequences == []


class TestStreamingDupes:
    def test_dedup_collapses_snapshots(
        self, subagent_streaming_dupes_path: Path,
    ) -> None:
        # Fixture has 3 snapshots of the same assistant message (id=msg_001),
        # then a matching tool_result, then a final assistant text. Dedup
        # keeps one tool_use per message_id, so exactly one tool_call.
        trace = parse_subagent_trace(subagent_streaming_dupes_path)
        assert len(trace.tool_calls) == 1

    def test_dedup_keeps_highest_output_tokens_snapshot(
        self, subagent_streaming_dupes_path: Path,
    ) -> None:
        # Snapshots have output_tokens 2, 5, 15; dedup keeps 15. That one's
        # input_tokens is 100, so the retained assistant's usage contributes
        # 15 output tokens (not 2 or 5).
        trace = parse_subagent_trace(subagent_streaming_dupes_path)
        # Final assistant adds output_tokens=5; total output = 15 + 5 = 20.
        assert trace.usage.output_tokens == 20
