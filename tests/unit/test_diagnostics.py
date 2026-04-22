"""Tests for the run_diagnostics pipeline wiring."""

from agentfluent.agents.models import AgentInvocation
from agentfluent.diagnostics import run_diagnostics
from agentfluent.diagnostics.models import SignalType
from agentfluent.traces.models import (
    UNKNOWN_AGENT_TYPE,
    RetrySequence,
    SubagentToolCall,
    SubagentTrace,
)


def _inv(
    agent_type: str = "pm",
    trace: SubagentTrace | None = None,
) -> AgentInvocation:
    return AgentInvocation(
        agent_type=agent_type,
        description="test",
        prompt="do something",
        tool_use_id="toolu_01",
        output_text="",
        trace=trace,
    )


def _stuck_trace(agent_type: str = "pm") -> SubagentTrace:
    """Trace with 5 identical Bash calls -> STUCK_PATTERN."""
    calls = [
        SubagentToolCall(
            tool_name="Bash",
            input_summary="ls /missing",
            result_summary="not found",
            is_error=True,
        )
        for _ in range(5)
    ]
    return SubagentTrace(
        agent_id="agent-x",
        agent_type=agent_type,
        delegation_prompt="find something",
        tool_calls=calls,
        retry_sequences=[
            RetrySequence(
                tool_name="Bash",
                attempts=5,
                tool_call_indices=[0, 1, 2, 3, 4],
                first_error_message="not found",
                last_error_message="not found",
                eventual_success=False,
            ),
        ],
    )


class TestRunDiagnosticsWiring:
    def test_invocation_with_trace_produces_trace_signals(self) -> None:
        inv = _inv(trace=_stuck_trace())
        result = run_diagnostics([inv])
        trace_signals = [
            s for s in result.signals if s.signal_type == SignalType.STUCK_PATTERN
        ]
        assert len(trace_signals) == 1
        assert trace_signals[0].agent_type == "pm"
        # Recommendation should also appear.
        assert any(
            r.signal_types == [SignalType.STUCK_PATTERN] for r in result.recommendations
        )

    def test_invocation_without_trace_emits_no_trace_signals(self) -> None:
        inv = _inv(trace=None)
        result = run_diagnostics([inv])
        trace_types = {
            SignalType.TOOL_ERROR_SEQUENCE,
            SignalType.RETRY_LOOP,
            SignalType.PERMISSION_FAILURE,
            SignalType.STUCK_PATTERN,
        }
        assert not any(s.signal_type in trace_types for s in result.signals)

    def test_unknown_agent_type_on_trace_signal_is_overwritten(self) -> None:
        # Trace.agent_type stays at UNKNOWN (simulating an unlinked trace
        # that was still attached somehow). run_diagnostics should
        # overwrite the signal's agent_type from the parent invocation.
        trace = _stuck_trace(agent_type=UNKNOWN_AGENT_TYPE)
        inv = _inv(agent_type="architect", trace=trace)
        result = run_diagnostics([inv])
        stuck = [
            s for s in result.signals if s.signal_type == SignalType.STUCK_PATTERN
        ]
        assert len(stuck) == 1
        assert stuck[0].agent_type == "architect"
