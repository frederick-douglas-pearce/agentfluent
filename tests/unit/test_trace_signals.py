"""Tests for trace-level signal extraction."""

from agentfluent.config.models import Severity
from agentfluent.diagnostics.models import SignalType
from agentfluent.diagnostics.trace_signals import extract_trace_signals
from agentfluent.traces.models import (
    RetrySequence,
    SubagentToolCall,
    SubagentTrace,
)


def _tc(
    tool: str = "Bash",
    inp: str = "ls /tmp",
    res: str = "ok",
    err: bool = False,
) -> SubagentToolCall:
    return SubagentToolCall(
        tool_name=tool,
        input_summary=inp,
        result_summary=res,
        is_error=err,
    )


def _rs(
    tool: str = "Bash",
    attempts: int = 3,
    indices: list[int] | None = None,
    first_err: str | None = None,
    success: bool = False,
) -> RetrySequence:
    return RetrySequence(
        tool_name=tool,
        attempts=attempts,
        first_error_message=first_err,
        last_error_message=first_err,
        eventual_success=success,
        tool_call_indices=indices or list(range(attempts)),
    )


def _trace(
    calls: list[SubagentToolCall] | None = None,
    sequences: list[RetrySequence] | None = None,
    agent_type: str = "general-purpose",
) -> SubagentTrace:
    return SubagentTrace(
        agent_id="agent-test",
        agent_type=agent_type,
        delegation_prompt="do the thing",
        tool_calls=calls or [],
        retry_sequences=sequences or [],
    )


class TestExtractTraceSignals:
    def test_none_trace_returns_empty(self) -> None:
        assert extract_trace_signals(None) == []

    def test_empty_tool_calls_returns_empty(self) -> None:
        assert extract_trace_signals(_trace()) == []

    def test_healthy_trace_emits_nothing(self) -> None:
        trace = _trace(calls=[_tc(), _tc(tool="Read")])
        assert extract_trace_signals(trace) == []

    def test_mixed_trace_emits_multiple_signal_types(self) -> None:
        # Permission failure at index 0 + error sequence at 1,2.
        trace = _trace(
            calls=[
                _tc(tool="Write", res="Permission denied on /etc", err=True),
                _tc(tool="Read", res="File not found", err=True),
                _tc(tool="Read", res="File not found", err=True),
            ],
        )
        signals = extract_trace_signals(trace)
        types = {s.signal_type for s in signals}
        assert SignalType.PERMISSION_FAILURE in types
        assert SignalType.TOOL_ERROR_SEQUENCE in types


class TestPermissionFailure:
    def test_detects_permission_denied(self) -> None:
        trace = _trace(
            calls=[_tc(tool="Write", res="Permission denied on /etc/passwd", err=True)],
        )
        signals = extract_trace_signals(trace)
        perms = [s for s in signals if s.signal_type == SignalType.PERMISSION_FAILURE]
        assert len(perms) == 1
        assert perms[0].severity == Severity.CRITICAL
        assert perms[0].detail["matched_keyword"] == "permission denied"

    def test_detects_all_four_keywords(self) -> None:
        for keyword in ("permission denied", "access denied", "not allowed", "blocked"):
            trace = _trace(calls=[_tc(tool="Bash", res=f"Error: {keyword} here", err=True)])
            signals = extract_trace_signals(trace)
            perms = [s for s in signals if s.signal_type == SignalType.PERMISSION_FAILURE]
            assert len(perms) == 1, f"missed keyword: {keyword}"
            assert perms[0].detail["matched_keyword"] == keyword

    def test_case_insensitive(self) -> None:
        trace = _trace(calls=[_tc(res="PERMISSION DENIED", err=True)])
        signals = extract_trace_signals(trace)
        perms = [s for s in signals if s.signal_type == SignalType.PERMISSION_FAILURE]
        assert len(perms) == 1
        assert perms[0].detail["matched_keyword"] == "permission denied"

    def test_evidence_capped_at_five(self) -> None:
        # 7 permission-denied calls on same tool -> 1 signal, 5 evidence entries.
        calls = [_tc(tool="Write", res="permission denied", err=True) for _ in range(7)]
        trace = _trace(calls=calls)
        signals = extract_trace_signals(trace)
        perms = [s for s in signals if s.signal_type == SignalType.PERMISSION_FAILURE]
        assert len(perms) == 1
        assert len(perms[0].detail["tool_calls"]) == 5  # type: ignore[arg-type]

    def test_groups_by_tool(self) -> None:
        # Two tools each denied -> two PERMISSION_FAILURE signals.
        trace = _trace(
            calls=[
                _tc(tool="Write", res="permission denied", err=True),
                _tc(tool="Bash", res="blocked by hook", err=True),
            ],
        )
        signals = extract_trace_signals(trace)
        perms = [s for s in signals if s.signal_type == SignalType.PERMISSION_FAILURE]
        assert len(perms) == 2
        tools = {s.detail["tool_name"] for s in perms}
        assert tools == {"Write", "Bash"}


class TestErrorSequences:
    def test_single_error_no_signal(self) -> None:
        trace = _trace(calls=[_tc(err=True)])
        signals = extract_trace_signals(trace)
        assert not any(s.signal_type == SignalType.TOOL_ERROR_SEQUENCE for s in signals)

    def test_two_consecutive_is_warning(self) -> None:
        trace = _trace(calls=[_tc(err=True), _tc(err=True)])
        signals = extract_trace_signals(trace)
        seqs = [s for s in signals if s.signal_type == SignalType.TOOL_ERROR_SEQUENCE]
        assert len(seqs) == 1
        assert seqs[0].severity == Severity.WARNING
        assert seqs[0].detail["error_count"] == 2

    def test_four_consecutive_is_critical(self) -> None:
        trace = _trace(calls=[_tc(err=True) for _ in range(4)])
        signals = extract_trace_signals(trace)
        seqs = [s for s in signals if s.signal_type == SignalType.TOOL_ERROR_SEQUENCE]
        assert len(seqs) == 1
        assert seqs[0].severity == Severity.CRITICAL
        assert seqs[0].detail["error_count"] == 4

    def test_non_consecutive_errors_no_signal(self) -> None:
        trace = _trace(
            calls=[_tc(err=True), _tc(err=False), _tc(err=True)],
        )
        signals = extract_trace_signals(trace)
        seqs = [s for s in signals if s.signal_type == SignalType.TOOL_ERROR_SEQUENCE]
        assert seqs == []

    def test_start_end_indices(self) -> None:
        trace = _trace(
            calls=[_tc(), _tc(err=True), _tc(err=True), _tc(err=True), _tc()],
        )
        signals = extract_trace_signals(trace)
        seqs = [s for s in signals if s.signal_type == SignalType.TOOL_ERROR_SEQUENCE]
        assert seqs[0].detail["start_index"] == 1
        assert seqs[0].detail["end_index"] == 3


class TestRetryLoop:
    def test_attempts_two_not_emitted(self) -> None:
        trace = _trace(
            calls=[_tc(err=True), _tc(err=True)],
            sequences=[_rs(attempts=2, indices=[0, 1])],
        )
        signals = extract_trace_signals(trace)
        assert not any(s.signal_type == SignalType.RETRY_LOOP for s in signals)

    def test_attempts_three_varied_inputs_emits_retry(self) -> None:
        trace = _trace(
            calls=[
                _tc(inp="edit line 40", err=True),
                _tc(inp="edit line 41", err=True),
                _tc(inp="edit line 42", err=True),
            ],
            sequences=[
                _rs(attempts=3, indices=[0, 1, 2], first_err="Parse error"),
            ],
        )
        signals = extract_trace_signals(trace)
        loops = [s for s in signals if s.signal_type == SignalType.RETRY_LOOP]
        assert len(loops) == 1
        assert loops[0].severity == Severity.WARNING
        assert loops[0].detail["retry_count"] == 3
        assert loops[0].detail["first_error_message"] == "Parse error"

    def test_first_error_message_propagates(self) -> None:
        trace = _trace(
            calls=[_tc(err=True), _tc(err=True), _tc(err=True)],
            sequences=[_rs(attempts=3, first_err="connection timeout")],
        )
        signals = extract_trace_signals(trace)
        loops = [s for s in signals if s.signal_type == SignalType.RETRY_LOOP]
        assert loops[0].detail["first_error_message"] == "connection timeout"


class TestStuckPattern:
    def test_three_identical_is_retry_not_stuck(self) -> None:
        # attempts=3 is below STUCK threshold (>=4) regardless of identical inputs.
        trace = _trace(
            calls=[_tc(inp="ls") for _ in range(3)],
            sequences=[_rs(attempts=3)],
        )
        signals = extract_trace_signals(trace)
        types = {s.signal_type for s in signals}
        assert SignalType.RETRY_LOOP in types
        assert SignalType.STUCK_PATTERN not in types

    def test_four_identical_is_stuck(self) -> None:
        trace = _trace(
            calls=[_tc(inp="ls /missing") for _ in range(4)],
            sequences=[_rs(attempts=4)],
        )
        signals = extract_trace_signals(trace)
        stucks = [s for s in signals if s.signal_type == SignalType.STUCK_PATTERN]
        assert len(stucks) == 1
        assert stucks[0].severity == Severity.CRITICAL
        assert stucks[0].detail["stuck_count"] == 4
        assert stucks[0].detail["input_summary"] == "ls /missing"

    def test_four_near_identical_is_retry_not_stuck(self) -> None:
        # One-char drift — passes RETRY's 0.80 similarity but fails STUCK's
        # exact-match requirement.
        trace = _trace(
            calls=[_tc(inp=f"ls /tmp/{i}") for i in range(4)],
            sequences=[_rs(attempts=4)],
        )
        signals = extract_trace_signals(trace)
        types = {s.signal_type for s in signals}
        assert SignalType.RETRY_LOOP in types
        assert SignalType.STUCK_PATTERN not in types


class TestMutualExclusion:
    def test_stuck_does_not_also_emit_retry(self) -> None:
        trace = _trace(
            calls=[_tc(inp="stuck") for _ in range(5)],
            sequences=[_rs(attempts=5)],
        )
        signals = extract_trace_signals(trace)
        types = [s.signal_type for s in signals]
        assert SignalType.STUCK_PATTERN in types
        assert SignalType.RETRY_LOOP not in types

    def test_tool_error_sequence_not_emitted_on_covered_indices(self) -> None:
        # 4 consecutive errors, all covered by a STUCK sequence — no
        # TOOL_ERROR_SEQUENCE should emit on top of STUCK.
        trace = _trace(
            calls=[_tc(inp="x", err=True) for _ in range(4)],
            sequences=[_rs(attempts=4)],
        )
        signals = extract_trace_signals(trace)
        types = {s.signal_type for s in signals}
        assert SignalType.STUCK_PATTERN in types
        assert SignalType.TOOL_ERROR_SEQUENCE not in types

    def test_tool_error_sequence_emits_on_uncovered_errors(self) -> None:
        # Two uncovered errors followed by a STUCK run: uncovered tail
        # still triggers TOOL_ERROR_SEQUENCE for its own indices.
        trace = _trace(
            calls=[
                _tc(tool="Read", err=True),
                _tc(tool="Read", err=True),
                _tc(inp="stuck", err=True),
                _tc(inp="stuck", err=True),
                _tc(inp="stuck", err=True),
                _tc(inp="stuck", err=True),
            ],
            sequences=[_rs(attempts=4, indices=[2, 3, 4, 5])],
        )
        signals = extract_trace_signals(trace)
        types = {s.signal_type for s in signals}
        assert SignalType.STUCK_PATTERN in types
        assert SignalType.TOOL_ERROR_SEQUENCE in types
        seqs = [s for s in signals if s.signal_type == SignalType.TOOL_ERROR_SEQUENCE]
        # Only the uncovered [0, 1] run should be reported.
        assert seqs[0].detail["error_count"] == 2
