"""The table formatter must surface ``tier3_degraded`` as a banner.

Pre-fix, the flag was set in ``DiagnosticsResult`` but no non-JSON
renderer touched it — interactive CLI users had no way to tell that
a Tier 3 rate-limit or recoverable error had silently truncated
their results. The footer banner closes that observability gap.
"""

from __future__ import annotations

from io import StringIO

from rich.console import Console

from agentfluent.analytics.agent_metrics import AgentMetrics
from agentfluent.analytics.pipeline import AnalysisResult
from agentfluent.analytics.tokens import TokenMetrics
from agentfluent.analytics.tools import ToolMetrics
from agentfluent.cli.formatters.table import format_analysis_table
from agentfluent.diagnostics.models import DiagnosticsResult


def _result(*, tier3_degraded: bool, signals: list = None) -> AnalysisResult:
    return AnalysisResult(
        token_metrics=TokenMetrics(),
        tool_metrics=ToolMetrics(),
        agent_metrics=AgentMetrics(),
        session_count=1,
        diagnostics=DiagnosticsResult(
            signals=signals or [],
            tier3_degraded=tier3_degraded,
        ),
    )


def _render(result: AnalysisResult, *, show_diagnostics: bool) -> str:
    buf = StringIO()
    format_analysis_table(
        Console(file=buf, width=160, force_terminal=False),
        result,
        verbose=False,
        show_diagnostics=show_diagnostics,
    )
    return buf.getvalue()


def test_banner_renders_when_degraded() -> None:
    out = _render(_result(tier3_degraded=True), show_diagnostics=True)
    assert "Tier 3 (GitHub) data is incomplete" in out


def test_banner_renders_in_summary_mode_too() -> None:
    # The summary path (no --diagnostics) also gets the banner so
    # users who run the default analyze command can still see Tier 3
    # was incomplete. Verified via show_diagnostics=False.
    out = _render(_result(tier3_degraded=True), show_diagnostics=False)
    assert "Tier 3 (GitHub) data is incomplete" in out


def test_banner_absent_when_not_degraded() -> None:
    # Regression guard: a clean run must not flash the warning to
    # users — otherwise the banner becomes meaningless noise.
    out = _render(_result(tier3_degraded=False), show_diagnostics=True)
    assert "Tier 3" not in out
