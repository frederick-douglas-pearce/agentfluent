"""Verbose-mode distribution context on outlier signals.

Verbose mode appends ``[median=..., P95=..., threshold=...]`` to
TOKEN_OUTLIER and DURATION_OUTLIER messages so users can place the
flagged value in the underlying distribution. Non-verbose output
stays unchanged. Other signal types pass through in both modes —
their ``detail`` dicts don't carry distribution stats.
"""

from __future__ import annotations

from rich.console import Console

from agentfluent.cli.formatters.table import (
    _format_diagnostics_table,
    _verbose_signal_message,
)
from agentfluent.config.models import Severity
from agentfluent.diagnostics.models import (
    DiagnosticSignal,
    DiagnosticsResult,
    SignalType,
)


def _outlier_signal(
    *,
    signal_type: SignalType,
    actual: float,
    median: float,
    q3: float,
    iqr: float,
    p95: float,
    threshold: float,
) -> DiagnosticSignal:
    excess = (actual - q3) / iqr
    return DiagnosticSignal(
        signal_type=signal_type,
        severity=Severity.WARNING,
        agent_type="pm",
        message=f"Agent 'pm' has {actual:,.0f} units, {excess:.1f}×IQR above Q3.",
        detail={
            "actual_value": actual,
            "median_value": median,
            "q3_value": q3,
            "iqr_value": iqr,
            "p95_value": p95,
            "threshold_value": threshold,
            "excess_iqrs": round(excess, 2),
        },
    )


def _render_signals(diag: DiagnosticsResult, *, verbose: bool) -> str:
    console = Console(record=True, width=240, force_terminal=False)
    _format_diagnostics_table(console, diag, verbose=verbose)
    return console.export_text()


class TestVerboseSignalMessage:
    def test_token_outlier_verbose_appends_distribution(self) -> None:
        sig = _outlier_signal(
            signal_type=SignalType.TOKEN_OUTLIER,
            actual=21_903,
            median=4_500,
            q3=7_763,
            iqr=4_726,
            p95=20_000,
            threshold=14_852,
        )
        msg = _verbose_signal_message(sig)
        assert "Agent 'pm' has 21,903 units" in msg
        assert "median=4,500" in msg
        assert "P95=20,000" in msg
        assert "threshold=14,852" in msg

    def test_duration_outlier_verbose_uses_seconds(self) -> None:
        sig = _outlier_signal(
            signal_type=SignalType.DURATION_OUTLIER,
            actual=54_619,
            median=15_000,
            q3=21_286,
            iqr=13_763,
            p95=50_000,
            threshold=41_931,
        )
        msg = _verbose_signal_message(sig)
        assert "median=15.0s" in msg
        assert "P95=50.0s" in msg
        assert "threshold=41.9s" in msg

    def test_non_outlier_passes_through(self) -> None:
        sig = DiagnosticSignal(
            signal_type=SignalType.RETRY_LOOP,
            severity=Severity.WARNING,
            agent_type="Explore",
            message="Subagent 'Explore' retried Read 5 times.",
            detail={"tool_name": "Read", "attempts": 5},
        )
        assert _verbose_signal_message(sig) == sig.message

    def test_outlier_missing_detail_fields_passes_through(self) -> None:
        # Edge case: an outlier signal with an incomplete detail dict
        # (e.g., constructed by older code, mocked test, or a future
        # detector that doesn't carry distribution stats) shouldn't
        # crash — fall back to the original message.
        sig = DiagnosticSignal(
            signal_type=SignalType.TOKEN_OUTLIER,
            severity=Severity.WARNING,
            agent_type="pm",
            message="Agent 'pm' has 100 tokens, X×IQR above Q3.",
            detail={"actual_value": 100},
        )
        assert _verbose_signal_message(sig) == sig.message


class TestDiagnosticsTableRendering:
    def test_verbose_renders_distribution_context_in_signals_table(self) -> None:
        sig = _outlier_signal(
            signal_type=SignalType.TOKEN_OUTLIER,
            actual=21_903,
            median=4_500,
            q3=7_763,
            iqr=4_726,
            p95=20_000,
            threshold=14_852,
        )
        diag = DiagnosticsResult(signals=[sig])
        verbose_output = _render_signals(diag, verbose=True)
        assert "median=4,500" in verbose_output
        assert "P95=20,000" in verbose_output
        assert "threshold=14,852" in verbose_output

    def test_non_verbose_omits_distribution_context(self) -> None:
        sig = _outlier_signal(
            signal_type=SignalType.TOKEN_OUTLIER,
            actual=21_903,
            median=4_500,
            q3=7_763,
            iqr=4_726,
            p95=20_000,
            threshold=14_852,
        )
        diag = DiagnosticsResult(signals=[sig])
        plain_output = _render_signals(diag, verbose=False)
        assert "median=" not in plain_output
        assert "P95=" not in plain_output
        assert "21,903 units" in plain_output
