"""#646 AC11: the analyze agent table and the ``--verbose`` per-invocation table
show trace spend, never the ``toolUseResult.totalTokens`` rollup proxy."""

from __future__ import annotations

from pathlib import Path

from rich.console import Console

from agentfluent.agents.models import AgentInvocation
from agentfluent.analytics.agent_metrics import compute_agent_metrics
from agentfluent.analytics.pipeline import AnalysisResult, SessionAnalysis
from agentfluent.analytics.tokens import TokenMetrics
from agentfluent.analytics.tools import ToolMetrics
from agentfluent.cli.formatters.table import format_analysis_table
from agentfluent.core.session import Usage
from agentfluent.traces.models import SubagentTrace

HAIKU = "claude-haiku-4-5-20251001"


def _inv(
    agent_type: str, description: str, *, rollup: int | None, spend: int | None,
) -> AgentInvocation:
    """``rollup`` is the proxy ``total_tokens``; ``spend`` the linked trace's usage
    (``None`` = no trace)."""
    inv = AgentInvocation(
        agent_type=agent_type,
        description=description,
        prompt="run",
        tool_use_id=f"toolu_{description}",
        total_tokens=rollup,
    )
    if spend is not None:
        inv.trace = SubagentTrace(
            agent_id=description,
            delegation_prompt="x",
            model=HAIKU,
            model_turns=4,
            usage=Usage(input_tokens=spend),
        )
    return inv


def _render(invocations: list[AgentInvocation], *, verbose: bool) -> list[str]:
    am = compute_agent_metrics(invocations, session_total_tokens=10_000_000)
    session = SessionAnalysis(
        session_path=Path("s.jsonl"),
        token_metrics=TokenMetrics(),
        tool_metrics=ToolMetrics(),
        agent_metrics=am,
        invocations=invocations,
    )
    result = AnalysisResult(sessions=[session], agent_metrics=am)
    console = Console(record=True, width=220, force_terminal=False)
    format_analysis_table(console, result, verbose=verbose)
    return console.export_text().splitlines()


def _cells(lines: list[str], first_cell: str) -> list[str]:
    for line in lines:
        cells = [c.strip() for c in line.replace("┃", "│").split("│")]
        if len(cells) > 2 and cells[1] == first_cell:
            return cells[1:-1]
    raise AssertionError(f"no row starting {first_cell!r} in:\n" + "\n".join(lines))


def _invocations() -> list[AgentInvocation]:
    return [
        # pm: two traced (spend 300,000 + 500,000) + one trace-missing.
        _inv("pm", "pm-a", rollup=5_111, spend=300_000),
        _inv("pm", "pm-b", rollup=7_222, spend=500_000),
        _inv("pm", "pm-c", rollup=9_333, spend=None),
        # explore: no spend coverage at all.
        _inv("explore", "ex-a", rollup=9_999, spend=None),
    ]


def test_agent_table_tokens_and_avg_are_spend_over_spend_coverage() -> None:
    lines = _render(_invocations(), verbose=False)
    pm = _cells(lines, "pm")
    # [type, count, avg turns, tokens, avg tokens/call, avg duration]
    assert pm[1] == "3"
    assert pm[3] == "800,000"
    # Avg divides by the 2 spend-covered invocations, not all 3.
    assert pm[4] == "400,000"
    joined = "\n".join(lines)
    for proxy in ("5,111", "7,222", "9,333", "21,666"):
        assert proxy not in joined


def test_agent_table_no_spend_coverage_renders_unknown_not_zero() -> None:
    explore = _cells(_render(_invocations(), verbose=False), "explore (builtin)")
    assert explore[3] == "-"
    assert explore[4] == "-"
    assert "9,999" not in explore


def test_verbose_per_invocation_tokens_are_trace_spend() -> None:
    lines = _render(_invocations(), verbose=True)
    rows = {
        desc: next(line for line in lines if desc in line)
        for desc in ("pm-a", "pm-b", "pm-c", "ex-a")
    }
    assert "300,000" in rows["pm-a"]
    assert "500,000" in rows["pm-b"]
    for desc, proxy in (("pm-a", "5,111"), ("pm-b", "7,222"), ("pm-c", "9,333"), ("ex-a", "9,999")):
        assert proxy not in rows[desc]
    # Trace-missing invocation: tokens cell is "-".
    pm_c = [c.strip() for c in rows["pm-c"].split("│")]
    assert pm_c[3] == "-"
