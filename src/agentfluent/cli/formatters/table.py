"""Rich table formatters for CLI output.

Each command gets its own `format_*_table` function -- no shared Formatter
base class, since the three commands produce different data shapes that
would not benefit from unification. Functions render already-computed data
to a Rich Console; I/O stays in the command callbacks.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Console
from rich.table import Table

from agentfluent.cli.formatters.helpers import (
    SEVERITY_COLORS,
    format_cost,
    format_date,
    format_size,
    format_tokens,
    score_color,
)

if TYPE_CHECKING:
    from agentfluent.analytics.pipeline import AnalysisResult
    from agentfluent.config.models import ConfigScore
    from agentfluent.core.discovery import ProjectInfo, SessionInfo
    from agentfluent.diagnostics.models import DiagnosticsResult


def format_projects_table(
    console: Console,
    projects: list[ProjectInfo],
) -> None:
    """Render discovered projects as a Rich table."""
    if not projects:
        console.print("No projects found in ~/.claude/projects/")
        return

    table = Table(title="Projects")
    table.add_column("Name", style="cyan")
    table.add_column("Sessions", justify="right")
    table.add_column("Size", justify="right")
    table.add_column("Latest", style="dim")

    for p in projects:
        table.add_row(
            p.display_name,
            str(p.session_count),
            format_size(p.total_size_bytes),
            format_date(p.latest_session),
        )

    console.print(table)


def format_sessions_table(
    console: Console,
    project_name: str,
    sessions: list[tuple[SessionInfo, int]],
) -> None:
    """Render per-session stats as a Rich table.

    `sessions` is a list of (SessionInfo, message_count) tuples. The caller
    owns session parsing so this function stays pure rendering.
    """
    if not sessions:
        console.print(f"No sessions in project '{project_name}'")
        return

    table = Table(title=f"Sessions — {project_name}")
    table.add_column("File", style="cyan")
    table.add_column("Size", justify="right")
    table.add_column("Modified", style="dim")
    table.add_column("Messages", justify="right")
    table.add_column("Subagents", justify="right", style="dim")

    for info, message_count in sessions:
        table.add_row(
            info.filename,
            format_size(info.size_bytes),
            format_date(info.modified),
            str(message_count),
            str(info.subagent_count) if info.subagent_count > 0 else "—",
        )

    console.print(table)


def format_analysis_table(
    console: Console,
    result: AnalysisResult,
    *,
    verbose: bool = False,
    show_diagnostics: bool = False,
) -> None:
    """Render analyze output: token, cost, tool, agent, and diagnostics tables."""
    tm = result.token_metrics
    am = result.agent_metrics
    tlm = result.tool_metrics

    token_table = Table(title="Token Usage", show_header=True)
    token_table.add_column("Metric", style="cyan")
    token_table.add_column("Value", justify="right")
    token_table.add_row("Input tokens", format_tokens(tm.input_tokens))
    token_table.add_row("Output tokens", format_tokens(tm.output_tokens))
    token_table.add_row("Cache creation tokens", format_tokens(tm.cache_creation_input_tokens))
    token_table.add_row("Cache read tokens", format_tokens(tm.cache_read_input_tokens))
    token_table.add_row("Total tokens", format_tokens(tm.total_tokens))
    token_table.add_row("Total cost", format_cost(tm.total_cost))
    token_table.add_row("Cache efficiency", f"{tm.cache_efficiency}%")
    token_table.add_row("API calls", str(tm.api_call_count))
    console.print(token_table)

    if tm.by_model and (verbose or len(tm.by_model) > 1):
        model_table = Table(title="Cost by Model", show_header=True)
        model_table.add_column("Model", style="cyan")
        model_table.add_column("Tokens", justify="right")
        model_table.add_column("Cost", justify="right")
        for model_name, breakdown in sorted(tm.by_model.items()):
            model_table.add_row(
                model_name,
                format_tokens(breakdown.total_tokens),
                format_cost(breakdown.cost),
            )
        console.print(model_table)

    if tlm.total_tool_calls > 0:
        tool_table = Table(title="Tool Usage", show_header=True)
        tool_table.add_column("Tool", style="cyan")
        tool_table.add_column("Calls", justify="right")
        tool_table.add_column("% of Total", justify="right")
        for name, count in tlm.tool_frequency.items():
            pct = round(count / tlm.total_tool_calls * 100, 1)
            tool_table.add_row(name, str(count), f"{pct}%")
        tool_table.add_row("", "", "")
        tool_table.add_row("Total", str(tlm.total_tool_calls), "")
        tool_table.add_row("Unique tools", str(tlm.unique_tool_count), "")
        console.print(tool_table)

    if am.total_invocations > 0:
        agent_table = Table(title="Agent Invocations", show_header=True)
        agent_table.add_column("Agent Type", style="cyan")
        agent_table.add_column("Count", justify="right")
        agent_table.add_column("Tokens", justify="right")
        agent_table.add_column("Avg Tokens/Call", justify="right")
        agent_table.add_column("Duration", justify="right")
        for _key, m in sorted(am.by_agent_type.items()):
            label = f"{m.agent_type} {'(builtin)' if m.is_builtin else ''}"
            avg_tok = (
                format_tokens(int(m.avg_tokens_per_invocation))
                if m.avg_tokens_per_invocation
                else "-"
            )
            duration = f"{m.total_duration_ms / 1000:.1f}s" if m.total_duration_ms else "-"
            agent_table.add_row(
                label.strip(),
                str(m.invocation_count),
                format_tokens(m.total_tokens),
                avg_tok,
                duration,
            )
        agent_table.add_row("", "", "", "", "")
        agent_table.add_row("Total", str(am.total_invocations), "", "", "")
        agent_table.add_row(
            "Agent token %",
            f"{am.agent_token_percentage}%",
            "",
            "",
            "",
        )
        console.print(agent_table)

    diag = result.diagnostics
    if diag:
        if show_diagnostics:
            _format_diagnostics_table(console, diag, verbose=verbose)
        else:
            _format_diagnostics_summary(console, diag)

    console.print(f"\n[bold]Sessions analyzed:[/bold] {result.session_count}")


def _format_diagnostics_table(
    console: Console,
    diag: DiagnosticsResult,
    *,
    verbose: bool = False,
) -> None:
    """Render diagnostic signals and recommendations tables."""
    if diag.signals:
        sig_table = Table(title="Diagnostic Signals", show_header=True)
        sig_table.add_column("Agent", style="cyan")
        sig_table.add_column("Type")
        sig_table.add_column("Severity")
        sig_table.add_column("Message")

        for sig in diag.signals:
            color = SEVERITY_COLORS.get(sig.severity, "white")
            sig_table.add_row(
                sig.agent_type,
                sig.signal_type.value,
                f"[{color}]{sig.severity.value}[/{color}]",
                sig.message,
            )
        console.print(sig_table)

    if diag.recommendations:
        rec_table = Table(title="Recommendations", show_header=True)
        rec_table.add_column("Agent", style="cyan")
        rec_table.add_column("Target")
        rec_table.add_column("Severity")
        if verbose:
            rec_table.add_column("Observation")
            rec_table.add_column("Action")
        else:
            rec_table.add_column("Recommendation")

        for rec in diag.recommendations:
            color = SEVERITY_COLORS.get(rec.severity, "white")
            if verbose:
                rec_table.add_row(
                    rec.agent_type,
                    rec.target,
                    f"[{color}]{rec.severity.value}[/{color}]",
                    rec.observation,
                    rec.action,
                )
            else:
                rec_table.add_row(
                    rec.agent_type,
                    rec.target,
                    f"[{color}]{rec.severity.value}[/{color}]",
                    rec.message,
                )
        console.print(rec_table)

    if diag.subagent_trace_count > 0:
        console.print(
            f"\n[dim]{diag.subagent_trace_count} subagent trace files available. "
            "Deep diagnostics (per-tool-call analysis) coming in v1.1.[/dim]"
        )


def _format_diagnostics_summary(console: Console, diag: DiagnosticsResult) -> None:
    """Print a brief diagnostics summary when --diagnostics is not passed."""
    signal_count = len(diag.signals)
    if signal_count > 0:
        console.print(
            f"\n[yellow]{signal_count} diagnostic signal(s) detected.[/yellow] "
            "Run with [bold]--diagnostics[/bold] for details."
        )


def format_config_check_table(
    console: Console,
    scores: list[ConfigScore],
    *,
    verbose: bool = False,
) -> None:
    """Render agent configuration scores and recommendations."""
    summary = Table(title="Agent Configuration Scores", show_header=True)
    summary.add_column("Agent", style="cyan")
    summary.add_column("Score", justify="right")
    summary.add_column("Description", justify="right")
    summary.add_column("Tools", justify="right")
    summary.add_column("Model", justify="right")
    summary.add_column("Prompt", justify="right")
    summary.add_column("Recs", justify="right")

    for s in scores:
        color = score_color(s.overall_score)
        summary.add_row(
            s.agent_name,
            f"[{color}]{s.overall_score}/100[/{color}]",
            f"{s.dimension_scores.get('description', 0)}/25",
            f"{s.dimension_scores.get('tool_restrictions', 0)}/25",
            f"{s.dimension_scores.get('model_selection', 0)}/25",
            f"{s.dimension_scores.get('prompt_body', 0)}/25",
            str(len(s.recommendations)),
        )
    console.print(summary)

    all_recs = [(s.agent_name, r) for s in scores for r in s.recommendations]
    if all_recs:
        rec_table = Table(title="Recommendations", show_header=True)
        rec_table.add_column("Agent", style="cyan")
        rec_table.add_column("Severity")
        rec_table.add_column("Recommendation")
        if verbose:
            rec_table.add_column("Action")

        for agent_name, rec in all_recs:
            color = SEVERITY_COLORS.get(rec.severity, "white")
            row = [
                agent_name,
                f"[{color}]{rec.severity.value}[/{color}]",
                rec.message,
            ]
            if verbose:
                row.append(rec.suggested_action)
            rec_table.add_row(*row)
        console.print(rec_table)

    avg = sum(s.overall_score for s in scores) // len(scores) if scores else 0
    console.print(
        f"\n[bold]Agents scanned:[/bold] {len(scores)}, "
        f"[bold]average score:[/bold] {avg}/100, "
        f"[bold]recommendations:[/bold] {len(all_recs)}"
    )
