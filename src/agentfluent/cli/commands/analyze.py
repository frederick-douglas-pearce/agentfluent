"""agentfluent analyze -- compute execution analytics and diagnostics."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from agentfluent.analytics.pipeline import AnalysisResult, analyze_sessions
from agentfluent.core.discovery import find_project

app = typer.Typer(help="Analyze agent sessions.")
console = Console()


def _format_cost(cost: float) -> str:
    """Format a dollar cost for display."""
    if cost < 0.01:
        return f"${cost:.4f}"
    return f"${cost:.2f}"


def _format_tokens(tokens: int) -> str:
    """Format token count with comma separator."""
    return f"{tokens:,}"


def _print_quiet(result: AnalysisResult) -> None:
    """Print a one-line summary."""
    tm = result.token_metrics
    am = result.agent_metrics
    console.print(
        f"Sessions: {result.session_count} | "
        f"Tokens: {_format_tokens(tm.total_tokens)} | "
        f"Cost: {_format_cost(tm.total_cost)} | "
        f"Agent invocations: {am.total_invocations}"
    )


def _print_table(result: AnalysisResult, *, verbose: bool = False) -> None:
    """Print Rich-formatted tables."""
    tm = result.token_metrics
    am = result.agent_metrics
    tlm = result.tool_metrics

    # Token summary table
    token_table = Table(title="Token Usage", show_header=True)
    token_table.add_column("Metric", style="cyan")
    token_table.add_column("Value", justify="right")
    token_table.add_row("Input tokens", _format_tokens(tm.input_tokens))
    token_table.add_row("Output tokens", _format_tokens(tm.output_tokens))
    token_table.add_row("Cache creation tokens", _format_tokens(tm.cache_creation_input_tokens))
    token_table.add_row("Cache read tokens", _format_tokens(tm.cache_read_input_tokens))
    token_table.add_row("Total tokens", _format_tokens(tm.total_tokens))
    token_table.add_row("Total cost", _format_cost(tm.total_cost))
    token_table.add_row("Cache efficiency", f"{tm.cache_efficiency}%")
    token_table.add_row("API calls", str(tm.api_call_count))
    console.print(token_table)

    # Per-model breakdown
    if tm.by_model and (verbose or len(tm.by_model) > 1):
        model_table = Table(title="Cost by Model", show_header=True)
        model_table.add_column("Model", style="cyan")
        model_table.add_column("Tokens", justify="right")
        model_table.add_column("Cost", justify="right")
        for model_name, breakdown in sorted(tm.by_model.items()):
            model_table.add_row(
                model_name,
                _format_tokens(breakdown.total_tokens),
                _format_cost(breakdown.cost),
            )
        console.print(model_table)

    # Tool patterns
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

    # Agent metrics
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
                _format_tokens(int(m.avg_tokens_per_invocation))
                if m.avg_tokens_per_invocation
                else "-"
            )
            duration = f"{m.total_duration_ms / 1000:.1f}s" if m.total_duration_ms else "-"
            agent_table.add_row(
                label.strip(),
                str(m.invocation_count),
                _format_tokens(m.total_tokens),
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

    # Session summary
    console.print(
        f"\n[bold]Sessions analyzed:[/bold] {result.session_count}"
    )


def _print_json(result: AnalysisResult) -> None:
    """Print JSON output."""
    data = asdict(result)
    # Convert Path objects to strings
    for session in data.get("sessions", []):
        if "session_path" in session:
            session["session_path"] = str(session["session_path"])
    console.print_json(json.dumps(data, default=str))


@app.callback(invoke_without_command=True)
def analyze(
    project: str = typer.Option(
        ...,
        "--project",
        "-p",
        help="Project slug or display name.",
    ),
    session: Optional[str] = typer.Option(  # noqa: UP007, UP045
        None,
        "--session",
        "-s",
        help="Specific session filename to analyze.",
    ),
    agent: Optional[str] = typer.Option(  # noqa: UP007, UP045
        None,
        "--agent",
        "-a",
        help="Filter to a specific agent type (e.g., 'pm').",
    ),
    latest: Optional[int] = typer.Option(  # noqa: UP007, UP045
        None,
        "--latest",
        "-n",
        help="Analyze only the N most recent sessions.",
    ),
    diagnostics: bool = typer.Option(
        False,
        "--diagnostics",
        "-d",
        help="Show detailed diagnostics (not yet implemented).",
    ),
    format: str = typer.Option(
        "table",
        "--format",
        "-f",
        help="Output format: table or json.",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show detailed output."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Show summary only."),
) -> None:
    """Analyze agent sessions for token usage, cost, and behavior diagnostics."""
    project_info = find_project(project)
    if project_info is None:
        console.print(f"[red]Project not found:[/red] {project}")
        console.print("Use [bold]agentfluent list[/bold] to see available projects.")
        raise SystemExit(2)

    # Resolve sessions
    session_infos = project_info.sessions
    if not session_infos:
        name = project_info.display_name
        console.print(f"[yellow]No sessions found for project:[/yellow] {name}")
        raise SystemExit(2)

    if session:
        session_infos = [s for s in session_infos if s.filename == session]
        if not session_infos:
            console.print(f"[red]Session not found:[/red] {session}")
            raise SystemExit(2)

    if latest is not None and latest > 0:
        session_infos = session_infos[:latest]

    paths = [s.path for s in session_infos]

    if diagnostics:
        console.print("[yellow]Diagnostics not yet implemented (E6).[/yellow]")

    # Run analysis
    result = analyze_sessions(paths, agent_filter=agent)

    # Output
    if format == "json":
        _print_json(result)
    elif quiet:
        _print_quiet(result)
    else:
        _print_table(result, verbose=verbose)
