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
    GLOBAL_AGENT_LABEL,
    SEVERITY_COLORS,
    average_score,
    axis_label,
    format_cost,
    format_date,
    format_size,
    format_tokens,
    score_color,
    severity_cell,
    truncate,
)
from agentfluent.diagnostics.models import Axis, SignalType

API_RATE_FOOTNOTE = (
    "API rate — pay-per-token equivalent. "
    "Subscription plans (Pro/Max/Team/Enterprise) have fixed monthly cost "
    "independent of usage."
)

GLOSSARY_FOOTNOTE = "See docs/GLOSSARY.md for term definitions."

# Negative-savings short marker for the Offload Candidates table's Note
# column. Single source of truth: the formatter renders it; tests assert
# on it. The model's verbose ``cost_note`` ("parent-thread cache appears
# load-bearing...") is a separate, longer text shown only in --verbose
# YAML output. Keeping the short and verbose forms decoupled lets each
# evolve independently without drifting.
OFFLOAD_COST_MORE_NOTE = "offload would cost MORE"

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
    top_n: int = 5,
    show_negative_savings: bool = False,
) -> None:
    """Render analyze output: token, cost, tool, agent, and diagnostics tables.

    ``top_n`` controls the "Top N priority fixes" summary block that
    renders above the Recommendations table (#172). 0 disables the
    summary; the full table still renders.
    """
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
        model_table = Table(
            title="Cost by Model (API rate)", show_header=True,
        )
        model_table.add_column("Model", style="cyan")
        model_table.add_column("Origin")
        model_table.add_column("Tokens", justify="right")
        model_table.add_column("Cost", justify="right")
        sorted_rows = sorted(
            tm.by_model,
            key=lambda b: (b.model, 0 if b.origin == "parent" else 1),
        )
        for breakdown in sorted_rows:
            model_table.add_row(
                breakdown.model,
                breakdown.origin,
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
                # Gate on idle_gap_ms, not (active vs duration) diff:
                # the two come from different sources (JSONL timestamps
                # vs parent toolUseResult) and disagree by ~ms even when
                # no idle was detected.
                if inv.duration_ms is None:
                    duration = "-"
                elif inv.idle_gap_ms and inv.active_duration_ms is not None:
                    duration = (
                        f"{inv.active_duration_ms / 1000:.1f}s "
                        f"({inv.duration_ms / 1000:.1f}s wall)"
                    )
                else:
                    duration = f"{inv.duration_ms / 1000:.1f}s"
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
            _format_diagnostics_table(
                console, diag, verbose=verbose, top_n=top_n,
                show_negative_savings=show_negative_savings,
            )
        else:
            _format_diagnostics_summary(console, diag)

    console.print(f"\n[bold]Sessions analyzed:[/bold] {result.session_count}")


def _verbose_signal_message(sig: DiagnosticSignal) -> str:
    """Augment outlier signals with distribution context for ``--verbose``.

    Token and duration outlier ``message`` already cite the actual
    value, the IQR-distance, and Q3. Verbose adds the rest of the
    distribution shape: median (center), P95 (tail), and the actual
    threshold value used. Other signal types pass through unchanged
    because their detail dicts don't carry distribution stats.
    """
    if sig.signal_type not in (SignalType.TOKEN_OUTLIER, SignalType.DURATION_OUTLIER):
        return sig.message

    median = sig.detail.get("median_value")
    p95 = sig.detail.get("p95_value")
    threshold = sig.detail.get("threshold_value")
    if not (
        isinstance(median, (int, float))
        and isinstance(p95, (int, float))
        and isinstance(threshold, (int, float))
    ):
        return sig.message

    if sig.signal_type == SignalType.DURATION_OUTLIER:
        m, p, t = (
            f"{median / 1000:.1f}s",
            f"{p95 / 1000:.1f}s",
            f"{threshold / 1000:.1f}s",
        )
    else:
        m, p, t = format_tokens(int(median)), format_tokens(int(p95)), format_tokens(int(threshold))

    return f"{sig.message} [median={m}, P95={p}, threshold={t}]"


def _format_diagnostics_table(
    console: Console,
    diag: DiagnosticsResult,
    *,
    verbose: bool = False,
    top_n: int = 5,
    show_negative_savings: bool = False,
) -> None:
    """Render diagnostic signals and recommendations tables.

    ``top_n`` controls the "Top priority fixes" summary block that
    renders above the aggregated Recommendations table (#172). The
    summary is suppressed in ``--verbose`` (where per-row priority
    breakdown lines convey the same info at higher granularity) and
    when ``top_n == 0`` or no aggregated rows exist.
    """
    if diag.signals:
        sig_table = Table(title="Diagnostic Signals", show_header=True)
        sig_table.add_column("Agent", style="cyan")
        sig_table.add_column("Type")
        sig_table.add_column("Severity")
        sig_table.add_column("Message")

        for sig in diag.signals:
            message = _verbose_signal_message(sig) if verbose else sig.message
            sig_table.add_row(
                escape(sig.agent_type or GLOBAL_AGENT_LABEL),
                escape(sig.signal_type.value),
                severity_cell(sig.severity),
                escape(message),
            )
        console.print(sig_table)

    if diag.aggregated_recommendations:
        if not verbose:
            _format_top_recommendations(console, diag, top_n=top_n)

        rec_table = Table(title="Recommendations", show_header=True)
        rec_table.add_column("#", style="dim", justify="right")
        rec_table.add_column("Agent", style="cyan")
        rec_table.add_column("Target")
        rec_table.add_column("Severity")
        rec_table.add_column("Count", justify="right")
        rec_table.add_column("Recommendation")

        for idx, agg in enumerate(diag.aggregated_recommendations, start=1):
            message = f"{axis_label(Axis(agg.primary_axis))} {escape(agg.representative_message)}"
            rec_table.add_row(
                str(idx),
                escape(agg.agent_type or GLOBAL_AGENT_LABEL),
                escape(agg.target),
                severity_cell(agg.severity),
                str(agg.count),
                message,
            )
        console.print(rec_table)

        if verbose:
            _print_priority_breakdowns(console, diag)

    _format_deep_diagnostics(console, diag, verbose=verbose)
    _format_delegation_suggestions(console, diag, verbose=verbose)
    _format_offload_candidates(
        console, diag,
        verbose=verbose,
        show_negative_savings=show_negative_savings,
    )

    console.print(GLOSSARY_FOOTNOTE, style="dim")


def _print_priority_breakdowns(
    console: Console,
    diag: DiagnosticsResult,
) -> None:
    """Render per-row dim priority breakdown lines under verbose mode.

    Format: ``  N. Priority: 312.4 (cost: 12.4, speed: 0.0, quality: 300.0)``

    Printed below the aggregated Recommendations table so the
    composite ``priority_score`` and per-axis contributions are
    visible without bloating the table itself.
    """
    if not diag.aggregated_recommendations:
        return
    console.print("\n[dim]Priority breakdown[/dim]")
    for idx, agg in enumerate(diag.aggregated_recommendations, start=1):
        cost = agg.axis_scores.get("cost", 0.0)
        speed = agg.axis_scores.get("speed", 0.0)
        quality = agg.axis_scores.get("quality", 0.0)
        console.print(
            f"  [dim]{idx}. Priority: {agg.priority_score:.1f} "
            f"(cost: {cost:.1f}, speed: {speed:.1f}, "
            f"quality: {quality:.1f})[/dim]",
        )


def _format_top_recommendations(
    console: Console,
    diag: DiagnosticsResult,
    *,
    top_n: int,
) -> None:
    """Render a compact "Top N priority fixes" pointer block above the full table.

    Each row's ``#N`` index matches the same row's index in the
    Recommendations table below — the summary is a pointer list, not a
    second copy of the table. Drops ``representative_message`` (the
    table immediately below carries it) and surfaces only the
    load-bearing scan signals: severity, agent, count, target, axis.
    Suppressed when ``top_n == 0`` or no aggregated rows exist. The
    aggregated list is already sorted by ``priority_score`` desc, so
    the top N rows are the top N priorities by definition.
    """
    if top_n <= 0:
        return
    aggs = diag.aggregated_recommendations
    if not aggs:
        return
    shown = min(top_n, len(aggs))

    console.print(
        f"\n[bold]Top {shown} priority fixes "
        "(see Recommendations table below for detail):[/bold]",
    )

    table = Table(show_header=False, box=None, padding=(0, 1))
    table.add_column("#", style="dim", justify="right")
    table.add_column("Severity")
    table.add_column("Agent", style="cyan")
    table.add_column("Count", justify="right")
    table.add_column("Target")
    table.add_column("Axis")

    for idx, agg in enumerate(aggs[:shown], start=1):
        table.add_row(
            f"#{idx}",
            severity_cell(agg.severity),
            escape(agg.agent_type or GLOBAL_AGENT_LABEL),
            f"{agg.count}×",
            f"target: {escape(agg.target)}",
            axis_label(Axis(agg.primary_axis)),
        )
    console.print(table)


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
    color = SEVERITY_COLORS[sig.severity]
    header = (
        f"\n[{color}]{escape(sig.signal_type.value)}[/{color}] "
        f"[cyan]{escape(sig.agent_type or GLOBAL_AGENT_LABEL)}[/cyan] — {escape(sig.message)}"
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
            console.print()
            console.print(escape(sug.yaml_draft))


def _format_offload_candidates(
    console: Console,
    diag: DiagnosticsResult,
    *,
    verbose: bool,
    show_negative_savings: bool = False,
) -> None:
    """Render the "Offload Candidates" section (#189).

    By default (``show_negative_savings=False``) suppresses rows where
    ``estimated_savings_usd <= 0``: those represent patterns where
    offloading would cost MORE than staying on the parent thread because
    parent-thread cache is load-bearing — they're informational, not
    actionable, and a section named "Offload Candidates" full of
    "do not offload" rows misleads at a glance (#344). The full set
    remains in JSON for consumers who want to see everything.

    When all candidates were filtered, render a one-line footnote
    pointing at ``--show-negative-savings`` so the user knows the
    section wasn't silently empty.

    Compact table sorted by ``estimated_savings_usd`` descending —
    biggest dollar wins first. With ``--show-negative-savings``,
    negative rows sort to the bottom under existing semantics.
    Verbose adds the offload-flavored ``yaml_draft`` (parent → alt
    model preamble + cost note + frontmatter + prompt) below each row.

    Negative-savings rendering (only with the flag):
      - savings cell: ``[red]+$X.XX[/red]`` (positive magnitude with a
        ``+`` mnemonic for "this many additional dollars")
      - Note column: includes ``"offload would cost MORE"`` so the
        sign-flip in the savings cell isn't easy to misread

    All JSONL/trace-derived strings (name, tools, cost_note, dedup_note)
    pass through ``escape`` before hitting Rich.
    """
    all_candidates = sorted(
        diag.offload_candidates,
        key=lambda c: c.estimated_savings_usd,
        reverse=True,
    )
    if not all_candidates:
        return

    if show_negative_savings:
        candidates = all_candidates
        hidden_count = 0
    else:
        candidates = [c for c in all_candidates if c.estimated_savings_usd > 0]
        hidden_count = len(all_candidates) - len(candidates)

    if not candidates:
        console.print("\n[bold]Offload Candidates[/bold]")
        console.print(
            f"[dim]No offload candidates surfaced ({hidden_count} "
            "negative-savings rows hidden — pass --show-negative-savings "
            "to inspect).[/dim]",
        )
        return

    console.print("\n[bold]Offload Candidates[/bold]")
    cand_table = Table(show_header=True, title_style="")
    cand_table.add_column("Name", style="cyan")
    cand_table.add_column("Confidence")
    cand_table.add_column("Cluster size", justify="right")
    cand_table.add_column("Tools")
    cand_table.add_column("Est. savings", justify="right")
    cand_table.add_column("Note")

    for cand in candidates:
        color = CONFIDENCE_COLORS.get(cand.confidence, "white")
        # Read tools/tools_note FLAT off the candidate, mirroring how
        # every other column reads from `OffloadCandidate` directly.
        # Reaching into `cand.subagent_draft` here would silently fall
        # through to "[dim]—[/dim]" for the v0.6 ``target_kind=skill``
        # path while the rest of the row still rendered.
        if cand.tools:
            tools_display = escape(", ".join(cand.tools))
        elif cand.tools_note:
            tools_display = escape(cand.tools_note)
        else:
            tools_display = "[dim]—[/dim]"

        # Signed savings with a sign-aware visual cue. Negatives flip
        # to "+$X.XX" magnitude in red; the Note column carries the
        # "cost MORE" warning so the meaning isn't lost on a quick scan.
        savings = cand.estimated_savings_usd
        if savings >= 0:
            savings_display = f"${savings:.2f}"
        else:
            savings_display = f"[red]+${-savings:.2f}[/red]"

        note_parts: list[str] = []
        if savings < 0:
            # Short marker only — the model's verbose `cost_note`
            # ("parent-thread cache load-bearing...") is the same
            # warning at length and lives in the --verbose YAML
            # preamble. Duplicating it here just bloats the table.
            note_parts.append(OFFLOAD_COST_MORE_NOTE)
        if cand.dedup_note:
            note_parts.append(escape(cand.dedup_note))
        note = "; ".join(note_parts)

        cand_table.add_row(
            escape(cand.name),
            f"[{color}]{cand.confidence}[/{color}]",
            str(cand.cluster_size),
            tools_display,
            savings_display,
            note,
        )
    console.print(cand_table)

    if verbose:
        for cand in candidates:
            console.print()
            console.print(escape(cand.yaml_draft))


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
            row = [
                agent_name,
                severity_cell(rec.severity),
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
    console.print(GLOSSARY_FOOTNOTE, style="dim")
