"""Rich table formatters for CLI output.

Each command gets its own `format_*_table` function -- no shared Formatter
base class, since the three commands produce different data shapes that
would not benefit from unification. Functions render already-computed data
to a Rich Console; I/O stays in the command callbacks.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Console
from rich.markup import escape
from rich.table import Table

from agentfluent.cli.formatters.helpers import (
    CONFIDENCE_COLORS,
    SEVERITY_COLORS,
    average_score,
    format_cost,
    format_date,
    format_size,
    format_tokens,
    score_color,
    truncate,
)

API_RATE_FOOTNOTE = (
    "API rate — pay-per-token equivalent. "
    "Subscription plans (Pro/Max/Team/Enterprise) have fixed monthly cost "
    "independent of usage."
)

if TYPE_CHECKING:
    from agentfluent.analytics.pipeline import AnalysisResult
    from agentfluent.config.models import ConfigScore
    from agentfluent.core.discovery import ProjectInfo, SessionInfo
    from agentfluent.diagnostics.models import DiagnosticSignal, DiagnosticsResult


def format_projects_table(
    console: Console,
    projects: list[ProjectInfo],
    *,
    verbose: bool = False,
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
    if verbose:
        table.add_column("Slug", style="dim")

    for p in projects:
        row = [
            p.display_name,
            str(p.session_count),
            format_size(p.total_size_bytes),
            format_date(p.latest_session),
        ]
        if verbose:
            row.append(p.slug)
        table.add_row(*row)

    console.print(table)


def format_sessions_table(
    console: Console,
    project_name: str,
    sessions: list[tuple[SessionInfo, int]],
    *,
    verbose: bool = False,
) -> None:
    """Render per-session stats as a Rich table.

    `sessions` is a list of (SessionInfo, message_count) tuples. The caller
    owns session parsing so this function stays pure rendering.
    """
    if not sessions:
        console.print(f"No sessions in project '{project_name}'")
        return

    table = Table(title=f"Sessions — {project_name}")
    file_label = "Path" if verbose else "File"
    table.add_column(file_label, style="cyan")
    table.add_column("Size", justify="right")
    table.add_column("Modified", style="dim")
    table.add_column("Messages", justify="right")
    table.add_column("Subagents", justify="right", style="dim")

    for info, message_count in sessions:
        table.add_row(
            str(info.path) if verbose else info.filename,
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
    token_table.add_row("Total cost (API rate)", format_cost(tm.total_cost))
    token_table.add_row("Cache efficiency", f"{tm.cache_efficiency}%")
    token_table.add_row("API calls", str(tm.api_call_count))
    console.print(token_table)

    if tm.by_model and (verbose or len(tm.by_model) > 1):
        model_table = Table(title="Cost by Model (API rate)", show_header=True)
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

    if verbose and len(result.sessions) > 1:
        session_table = Table(title="Per-Session Breakdown", show_header=True)
        session_table.add_column("Session", style="cyan")
        session_table.add_column("Tokens", justify="right")
        session_table.add_column("Cost (API rate)", justify="right")
        session_table.add_column("Tool calls", justify="right")
        session_table.add_column("Invocations", justify="right")
        for s in result.sessions:
            session_table.add_row(
                s.session_path.name,
                format_tokens(s.token_metrics.total_tokens),
                format_cost(s.token_metrics.total_cost),
                str(s.tool_metrics.total_tool_calls),
                str(s.agent_metrics.total_invocations),
            )
        console.print(session_table)

    if verbose and am.total_invocations > 0:
        inv_table = Table(title="Per-Invocation Detail", show_header=True)
        inv_table.add_column("Agent", style="cyan")
        inv_table.add_column("Description")
        inv_table.add_column("Tokens", justify="right")
        inv_table.add_column("Tool uses", justify="right")
        inv_table.add_column("Duration", justify="right")
        for s in result.sessions:
            for inv in s.invocations:
                tokens = format_tokens(inv.total_tokens) if inv.total_tokens else "-"
                tools = str(inv.tool_uses) if inv.tool_uses else "-"
                duration = f"{inv.duration_ms / 1000:.1f}s" if inv.duration_ms else "-"
                desc = truncate(inv.description, 60)
                inv_table.add_row(
                    escape(inv.agent_type),
                    escape(desc),
                    tokens,
                    tools,
                    duration,
                )
        console.print(inv_table)

    console.print(API_RATE_FOOTNOTE, style="dim")

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
                escape(sig.agent_type),
                escape(sig.signal_type.value),
                f"[{color}]{sig.severity.value}[/{color}]",
                escape(sig.message),
            )
        console.print(sig_table)

    if verbose and diag.recommendations:
        rec_table = Table(title="Recommendations", show_header=True)
        rec_table.add_column("Agent", style="cyan")
        rec_table.add_column("Target")
        rec_table.add_column("Severity")
        rec_table.add_column("Observation")
        rec_table.add_column("Action")

        for rec in diag.recommendations:
            color = SEVERITY_COLORS.get(rec.severity, "white")
            rec_table.add_row(
                escape(rec.agent_type),
                escape(rec.target),
                f"[{color}]{rec.severity.value}[/{color}]",
                escape(rec.observation),
                escape(rec.action),
            )
        console.print(rec_table)
    elif diag.aggregated_recommendations:
        rec_table = Table(title="Recommendations", show_header=True)
        rec_table.add_column("Agent", style="cyan")
        rec_table.add_column("Target")
        rec_table.add_column("Severity")
        rec_table.add_column("Count", justify="right")
        rec_table.add_column("Recommendation")

        for agg in diag.aggregated_recommendations:
            color = SEVERITY_COLORS.get(agg.severity, "white")
            rec_table.add_row(
                escape(agg.agent_type),
                escape(agg.target),
                f"[{color}]{agg.severity.value}[/{color}]",
                str(agg.count),
                escape(agg.representative_message),
            )
        console.print(rec_table)

    _format_deep_diagnostics(console, diag, verbose=verbose)
    _format_delegation_suggestions(console, diag, verbose=verbose)


def _format_deep_diagnostics(
    console: Console,
    diag: DiagnosticsResult,
    *,
    verbose: bool,
) -> None:
    """Render the Deep Diagnostics section with trace-signal evidence.

    Compact summary by default; per-signal evidence sub-tables under
    --verbose. Emits nothing when no trace-level signals are present.
    """
    from agentfluent.diagnostics import TRACE_SIGNAL_TYPES

    trace_signals = [s for s in diag.signals if s.signal_type in TRACE_SIGNAL_TYPES]
    if not trace_signals:
        return

    unique_agents = {s.agent_type for s in trace_signals}
    if not verbose:
        console.print(
            f"\n[bold]Deep Diagnostics:[/bold] {len(trace_signals)} trace signal(s) "
            f"across {len(unique_agents)} subagent(s). "
            "Use [bold]--verbose[/bold] for per-call evidence."
        )
        return

    console.print("\n[bold]Deep Diagnostics[/bold]")
    for sig in trace_signals:
        _render_trace_signal_evidence(console, sig)


def _render_trace_signal_evidence(console: Console, sig: DiagnosticSignal) -> None:
    """Render one trace signal with its evidence sub-table.

    All JSONL-sourced strings (agent_type, message, and every evidence
    dict field) are escaped before being passed to Rich — trace content
    is untrusted and could otherwise smuggle markup like `[link=…]`.
    """
    color = SEVERITY_COLORS.get(sig.severity, "white")
    header = (
        f"\n[{color}]{escape(sig.signal_type.value)}[/{color}] "
        f"[cyan]{escape(sig.agent_type)}[/cyan] — {escape(sig.message)}"
    )
    console.print(header)

    evidence = sig.detail.get("tool_calls", [])
    if not isinstance(evidence, list) or not evidence:
        return

    ev_table = Table(show_header=True, box=None, padding=(0, 1))
    ev_table.add_column("#", style="dim", justify="right")
    ev_table.add_column("Tool")
    ev_table.add_column("Input", overflow="fold")
    ev_table.add_column("Err", justify="center")
    ev_table.add_column("Result", overflow="fold")

    for entry in evidence:
        if not isinstance(entry, dict):
            continue
        ev_table.add_row(
            escape(str(entry.get("index", ""))),
            escape(str(entry.get("tool_name", ""))),
            escape(truncate(str(entry.get("input_summary", "")), 60)),
            "✗" if entry.get("is_error") else "",
            escape(truncate(str(entry.get("result_summary", "")), 60)),
        )
    console.print(ev_table)


def _format_delegation_suggestions(
    console: Console,
    diag: DiagnosticsResult,
    *,
    verbose: bool,
) -> None:
    """Render the "Suggested Subagents" section.

    Compact table by default: one row per suggestion with name, model,
    confidence, size, tools summary, dedup note. Verbose adds the
    synthesized prompt + top-terms block under each row.

    All JSONL-derived fields (name, description, tools, dedup note)
    pass through ``escape`` before hitting Rich — trace content is
    untrusted and could smuggle markup.
    """
    suggestions = diag.delegation_suggestions
    if not suggestions:
        return

    console.print("\n[bold]Suggested Subagents[/bold]")
    sug_table = Table(show_header=True, title_style="")
    sug_table.add_column("Name", style="cyan")
    sug_table.add_column("Model")
    sug_table.add_column("Confidence")
    sug_table.add_column("Cluster size", justify="right")
    sug_table.add_column("Tools")
    sug_table.add_column("Note")

    for sug in suggestions:
        color = CONFIDENCE_COLORS.get(sug.confidence, "white")
        tools_display = (
            ", ".join(sug.tools) if sug.tools
            else escape(sug.tools_note) or "[dim]—[/dim]"
        )
        note = escape(sug.dedup_note) if sug.dedup_note else ""
        sug_table.add_row(
            escape(sug.name),
            escape(sug.model),
            f"[{color}]{sug.confidence}[/{color}]",
            str(sug.cluster_size),
            escape(tools_display) if sug.tools else tools_display,
            note,
        )
    console.print(sug_table)

    if verbose:
        for sug in suggestions:
            top_terms = ", ".join(sug.top_terms) if sug.top_terms else "—"
            console.print(
                f"\n[cyan]{escape(sug.name)}[/cyan]  "
                f"[dim](cohesion {sug.cohesion_score:.2f}, "
                f"top terms: {escape(top_terms)})[/dim]",
            )
            console.print(f"  {escape(sug.description)}")
            console.print(f"  [dim]prompt draft:[/dim] {escape(sug.prompt_template)}")


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

    console.print(
        f"\n[bold]Agents scanned:[/bold] {len(scores)}, "
        f"[bold]average score:[/bold] {average_score(scores)}/100, "
        f"[bold]recommendations:[/bold] {len(all_recs)}"
    )
