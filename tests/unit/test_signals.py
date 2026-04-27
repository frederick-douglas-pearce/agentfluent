"""Tests for behavior signal extraction."""

from agentfluent.agents.models import AgentInvocation
from agentfluent.config.models import Severity
from agentfluent.diagnostics.models import SignalType
from agentfluent.diagnostics.signals import extract_signals


def _inv(
    agent_type: str = "pm",
    output_text: str = "",
    total_tokens: int | None = None,
    tool_uses: int | None = None,
    duration_ms: int | None = None,
    tool_use_id: str = "toolu_01",
    agent_id: str | None = None,
) -> AgentInvocation:
    return AgentInvocation(
        agent_type=agent_type,
        description="test",
        prompt="do something",
        tool_use_id=tool_use_id,
        total_tokens=total_tokens,
        tool_uses=tool_uses,
        duration_ms=duration_ms,
        output_text=output_text,
        agent_id=agent_id,
    )


class TestErrorPatternDetection:
    def test_detects_blocked(self) -> None:
        invocations = [_inv(output_text="The tool was blocked by permissions.")]
        signals = extract_signals(invocations)
        error_signals = [s for s in signals if s.signal_type == SignalType.ERROR_PATTERN]
        assert len(error_signals) == 1
        assert error_signals[0].severity == Severity.CRITICAL
        assert "blocked" in error_signals[0].detail["keyword"]

    def test_detects_permission_denied(self) -> None:
        invocations = [_inv(output_text="Permission denied when accessing file.")]
        signals = extract_signals(invocations)
        error_signals = [s for s in signals if s.signal_type == SignalType.ERROR_PATTERN]
        assert len(error_signals) == 1
        assert error_signals[0].severity == Severity.CRITICAL

    def test_detects_warning_keywords(self) -> None:
        invocations = [_inv(output_text="The operation failed with an error.")]
        signals = extract_signals(invocations)
        error_signals = [s for s in signals if s.signal_type == SignalType.ERROR_PATTERN]
        assert len(error_signals) >= 1
        assert all(s.severity == Severity.WARNING for s in error_signals)

    def test_no_error_keywords(self) -> None:
        invocations = [_inv(output_text="Everything completed successfully.")]
        signals = extract_signals(invocations)
        error_signals = [s for s in signals if s.signal_type == SignalType.ERROR_PATTERN]
        assert len(error_signals) == 0

    def test_empty_output(self) -> None:
        invocations = [_inv(output_text="")]
        signals = extract_signals(invocations)
        assert len(signals) == 0

    def test_multiple_keywords_in_one_output(self) -> None:
        invocations = [_inv(output_text="Failed to connect. Retry attempted but timed out.")]
        signals = extract_signals(invocations)
        error_signals = [s for s in signals if s.signal_type == SignalType.ERROR_PATTERN]
        assert len(error_signals) >= 2

    def test_snippet_context(self) -> None:
        text = "x" * 100 + "blocked" + "y" * 100
        invocations = [_inv(output_text=text)]
        signals = extract_signals(invocations)
        assert signals[0].detail["snippet"]


class TestTokenOutlierDetection:
    def test_detects_outlier(self) -> None:
        invocations = [
            _inv(total_tokens=1000, tool_uses=10),  # 100 tokens/use
            _inv(total_tokens=1000, tool_uses=10),  # 100 tokens/use
            _inv(total_tokens=5000, tool_uses=10),  # 500 tokens/use (5x mean ~233)
        ]
        signals = extract_signals(invocations)
        outliers = [s for s in signals if s.signal_type == SignalType.TOKEN_OUTLIER]
        assert len(outliers) >= 1
        assert outliers[0].detail["ratio"] > 2.0

    def test_no_outlier_when_similar(self) -> None:
        invocations = [
            _inv(total_tokens=1000, tool_uses=10),
            _inv(total_tokens=1100, tool_uses=10),
            _inv(total_tokens=900, tool_uses=10),
        ]
        signals = extract_signals(invocations)
        outliers = [s for s in signals if s.signal_type == SignalType.TOKEN_OUTLIER]
        assert len(outliers) == 0

    def test_single_invocation_skipped(self) -> None:
        invocations = [_inv(total_tokens=5000, tool_uses=10)]
        signals = extract_signals(invocations)
        outliers = [s for s in signals if s.signal_type == SignalType.TOKEN_OUTLIER]
        assert len(outliers) == 0

    def test_no_metadata_skipped(self) -> None:
        invocations = [
            _inv(total_tokens=None, tool_uses=None),
            _inv(total_tokens=None, tool_uses=None),
        ]
        signals = extract_signals(invocations)
        outliers = [s for s in signals if s.signal_type == SignalType.TOKEN_OUTLIER]
        assert len(outliers) == 0


class TestDurationOutlierDetection:
    def test_detects_outlier(self) -> None:
        invocations = [
            _inv(duration_ms=10000, tool_uses=10),  # 1000ms/use
            _inv(duration_ms=10000, tool_uses=10),  # 1000ms/use
            _inv(duration_ms=50000, tool_uses=10),  # 5000ms/use (5x mean ~2333)
        ]
        signals = extract_signals(invocations)
        outliers = [s for s in signals if s.signal_type == SignalType.DURATION_OUTLIER]
        assert len(outliers) >= 1

    def test_single_invocation_skipped(self) -> None:
        invocations = [_inv(duration_ms=50000, tool_uses=10)]
        signals = extract_signals(invocations)
        outliers = [s for s in signals if s.signal_type == SignalType.DURATION_OUTLIER]
        assert len(outliers) == 0


class TestExtractSignals:
    def test_empty_invocations(self) -> None:
        assert extract_signals([]) == []

    def test_mixed_signals(self) -> None:
        invocations = [
            _inv(output_text="Error occurred", total_tokens=1000, tool_uses=10),
            _inv(output_text="Success", total_tokens=1000, tool_uses=10),
            _inv(output_text="", total_tokens=5000, tool_uses=10),
        ]
        signals = extract_signals(invocations)
        types = {s.signal_type for s in signals}
        assert SignalType.ERROR_PATTERN in types


class TestInvocationIdPropagation:
    """#197: every per-invocation signal carries an invocation_id pointing
    back to the source AgentInvocation."""

    def test_error_pattern_uses_agent_id_when_present(self) -> None:
        invocations = [
            _inv(
                output_text="Operation blocked.",
                agent_id="ag-uuid-1",
                tool_use_id="toolu_99",
            ),
        ]
        signals = extract_signals(invocations)
        assert signals[0].invocation_id == "ag-uuid-1"

    def test_falls_back_to_tool_use_id_when_agent_id_missing(self) -> None:
        # Older sessions / interrupted runs lack agent_id; tool_use_id
        # is always populated and lets consumers locate the parent
        # tool_use block in the session JSONL.
        invocations = [
            _inv(
                output_text="Operation blocked.",
                agent_id=None,
                tool_use_id="toolu_42",
            ),
        ]
        signals = extract_signals(invocations)
        assert signals[0].invocation_id == "toolu_42"

    def test_token_outlier_signal_carries_invocation_id(self) -> None:
        invocations = [
            _inv(total_tokens=1000, tool_uses=10, agent_id="ag-1"),
            _inv(total_tokens=1000, tool_uses=10, agent_id="ag-2"),
            _inv(total_tokens=10000, tool_uses=10, agent_id="ag-3"),
        ]
        signals = extract_signals(invocations)
        outlier = next(s for s in signals if s.signal_type == SignalType.TOKEN_OUTLIER)
        # Outlier rule fires for ag-3 (10x the mean, well above 2x threshold).
        assert outlier.invocation_id == "ag-3"

    def test_duration_outlier_signal_carries_invocation_id(self) -> None:
        invocations = [
            _inv(duration_ms=1000, tool_uses=10, agent_id="ag-1"),
            _inv(duration_ms=1000, tool_uses=10, agent_id="ag-2"),
            _inv(duration_ms=10000, tool_uses=10, agent_id="ag-slow"),
        ]
        signals = extract_signals(invocations)
        outlier = next(s for s in signals if s.signal_type == SignalType.DURATION_OUTLIER)
        assert outlier.invocation_id == "ag-slow"

