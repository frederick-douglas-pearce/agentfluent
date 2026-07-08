"""Tests for behavior signal extraction."""

import pytest

from agentfluent.agents.models import AgentInvocation
from agentfluent.config.models import Severity
from agentfluent.diagnostics.models import SignalType
from agentfluent.diagnostics.signals import (
    FILE_READING_TOOLS,
    detect_is_error_for_tool,
    detect_is_error_from_text,
    extract_signals,
)


def _inv(
    agent_type: str = "pm",
    output_text: str = "",
    total_tokens: int | None = None,
    tool_uses: int | None = None,
    duration_ms: int | None = None,
    tool_use_id: str = "toolu_01",
    agent_id: str | None = None,
    with_trace: bool = False,
) -> AgentInvocation:
    inv = AgentInvocation(
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
    if with_trace:
        from agentfluent.traces.models import SubagentTrace

        inv.trace = SubagentTrace(
            agent_id=agent_id or "test-trace",
            agent_type=agent_type,
            delegation_prompt="x",
            duration_ms=duration_ms,
            idle_gap_ms=None,
        )
    return inv


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

    def test_mid_text_keyword_beyond_window_does_not_fire(self) -> None:
        """#281: ``_extract_error_signals`` only scans the leading
        ``ERROR_DETECTION_WINDOW_CHARS`` of ``output_text``. Successful
        agent outputs that mention error-handling topics mid-text
        (issue titles, code identifiers like ``tool_error_sequence``)
        no longer surface as ``ERROR_PATTERN`` signals."""
        # Long benign prefix (well past the 200-char window), then a
        # keyword. Pre-fix this fired a signal; post-fix it does not.
        text = (
            "The implementation completed cleanly with all tests "
            "passing. " * 30  # ~900 chars of benign prose, no keywords
        ) + "but a permission denied marker appears here mid-text."
        assert len(text) > 500
        invocations = [_inv(output_text=text)]
        signals = extract_signals(invocations)
        error_signals = [
            s for s in signals if s.signal_type == SignalType.ERROR_PATTERN
        ]
        assert error_signals == []

    def test_leading_keyword_within_window_still_fires(self) -> None:
        """Real error reports tend to lead the response — the bounded
        scan must still catch them."""
        text = (
            "Permission denied: cannot read /etc/shadow.\n\n"
            + ("Retried with elevated permissions and now everything works. "
               * 20)  # long trailing prose, irrelevant
        )
        invocations = [_inv(output_text=text)]
        signals = extract_signals(invocations)
        error_signals = [
            s for s in signals if s.signal_type == SignalType.ERROR_PATTERN
        ]
        assert any(s.detail["keyword"] == "permission denied" for s in error_signals)

    def test_window_boundary_keyword_just_inside_fires(self) -> None:
        """Keyword ending right at the window boundary fires; just past
        it does not. Pins the boundary semantics."""
        from agentfluent.diagnostics.signals import ERROR_DETECTION_WINDOW_CHARS
        # "failed" ends exactly at position 200 (within window).
        kw = "failed"
        text = "x " * ((ERROR_DETECTION_WINDOW_CHARS - len(kw)) // 2) + kw
        assert len(text) <= ERROR_DETECTION_WINDOW_CHARS
        signals = extract_signals([_inv(output_text=text)])
        assert any(
            s.detail["keyword"] == "failed"
            for s in signals if s.signal_type == SignalType.ERROR_PATTERN
        )
        # Same keyword pushed just past the window: no signal.
        text_just_past = "x" * ERROR_DETECTION_WINDOW_CHARS + " failed"
        signals = extract_signals([_inv(output_text=text_just_past)])
        assert not any(
            s.signal_type == SignalType.ERROR_PATTERN for s in signals
        )


class TestTokenOutlierDetection:
    """IQR-based detection (#186 P2): val > Q3 + 1.5*IQR. Requires
    OUTLIER_MIN_SAMPLE invocations and IQR > 0 to fire."""

    def test_detects_outlier(self) -> None:
        # Spread of 50–130 tokens/use + one clear outlier at 1000.
        # Q1≈67.5, Q3≈122.5, IQR≈55, threshold≈205 — 1000 fires.
        invocations = [
            _inv(total_tokens=500 + 100 * i, tool_uses=10) for i in range(9)
        ] + [_inv(total_tokens=10_000, tool_uses=10)]
        signals = extract_signals(invocations)
        outliers = [s for s in signals if s.signal_type == SignalType.TOKEN_OUTLIER]
        assert len(outliers) == 1
        d = outliers[0].detail
        assert d["actual_value"] == 1000.0
        assert d["excess_iqrs"] > 1.5  # by definition of the threshold
        assert {"q3_value", "iqr_value", "median_value", "p95_value", "threshold_value"} <= d.keys()

    def test_no_outlier_when_similar(self) -> None:
        invocations = [_inv(total_tokens=1000 + 50 * i, tool_uses=10) for i in range(10)]
        signals = extract_signals(invocations)
        outliers = [s for s in signals if s.signal_type == SignalType.TOKEN_OUTLIER]
        assert len(outliers) == 0

    def test_below_min_sample_skipped(self) -> None:
        # IQR detection needs OUTLIER_MIN_SAMPLE (= 4). At n=3 the rule
        # mathematically applies but the architect review bounded
        # detection at n>=4 for stability.
        invocations = [
            _inv(total_tokens=100, tool_uses=10),
            _inv(total_tokens=100, tool_uses=10),
            _inv(total_tokens=10_000, tool_uses=10),
        ]
        signals = extract_signals(invocations)
        outliers = [s for s in signals if s.signal_type == SignalType.TOKEN_OUTLIER]
        assert len(outliers) == 0

    def test_zero_iqr_skipped(self) -> None:
        # All values identical → IQR=0 → can't establish outlier.
        invocations = [_inv(total_tokens=1000, tool_uses=10) for _ in range(8)]
        signals = extract_signals(invocations)
        outliers = [s for s in signals if s.signal_type == SignalType.TOKEN_OUTLIER]
        assert len(outliers) == 0

    def test_no_metadata_skipped(self) -> None:
        invocations = [_inv(total_tokens=None, tool_uses=None) for _ in range(5)]
        signals = extract_signals(invocations)
        outliers = [s for s in signals if s.signal_type == SignalType.TOKEN_OUTLIER]
        assert len(outliers) == 0


class TestDurationOutlierDetection:
    def test_detects_outlier(self) -> None:
        # Spread of 1000–1800 ms/use + one outlier at 50000.
        invocations = [
            _inv(duration_ms=10_000 + 1000 * i, tool_uses=10, with_trace=True)
            for i in range(9)
        ] + [_inv(duration_ms=500_000, tool_uses=10, with_trace=True)]
        signals = extract_signals(invocations)
        outliers = [s for s in signals if s.signal_type == SignalType.DURATION_OUTLIER]
        assert len(outliers) == 1

    def test_single_invocation_skipped(self) -> None:
        invocations = [_inv(duration_ms=50000, tool_uses=10, with_trace=True)]
        signals = extract_signals(invocations)
        outliers = [s for s in signals if s.signal_type == SignalType.DURATION_OUTLIER]
        assert len(outliers) == 0

    def test_no_trace_invocations_excluded(self) -> None:
        # #453: no-trace invocations have duration_reliable=False and
        # must not produce duration_outlier signals (would false-positive
        # because wall-clock duration silently includes user-wait time).
        invocations = [
            _inv(duration_ms=10_000 + 1000 * i, tool_uses=10, with_trace=True)
            for i in range(9)
        ] + [_inv(duration_ms=500_000, tool_uses=10, with_trace=False)]
        signals = extract_signals(invocations)
        outliers = [s for s in signals if s.signal_type == SignalType.DURATION_OUTLIER]
        assert outliers == []

    def test_uses_active_duration_when_trace_present(self) -> None:
        # #230: outlier detection compares active_duration_per_tool_use.
        # Wall-clock 500_000ms would flag as an IQR-outlier; active
        # duration of ~10_000ms (rest is idle wait) puts it in line.
        from agentfluent.traces.models import SubagentTrace

        peers = [
            _inv(duration_ms=10_000 + 1000 * i, tool_uses=10, agent_id=f"ag-{i}")
            for i in range(9)
        ]
        slow_wall = _inv(duration_ms=500_000, tool_uses=10, agent_id="ag-slow")
        slow_wall.trace = SubagentTrace(
            agent_id="t-slow",
            agent_type="pm",
            delegation_prompt="x",
            duration_ms=500_000,
            idle_gap_ms=490_000,  # active = 10_000ms, in line with peers
        )

        signals = extract_signals([*peers, slow_wall])
        outliers = [s for s in signals if s.signal_type == SignalType.DURATION_OUTLIER]
        assert outliers == []


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
        peers = [
            _inv(total_tokens=1000 + 100 * i, tool_uses=10, agent_id=f"ag-{i}")
            for i in range(9)
        ]
        outlier_inv = _inv(total_tokens=100_000, tool_uses=10, agent_id="ag-spike")
        signals = extract_signals([*peers, outlier_inv])
        outlier = next(s for s in signals if s.signal_type == SignalType.TOKEN_OUTLIER)
        assert outlier.invocation_id == "ag-spike"

    def test_duration_outlier_signal_carries_invocation_id(self) -> None:
        peers = [
            _inv(
                duration_ms=1000 + 100 * i, tool_uses=10,
                agent_id=f"ag-{i}", with_trace=True,
            )
            for i in range(9)
        ]
        slow = _inv(
            duration_ms=100_000, tool_uses=10,
            agent_id="ag-slow", with_trace=True,
        )
        signals = extract_signals([*peers, slow])
        outlier = next(s for s in signals if s.signal_type == SignalType.DURATION_OUTLIER)
        assert outlier.invocation_id == "ag-slow"


# The 15 genuine first-attempt failures from the v0.10 dogfood (#580): each
# renders leading with a structured error signature. All must still synthesize
# is_error=True on a file-reading tool.
_GENUINE_FIRES = [
    "<tool_use_error>InputValidationError: offset must be a number, got array",
    "<tool_use_error>InputValidationError: missing required parameter 'pattern'",
    "EISDIR: illegal operation on a directory, read",
    "File does not exist.",
    "File does not exist. Did you mean src/agentfluent/traces/parser.py?",
    "File content (28451 tokens) exceeds maximum allowed tokens (25000). "
    "Please use offset and limit to read specific portions.",
]

# The 10 self-referential false positives (#580): successful Read/Grep of
# AgentFluent's own error-handling source, whose head carries error vocabulary
# but does NOT lead with a structured signature (a grep hit line leads with the
# filename; a Read leads with a line-number or a docstring/keyword). None may
# synthesize is_error=True on a file-reading tool.
_CONTENT_FPS = [
    # architect -> Grep hit: leads with the filename, not a signature.
    "parser.py:33:from agentfluent.diagnostics.signals import "
    "detect_is_error_from_text",
    "signals.py:44:# Real error messages lead with the indicator",
    # Explore -> Read of a class whose docstring names the signal.
    'class ParameterRetryRule:\n    """PARAMETER_RETRY -> recommend input_examples',
    # candidate-verifier -> Read of a module docstring about error detection.
    '"""Behavior signal extraction from agent invocations.\n\n'
    "Detects error patterns in output text",
    # architect -> Read of a function definition.
    "def compute_error_rate(invocations: list[AgentInvocation]) -> float:",
    # architect -> Read of the release-loop skill frontmatter (line-numbered).
    "1\t---\n2\tname: release-loop\n3\tdescription: Run one routed iteration",
    # pm -> Read of the run_diagnostics pipeline docstring (x2 in corpus).
    '"""Run the diagnostics pipeline: signals -> correlation -> recommendations."""',
    "run_diagnostics wires the error-pattern signal into the pipeline",
    # A Read whose first line mentions an errno mid-line (not leading).
    "The parser maps EISDIR and ENOENT to is_error in traces/parser.py",
    # A Grep hit whose content line contains the literal wrapper string.
    "signals.py:52:    r\"<tool_use_error>|EISDIR|ENOENT|EACCES\"",
]


class TestDetectIsErrorForTool:
    """#580: tool-aware is_error synthesis anchored to a leading signature."""

    @pytest.mark.parametrize("tool", sorted(FILE_READING_TOOLS))
    @pytest.mark.parametrize("text", _GENUINE_FIRES)
    def test_genuine_fires_still_fire_on_file_reading_tools(
        self, tool: str, text: str,
    ) -> None:
        assert detect_is_error_for_tool(text, tool) is True

    @pytest.mark.parametrize("tool", sorted(FILE_READING_TOOLS))
    @pytest.mark.parametrize("text", _CONTENT_FPS)
    def test_content_fps_suppressed_on_file_reading_tools(
        self, tool: str, text: str,
    ) -> None:
        assert detect_is_error_for_tool(text, tool) is False

    def test_leading_whitespace_before_signature_still_fires(self) -> None:
        assert detect_is_error_for_tool("   \n  EISDIR: is a directory", "Read") is True

    def test_signature_is_case_sensitive(self) -> None:
        # Lowercase errno is source prose, not a system error string.
        assert detect_is_error_for_tool("eisdir mentioned in a comment", "Read") is False

    def test_non_file_reading_tool_keeps_windowed_behavior(self) -> None:
        # Bash delegates to detect_is_error_from_text: a leading generic keyword
        # still fires (the file-reading anchor does not apply).
        assert detect_is_error_for_tool("Error: command not found", "Bash") is True
        assert (
            detect_is_error_for_tool("Error: command not found", "Bash")
            == detect_is_error_from_text("Error: command not found")
        )

    def test_non_file_reading_mid_keyword_matches_windowed(self) -> None:
        text = "wrote output; the word failed appears here"
        assert detect_is_error_for_tool(text, "Bash") == detect_is_error_from_text(text)

    def test_generic_keyword_leading_on_read_does_not_fire(self) -> None:
        # The core #580 regression: "error"/"failed" at the top of a Read is
        # source content, not a tool failure.
        assert detect_is_error_for_tool("error handling utilities", "Read") is False
        assert detect_is_error_for_tool("failed attempts are logged", "Grep") is False

    def test_none_and_empty_return_false(self) -> None:
        assert detect_is_error_for_tool(None, "Read") is False
        assert detect_is_error_for_tool("", "Read") is False
        assert detect_is_error_for_tool("   ", "Read") is False

