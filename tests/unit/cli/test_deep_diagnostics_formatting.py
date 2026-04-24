"""Tests for the Deep Diagnostics section of the analyze formatter."""

from __future__ import annotations

from rich.console import Console

from agentfluent.cli.formatters.table import (
    _format_deep_diagnostics,
    _format_delegation_suggestions,
)
from agentfluent.config.models import Severity
from agentfluent.diagnostics.models import (
    DelegationSuggestion,
    DiagnosticSignal,
    DiagnosticsResult,
    SignalType,
)
from tests._builders import delegation_suggestion as _suggestion


def _trace_signal(
    signal_type: SignalType = SignalType.STUCK_PATTERN,
    agent_type: str = "pm",
    detail: dict[str, object] | None = None,
) -> DiagnosticSignal:
    return DiagnosticSignal(
        signal_type=signal_type,
        severity=Severity.CRITICAL,
        agent_type=agent_type,
        message=f"{signal_type.value} on {agent_type}",
        detail=detail if detail is not None else {
            "tool_calls": [
                {
                    "index": 0,
                    "tool_name": "Bash",
                    "input_summary": "ls /missing",
                    "result_summary": "not found",
                    "is_error": True,
                },
            ],
            "stuck_count": 5,
        },
    )


def _result(signals: list[DiagnosticSignal]) -> DiagnosticsResult:
    return DiagnosticsResult(signals=signals, recommendations=[])


def _render(diag: DiagnosticsResult, *, verbose: bool) -> str:
    console = Console(record=True, width=120)
    _format_deep_diagnostics(console, diag, verbose=verbose)
    return console.export_text()


class TestDeepDiagnosticsSection:
    def test_absent_when_no_trace_signals(self) -> None:
        # Only a metadata-level signal — no Deep Diagnostics output.
        meta_signal = DiagnosticSignal(
            signal_type=SignalType.ERROR_PATTERN,
            severity=Severity.WARNING,
            agent_type="pm",
            message="error",
            detail={"keyword": "error"},
        )
        out = _render(_result([meta_signal]), verbose=False)
        assert "Deep Diagnostics" not in out

    def test_compact_summary_by_default(self) -> None:
        out = _render(_result([_trace_signal()]), verbose=False)
        assert "Deep Diagnostics" in out
        assert "1 trace signal" in out
        assert "--verbose" in out

    def test_compact_summary_counts_unique_agents(self) -> None:
        signals = [
            _trace_signal(agent_type="pm"),
            _trace_signal(agent_type="pm"),
            _trace_signal(agent_type="architect"),
        ]
        out = _render(_result(signals), verbose=False)
        assert "3 trace signal" in out
        assert "2 subagent" in out

    def test_verbose_renders_evidence_subtable(self) -> None:
        out = _render(_result([_trace_signal()]), verbose=True)
        assert "Deep Diagnostics" in out
        # Evidence row content should appear.
        assert "Bash" in out
        assert "ls /missing" in out
        # No "--verbose" hint when already verbose.
        assert "--verbose" not in out

    def test_verbose_renders_multiple_signals(self) -> None:
        signals = [
            _trace_signal(signal_type=SignalType.STUCK_PATTERN, agent_type="pm"),
            _trace_signal(signal_type=SignalType.RETRY_LOOP, agent_type="architect"),
        ]
        out = _render(_result(signals), verbose=True)
        assert "stuck_pattern" in out
        assert "retry_loop" in out
        assert "pm" in out
        assert "architect" in out

    def test_verbose_handles_missing_evidence_gracefully(self) -> None:
        # A trace signal with no `tool_calls` in detail still renders the
        # header without crashing on the sub-table.
        sig = _trace_signal(detail={"stuck_count": 5})
        out = _render(_result([sig]), verbose=True)
        assert "stuck_pattern" in out


class TestMarkupInjection:
    """Untrusted JSONL content must not be interpreted as Rich markup.

    Trace data (tool results, subagent summaries) can contain attacker-
    crafted content like ``[link=https://evil]...[/link]``. If the
    formatter renders those strings verbatim, Rich interprets the tags
    and the user sees a phishable hyperlink. All user-data-derived
    strings must pass through ``rich.markup.escape``.
    """

    def test_agent_type_with_markup_is_escaped(self) -> None:
        sig = _trace_signal(agent_type="[link=https://evil]click[/link]")
        out = _render(_result([sig]), verbose=True)
        assert "[link=https://evil]" in out  # escaped form renders the tag literally
        assert "click" in out

    def test_message_with_markup_is_escaped(self) -> None:
        sig = DiagnosticSignal(
            signal_type=SignalType.STUCK_PATTERN,
            severity=Severity.CRITICAL,
            agent_type="pm",
            message="injected [bold red]CRITICAL FINDING[/bold red] warning",
            detail={},
        )
        out = _render(_result([sig]), verbose=True)
        # The bracketed tag must appear literally, not be consumed as markup.
        assert "[bold red]" in out
        assert "CRITICAL FINDING" in out

    def test_evidence_fields_with_markup_are_escaped(self) -> None:
        sig = _trace_signal(detail={
            "tool_calls": [
                {
                    "index": 0,
                    "tool_name": "Bash",
                    "input_summary": "[link=https://phish]go[/link]",
                    "result_summary": "[bold]danger[/bold]",
                    "is_error": True,
                },
            ],
            "stuck_count": 1,
        })
        out = _render(_result([sig]), verbose=True)
        assert "[link=https://phish]" in out
        assert "[bold]" in out


class TestJsonRoundTrip:
    def test_trace_signal_with_evidence_roundtrips(self) -> None:
        import json

        diag = _result([_trace_signal()])
        dumped = diag.model_dump(mode="json")
        # Re-parse to confirm it's JSON-serializable.
        restored = json.loads(json.dumps(dumped))
        assert restored["signals"][0]["signal_type"] == "stuck_pattern"
        assert restored["signals"][0]["detail"]["tool_calls"][0]["tool_name"] == "Bash"
        assert restored["signals"][0]["detail"]["stuck_count"] == 5


def _result_with_suggestions(
    suggestions: list[DelegationSuggestion],
) -> DiagnosticsResult:
    return DiagnosticsResult(delegation_suggestions=suggestions)


def _render_suggestions(diag: DiagnosticsResult, *, verbose: bool) -> str:
    console = Console(record=True, width=140)
    _format_delegation_suggestions(console, diag, verbose=verbose)
    return console.export_text()


class TestDelegationSuggestionsSection:
    def test_absent_when_no_suggestions(self) -> None:
        out = _render_suggestions(_result_with_suggestions([]), verbose=False)
        assert "Suggested Subagents" not in out

    def test_table_rendered_when_suggestions_present(self) -> None:
        out = _render_suggestions(
            _result_with_suggestions([_suggestion()]), verbose=False,
        )
        assert "Suggested Subagents" in out
        assert "test-runner" in out
        assert "claude-sonnet-4-6" in out

    def test_verbose_emits_yaml_subagent_draft(self) -> None:
        out = _render_suggestions(
            _result_with_suggestions([_suggestion()]), verbose=True,
        )
        assert "# Suggested agent: test-runner" in out
        assert "# Confidence: high" in out
        assert "description:" in out
        assert "model: claude-sonnet-4-6" in out
        assert "tools:" in out
        assert "- Read" in out
        assert "---" in out
        assert "You run pytest tests" in out
        assert "pytest" in out

    def test_verbose_low_confidence_includes_review_warning(self) -> None:
        out = _render_suggestions(
            _result_with_suggestions([_suggestion(confidence="low")]), verbose=True,
        )
        assert "REVIEW BEFORE USE" in out
        assert "# Confidence: low" in out

    def test_verbose_empty_tools_notes_reason(self) -> None:
        sug = _suggestion(tools=[], tools_note="no subagent traces linked")
        out = _render_suggestions(
            _result_with_suggestions([sug]), verbose=True,
        )
        assert "tools: []" in out
        assert "no subagent traces linked" in out

    def test_dedup_note_rendered(self) -> None:
        sug = _suggestion(dedup_note="suppressed — already covered by 'pm' (similarity 0.85)")
        out = _render_suggestions(
            _result_with_suggestions([sug]), verbose=False,
        )
        assert "suppressed" in out
        assert "pm" in out

    def test_tools_note_rendered_when_no_tools(self) -> None:
        sug = _suggestion(tools=[], tools_note="# run with newer session data")
        out = _render_suggestions(
            _result_with_suggestions([sug]), verbose=False,
        )
        assert "newer session data" in out


class TestDelegationMarkupInjection:
    """Trace-derived strings in delegation suggestions must be Rich-escaped,
    same contract as TestMarkupInjection above."""

    def test_name_with_markup_is_escaped(self) -> None:
        sug = _suggestion(name="[link=https://evil]click[/link]")
        out = _render_suggestions(
            _result_with_suggestions([sug]), verbose=False,
        )
        assert "[link=https://evil]" in out

    def test_dedup_note_with_markup_is_escaped(self) -> None:
        sug = _suggestion(dedup_note="[bold red]CRITICAL[/bold red]")
        out = _render_suggestions(
            _result_with_suggestions([sug]), verbose=False,
        )
        assert "[bold red]" in out
