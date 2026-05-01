"""Active vs wall duration rendering in the verbose Per-Invocation table.

Display gates on ``idle_gap_ms`` (authoritative), not on a cross-source
comparison of ``active_duration_ms`` vs ``duration_ms`` — the two come
from different sources (JSONL timestamps vs parent toolUseResult) and
disagree by ~ms even when no idle was detected.
"""

from __future__ import annotations

from pathlib import Path

from rich.console import Console

from agentfluent.agents.models import AgentInvocation
from agentfluent.analytics.agent_metrics import AgentMetrics, AgentTypeMetrics
from agentfluent.analytics.pipeline import AnalysisResult, SessionAnalysis
from agentfluent.analytics.tokens import TokenMetrics
from agentfluent.analytics.tools import ToolMetrics
from agentfluent.cli.formatters.table import format_analysis_table
from agentfluent.traces.models import SubagentTrace


def _trace(*, duration_ms: int, idle_gap_ms: int) -> SubagentTrace:
    return SubagentTrace(
        agent_id="abc",
        agent_type="pm",
        delegation_prompt="x",
        duration_ms=duration_ms,
        idle_gap_ms=idle_gap_ms,
    )


def _invocation(
    agent_type: str,
    *,
    description: str,
    parent_duration_ms: int,
    trace: SubagentTrace | None,
) -> AgentInvocation:
    return AgentInvocation(
        agent_type=agent_type,
        description=description,
        prompt="run",
        tool_use_id=f"toolu_{description}",
        total_tokens=1000,
        tool_uses=10,
        duration_ms=parent_duration_ms,
        trace=trace,
    )


def _make_result(invocations: list[AgentInvocation]) -> AnalysisResult:
    am = AgentMetrics(
        total_invocations=len(invocations),
        by_agent_type={
            "pm": AgentTypeMetrics(
                agent_type="pm",
                is_builtin=False,
                invocation_count=len(invocations),
                total_tokens=sum((i.total_tokens or 0) for i in invocations),
                total_duration_ms=sum((i.duration_ms or 0) for i in invocations),
            ),
        },
    )
    session = SessionAnalysis(
        session_path=Path("session-1.jsonl"),
        token_metrics=TokenMetrics(),
        tool_metrics=ToolMetrics(),
        agent_metrics=am,
        invocations=invocations,
    )
    return AnalysisResult(sessions=[session], agent_metrics=am)


def _render(result: AnalysisResult) -> str:
    console = Console(record=True, width=200, force_terminal=False)
    format_analysis_table(console, result, verbose=True)
    return console.export_text()


def test_idle_gap_renders_active_wall_dual_format() -> None:
    inv = _invocation(
        "pm",
        description="with-idle",
        parent_duration_ms=1_834_000,
        trace=_trace(duration_ms=1_834_000, idle_gap_ms=1_452_000),
    )
    output = _render(_make_result([inv]))
    # 382s active vs 1834s wall → both numbers shown with "wall" suffix.
    assert "382.0s (1834.0s wall)" in output


def test_zero_idle_gap_renders_bare_duration() -> None:
    # Regression: parent duration_ms=343_500, trace.duration_ms=343_491
    # (~9ms cross-source disagreement). With idle_gap_ms=0 the dual
    # format must NOT fire.
    trace = SubagentTrace(
        agent_id="abc",
        agent_type="pm",
        delegation_prompt="x",
        duration_ms=343_491,
        idle_gap_ms=0,
    )
    inv = _invocation(
        "pm",
        description="no-idle",
        parent_duration_ms=343_500,
        trace=trace,
    )
    output = _render(_make_result([inv]))
    rows = [line for line in output.splitlines() if "no-idle" in line]
    assert any("343.5s" in r for r in rows)
    assert all("wall" not in r for r in rows)


def test_no_trace_renders_bare_duration() -> None:
    inv = _invocation(
        "pm",
        description="no-trace",
        parent_duration_ms=120_000,
        trace=None,
    )
    output = _render(_make_result([inv]))
    rows = [line for line in output.splitlines() if "no-trace" in line]
    assert any("120.0s" in r for r in rows)
    assert all("wall" not in r for r in rows)


def test_missing_duration_renders_dash() -> None:
    inv = AgentInvocation(
        agent_type="pm",
        description="interrupted",
        prompt="run",
        tool_use_id="toolu_x",
        duration_ms=None,
    )
    output = _render(_make_result([inv]))
    # Look for "interrupted" row's Duration column showing "-"
    rows = [line for line in output.splitlines() if "interrupted" in line]
    assert any(" - " in row or row.rstrip().endswith("-") for row in rows)
