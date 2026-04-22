"""Tests for the diagnostics orchestration pipeline.

Covers: metadata/trace signal dedup, subagent_trace_count semantics,
and backward compatibility of the public `run_diagnostics` import path.
"""

from agentfluent.agents.models import AgentInvocation
from agentfluent.diagnostics.models import SignalType
from agentfluent.diagnostics.pipeline import run_diagnostics
from agentfluent.traces.models import (
    RetrySequence,
    SubagentToolCall,
    SubagentTrace,
)


def _inv(
    agent_type: str = "pm",
    output_text: str = "",
    trace: SubagentTrace | None = None,
) -> AgentInvocation:
    return AgentInvocation(
        agent_type=agent_type,
        is_builtin=False,
        description="test",
        prompt="do something",
        tool_use_id="toolu_01",
        output_text=output_text,
        trace=trace,
    )


def _stuck_trace(agent_type: str = "pm") -> SubagentTrace:
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
        delegation_prompt="find",
        tool_calls=calls,
        retry_sequences=[
            RetrySequence(
                tool_name="Bash",
                attempts=5,
                tool_call_indices=[0, 1, 2, 3, 4],
                first_error_message="not found",
                eventual_success=False,
            ),
        ],
    )


class TestDedup:
    def test_metadata_error_pattern_suppressed_when_trace_signal_same_agent_type(
        self,
    ) -> None:
        inv = _inv(
            agent_type="pm",
            output_text="The operation failed with a permission denied error.",
            trace=_stuck_trace(agent_type="pm"),
        )
        result = run_diagnostics([inv])
        by_type = {s.signal_type for s in result.signals}
        assert SignalType.STUCK_PATTERN in by_type
        # ERROR_PATTERN for pm is suppressed.
        assert not any(
            s.signal_type == SignalType.ERROR_PATTERN and s.agent_type == "pm"
            for s in result.signals
        )

    def test_metadata_error_pattern_retained_for_other_agent_type(self) -> None:
        # agent A has a trace, agent B doesn't; B's metadata signals stay.
        inv_a = _inv(agent_type="pm", trace=_stuck_trace(agent_type="pm"))
        inv_b = _inv(agent_type="architect", output_text="permission denied")
        result = run_diagnostics([inv_a, inv_b])
        assert any(
            s.signal_type == SignalType.ERROR_PATTERN and s.agent_type == "architect"
            for s in result.signals
        )

    def test_no_trace_signals_all_metadata_retained(self) -> None:
        inv = _inv(output_text="failed to load the config")
        result = run_diagnostics([inv])
        error_signals = [
            s for s in result.signals if s.signal_type == SignalType.ERROR_PATTERN
        ]
        assert len(error_signals) >= 1

    def test_token_outlier_not_suppressed_by_trace_signal(self) -> None:
        # 'pm' invocations spread such that one clears the 2x-mean outlier
        # threshold; one of them also carries a trace. TOKEN_OUTLIER must
        # survive the dedup pass.
        inv_a = AgentInvocation(
            agent_type="pm", is_builtin=False, description="a", prompt="a",
            tool_use_id="t1", output_text="",
            total_tokens=100, tool_uses=1, trace=_stuck_trace(agent_type="pm"),
        )
        inv_b = AgentInvocation(
            agent_type="pm", is_builtin=False, description="b", prompt="b",
            tool_use_id="t2", output_text="",
            total_tokens=100, tool_uses=1,
        )
        inv_c = AgentInvocation(
            agent_type="pm", is_builtin=False, description="c", prompt="c",
            tool_use_id="t3", output_text="",
            total_tokens=10_000, tool_uses=1,
        )
        result = run_diagnostics([inv_a, inv_b, inv_c])
        assert any(s.signal_type == SignalType.TOKEN_OUTLIER for s in result.signals)
        assert any(s.signal_type == SignalType.STUCK_PATTERN for s in result.signals)

    def test_dedup_happens_before_correlation(self) -> None:
        # An ERROR_PATTERN "permission denied" signal normally triggers
        # AccessErrorRule. When suppressed by a trace signal, its
        # recommendation must also be absent.
        inv = _inv(
            agent_type="pm",
            output_text="permission denied when accessing file",
            trace=_stuck_trace(agent_type="pm"),
        )
        result = run_diagnostics([inv])
        # StuckPatternRule should produce a recommendation for the trace.
        stuck_recs = [
            r for r in result.recommendations
            if r.signal_types == [SignalType.STUCK_PATTERN]
        ]
        assert len(stuck_recs) == 1
        # AccessErrorRule's recommendation (from metadata ERROR_PATTERN)
        # should NOT appear.
        assert not any(
            r.signal_types == [SignalType.ERROR_PATTERN] and r.agent_type == "pm"
            for r in result.recommendations
        )


class TestSubagentTraceCount:
    def test_counts_parsed_linked_traces(self) -> None:
        inv_a = _inv(trace=_stuck_trace())
        inv_b = _inv(trace=_stuck_trace())
        result = run_diagnostics([inv_a, inv_b])
        assert result.subagent_trace_count == 2

    def test_zero_when_no_invocations(self) -> None:
        result = run_diagnostics([])
        assert result.subagent_trace_count == 0

    def test_mix_of_linked_and_unlinked(self) -> None:
        inv_linked = _inv(trace=_stuck_trace())
        inv_unlinked = _inv(trace=None)
        result = run_diagnostics([inv_linked, inv_unlinked])
        assert result.subagent_trace_count == 1


class TestBackwardCompatImport:
    def test_run_diagnostics_importable_from_diagnostics_package(self) -> None:
        from agentfluent.diagnostics import run_diagnostics as rd_from_pkg

        # Same callable as pipeline.run_diagnostics.
        assert rd_from_pkg is run_diagnostics

    def test_trace_signal_types_exported(self) -> None:
        from agentfluent.diagnostics import TRACE_SIGNAL_TYPES

        assert SignalType.STUCK_PATTERN in TRACE_SIGNAL_TYPES
        assert SignalType.ERROR_PATTERN not in TRACE_SIGNAL_TYPES
