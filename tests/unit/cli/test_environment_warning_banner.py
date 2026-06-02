"""The table formatter must surface ``AnalysisResult.warnings`` as a banner.

The cleanupPeriodDays retention warning (#481) is an environment-level
signal: it's attached to ``AnalysisResult.warnings`` at discovery time
and must render above the metrics it may have bounded, in both the full
and summary table paths. JSON consumers read the warnings from the
envelope instead; that path must not emit the banner.
"""

from __future__ import annotations

from io import StringIO
from pathlib import Path

from rich.console import Console

from agentfluent.analytics.agent_metrics import AgentMetrics
from agentfluent.analytics.pipeline import AnalysisResult
from agentfluent.analytics.tokens import TokenMetrics
from agentfluent.analytics.tools import ToolMetrics
from agentfluent.cli.formatters.table import format_analysis_table
from agentfluent.config.models import EnvironmentWarning, Severity


def _result(*, warnings: list[EnvironmentWarning] | None = None) -> AnalysisResult:
    return AnalysisResult(
        token_metrics=TokenMetrics(),
        tool_metrics=ToolMetrics(),
        agent_metrics=AgentMetrics(),
        session_count=1,
        warnings=warnings or [],
    )


def _warning() -> EnvironmentWarning:
    return EnvironmentWarning(
        code="cleanup_period_truncation",
        severity=Severity.WARNING,
        message="Claude Code `cleanupPeriodDays` is 30 days. Sessions older "
        "than 30 days have been deleted.",
        remediation_path=Path("/home/u/.claude/settings.json"),
    )


def _render(result: AnalysisResult, *, show_diagnostics: bool = False) -> str:
    buf = StringIO()
    format_analysis_table(
        Console(file=buf, width=200, force_terminal=False),
        result,
        verbose=False,
        show_diagnostics=show_diagnostics,
    )
    return buf.getvalue()


def test_banner_renders_when_warning_present() -> None:
    out = _render(_result(warnings=[_warning()]))
    assert "⚠" in out
    assert "cleanupPeriodDays" in out


def test_banner_renders_in_diagnostics_mode_too() -> None:
    out = _render(_result(warnings=[_warning()]), show_diagnostics=True)
    assert "cleanupPeriodDays" in out


def test_no_banner_when_no_warnings() -> None:
    out = _render(_result(warnings=[]))
    assert "⚠" not in out


def test_warning_serialized_in_json_envelope() -> None:
    """The warning survives a model_dump round-trip for the JSON path."""
    result = _result(warnings=[_warning()])
    dumped = result.model_dump(mode="json")
    assert dumped["warnings"][0]["code"] == "cleanup_period_truncation"
    assert dumped["warnings"][0]["severity"] == "warning"
