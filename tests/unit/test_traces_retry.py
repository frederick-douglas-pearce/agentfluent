"""Tests for retry sequence detection."""

from __future__ import annotations

from collections.abc import Callable
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from agentfluent.traces.models import RetrySequence, SubagentToolCall
from agentfluent.traces.parser import parse_subagent_trace
from agentfluent.traces.retry import (
    SIMILARITY_THRESHOLD,
    _is_similar_retry,
    detect_retry_sequences,
)
from tests._builders import (
    assistant_with_tool_use,
    user_message,
    user_with_tool_result,
)

WriteJSONL = Callable[[str, list[dict[str, Any]]], Path]


def _call(
    name: str = "Bash",
    input_str: str = '{"cmd":"ls"}',
    *,
    is_error: bool = False,
    result: str = "ok",
) -> SubagentToolCall:
    return SubagentToolCall(
        tool_name=name,
        input_summary=input_str,
        result_summary=result,
        is_error=is_error,
    )


class TestDetectRetrySequences:
    def test_empty_list_returns_empty(self) -> None:
        assert detect_retry_sequences([]) == []

    def test_single_call_returns_empty(self) -> None:
        assert detect_retry_sequences([_call()]) == []

    def test_two_identical_emits_one_sequence(self) -> None:
        seqs = detect_retry_sequences([_call(), _call()])
        assert len(seqs) == 1
        assert isinstance(seqs[0], RetrySequence)
        assert seqs[0].tool_name == "Bash"
        assert seqs[0].attempts == 2
        assert seqs[0].tool_call_indices == [0, 1]

    def test_three_identical_emits_one_sequence_attempts_three(self) -> None:
        seqs = detect_retry_sequences([_call(), _call(), _call()])
        assert len(seqs) == 1
        assert seqs[0].attempts == 3
        assert seqs[0].tool_call_indices == [0, 1, 2]

    def test_two_identical_then_different_emits_one_sequence(self) -> None:
        seqs = detect_retry_sequences(
            [_call(), _call(), _call(name="Read", input_str='{"file":"x"}')],
        )
        assert len(seqs) == 1
        assert seqs[0].attempts == 2
        assert seqs[0].tool_call_indices == [0, 1]

    def test_dissimilar_same_tool_adjacent_emits_nothing(self) -> None:
        # Different JSON bodies for the same tool; ratio falls below threshold.
        seqs = detect_retry_sequences(
            [
                _call(input_str='{"cmd":"ls /tmp"}'),
                _call(input_str='{"cmd":"rm -rf /"}'),
            ],
        )
        assert seqs == []

    def test_same_input_different_tool_emits_nothing(self) -> None:
        seqs = detect_retry_sequences(
            [_call(name="Bash"), _call(name="Read")],
        )
        assert seqs == []

    def test_interleaved_breaks_run(self) -> None:
        # [Bash, Bash, Read, Bash, Bash] -> two separate Bash runs of 2
        seqs = detect_retry_sequences(
            [
                _call(name="Bash"),
                _call(name="Bash"),
                _call(name="Read", input_str='{"file":"x"}'),
                _call(name="Bash"),
                _call(name="Bash"),
            ],
        )
        assert len(seqs) == 2
        assert seqs[0].tool_call_indices == [0, 1]
        assert seqs[1].tool_call_indices == [3, 4]

    def test_two_separate_runs_of_same_tool(self) -> None:
        # [B, B, C, B, B] -> two runs of attempts=2
        seqs = detect_retry_sequences(
            [
                _call(name="Bash", input_str='{"cmd":"ls"}'),
                _call(name="Bash", input_str='{"cmd":"ls"}'),
                _call(name="Read", input_str='{"file":"x"}'),
                _call(name="Bash", input_str='{"cmd":"pwd"}'),
                _call(name="Bash", input_str='{"cmd":"pwd"}'),
            ],
        )
        assert [s.attempts for s in seqs] == [2, 2]

    def test_similarity_boundary_inclusive(self) -> None:
        # Craft two strings whose SequenceMatcher.ratio() is exactly
        # SIMILARITY_THRESHOLD (0.80). 20-char strings sharing 16 chars
        # yield 2*16/(20+20) = 0.80. `>=` makes this qualify.
        a = "aaaaaaaaaaaaaaaaaaaa"              # 20 a's
        b = "aaaaaaaaaaaaaaaabbbb"              # 16 a's then 4 b's
        ratio = SequenceMatcher(None, a, b).ratio()
        assert ratio == SIMILARITY_THRESHOLD, f"expected {SIMILARITY_THRESHOLD}, got {ratio}"

        seqs = detect_retry_sequences([_call(input_str=a), _call(input_str=b)])
        assert len(seqs) == 1

    def test_similarity_just_below_threshold_rejected(self) -> None:
        # 20-char strings sharing 15 chars -> 2*15/40 = 0.75 < 0.80
        a = "aaaaaaaaaaaaaaaaaaaa"
        b = "aaaaaaaaaaaaaaabbbbb"
        seqs = detect_retry_sequences([_call(input_str=a), _call(input_str=b)])
        assert seqs == []

    def test_drift_chain_tolerates_gradual_change(self) -> None:
        # Adjacent pairs each pass threshold; head-vs-tail would fail.
        # 40-char strings, shifting 4 chars per step.
        a = "a" * 40
        b = ("a" * 36) + "bbbb"       # vs a: ratio = 2*36/80 = 0.90
        c = ("a" * 32) + "bbbbbbbb"   # vs b: 2*36/80 = 0.90 (shared 36 a's); vs a: 0.80
        d = ("a" * 28) + "bbbbbbbbbbbb"  # vs c: 0.90; vs a: 0.70

        assert _is_similar_retry(_call(input_str=a), _call(input_str=b))
        assert _is_similar_retry(_call(input_str=b), _call(input_str=c))
        assert _is_similar_retry(_call(input_str=c), _call(input_str=d))
        # Head-to-tail would fail:
        assert not _is_similar_retry(_call(input_str=a), _call(input_str=d))

        seqs = detect_retry_sequences(
            [
                _call(input_str=a),
                _call(input_str=b),
                _call(input_str=c),
                _call(input_str=d),
            ],
        )
        # Predecessor-based comparison emits one sequence spanning all four.
        assert len(seqs) == 1
        assert seqs[0].attempts == 4
        assert seqs[0].tool_call_indices == [0, 1, 2, 3]

    def test_all_errors_run_captures_first_and_last(self) -> None:
        seqs = detect_retry_sequences(
            [
                _call(is_error=True, result="err1"),
                _call(is_error=True, result="err2"),
                _call(is_error=True, result="err3"),
            ],
        )
        assert len(seqs) == 1
        assert seqs[0].first_error_message == "err1"
        assert seqs[0].last_error_message == "err3"
        assert seqs[0].eventual_success is False

    def test_mixed_errors_populates_fields_correctly(self) -> None:
        # [err1, ok, err2, ok] — first error is at index 0, last error at 2,
        # eventual_success driven by the last call's is_error (False => success).
        seqs = detect_retry_sequences(
            [
                _call(is_error=True, result="err1"),
                _call(is_error=False, result="ok1"),
                _call(is_error=True, result="err2"),
                _call(is_error=False, result="ok2"),
            ],
        )
        assert len(seqs) == 1
        assert seqs[0].first_error_message == "err1"
        assert seqs[0].last_error_message == "err2"
        assert seqs[0].eventual_success is True

    def test_no_error_repeat_chain_has_none_error_messages(self) -> None:
        # Legal but rare: three successful identical calls form a sequence.
        seqs = detect_retry_sequences(
            [_call(is_error=False), _call(is_error=False), _call(is_error=False)],
        )
        assert len(seqs) == 1
        assert seqs[0].first_error_message is None
        assert seqs[0].last_error_message is None
        assert seqs[0].eventual_success is True

    def test_single_error_has_first_equal_last(self) -> None:
        seqs = detect_retry_sequences(
            [_call(is_error=False, result="ok"), _call(is_error=True, result="boom")],
        )
        assert len(seqs) == 1
        assert seqs[0].first_error_message == "boom"
        assert seqs[0].last_error_message == "boom"
        assert seqs[0].eventual_success is False

    def test_tool_call_indices_match_range(self) -> None:
        # [B, B, B, R, B, B] -> two runs at [0,1,2] and [4,5]
        seqs = detect_retry_sequences(
            [
                _call(name="Bash"),
                _call(name="Bash"),
                _call(name="Bash"),
                _call(name="Read", input_str='{"file":"x"}'),
                _call(name="Bash"),
                _call(name="Bash"),
            ],
        )
        assert seqs[0].tool_call_indices == [0, 1, 2]
        assert seqs[1].tool_call_indices == [4, 5]
        assert seqs[0].attempts == 3
        assert seqs[1].attempts == 2


class TestParserIntegration:
    """Confirm `parse_subagent_trace` wires in the detector."""

    def _write_trace_with_calls(
        self,
        write_jsonl: WriteJSONL,
        tool_calls: list[tuple[str, str, dict[str, Any], bool, str]],
    ) -> Path:
        """Build a JSONL trace with one assistant-user pair per tool call.

        Each `tool_calls` tuple is (tool_use_id, tool_name, input, is_error, result).
        """
        lines: list[dict[str, Any]] = [
            user_message("go", timestamp="2026-04-21T10:00:00.000Z"),
        ]
        for i, (tool_use_id, name, inp, is_err, result) in enumerate(tool_calls):
            lines.append(
                assistant_with_tool_use(
                    tool_use_id, name, inp,
                    message_id=f"msg_{i}",
                    timestamp=f"2026-04-21T10:00:{i + 1:02d}.000Z",
                ),
            )
            lines.append(
                user_with_tool_result(
                    tool_use_id, result, is_error=is_err,
                    timestamp=f"2026-04-21T10:00:{i + 1:02d}.500Z",
                ),
            )
        return write_jsonl("agent-integration.jsonl", lines)

    def test_no_retries_leaves_fields_empty(
        self, write_jsonl: WriteJSONL,
    ) -> None:
        path = self._write_trace_with_calls(
            write_jsonl,
            [
                ("t1", "Bash", {"cmd": "ls"}, False, "ok"),
                ("t2", "Read", {"file": "x"}, False, "ok"),
            ],
        )
        trace = parse_subagent_trace(path)
        assert trace.retry_sequences == []
        assert trace.total_retries == 0

    def test_one_retry_produces_total_retries_one(
        self, write_jsonl: WriteJSONL,
    ) -> None:
        path = self._write_trace_with_calls(
            write_jsonl,
            [
                ("t1", "Bash", {"cmd": "ls"}, True, "err"),
                ("t2", "Bash", {"cmd": "ls"}, False, "ok"),
            ],
        )
        trace = parse_subagent_trace(path)
        assert len(trace.retry_sequences) == 1
        assert trace.retry_sequences[0].attempts == 2
        assert trace.retry_sequences[0].eventual_success is True
        # total_retries = sum(attempts - 1) = 2 - 1 = 1
        assert trace.total_retries == 1

    def test_two_separate_runs_sums_across(
        self, write_jsonl: WriteJSONL,
    ) -> None:
        path = self._write_trace_with_calls(
            write_jsonl,
            [
                # Run 1: three Bash ls
                ("t1", "Bash", {"cmd": "ls"}, True, "err"),
                ("t2", "Bash", {"cmd": "ls"}, True, "err"),
                ("t3", "Bash", {"cmd": "ls"}, False, "ok"),
                # Breaker
                ("t4", "Read", {"file": "x"}, False, "ok"),
                # Run 2: two Bash pwd
                ("t5", "Bash", {"cmd": "pwd"}, False, "ok"),
                ("t6", "Bash", {"cmd": "pwd"}, False, "ok"),
            ],
        )
        trace = parse_subagent_trace(path)
        assert len(trace.retry_sequences) == 2
        attempts = sorted(seq.attempts for seq in trace.retry_sequences)
        assert attempts == [2, 3]
        # total_retries = (3-1) + (2-1) = 3
        assert trace.total_retries == 3
