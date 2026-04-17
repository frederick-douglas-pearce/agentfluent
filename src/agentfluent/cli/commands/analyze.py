"""agentfluent analyze -- compute execution analytics and diagnostics."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Optional

import typer
from rich.console import Console

from agentfluent.analytics.pipeline import AnalysisResult, analyze_sessions
from agentfluent.cli.formatters.helpers import format_cost, format_tokens
from agentfluent.cli.formatters.table import format_analysis_table
from agentfluent.core.discovery import find_project
from agentfluent.diagnostics import run_diagnostics

app = typer.Typer(help="Analyze agent sessions.")
console = Console()
err_console = Console(stderr=True)


def _print_quiet(result: AnalysisResult) -> None:
    """Print a one-line summary."""
    tm = result.token_metrics
    am = result.agent_metrics
    parts = [
        f"Sessions: {result.session_count}",
        f"Tokens: {format_tokens(tm.total_tokens)}",
        f"Cost: {format_cost(tm.total_cost)}",
        f"Agent invocations: {am.total_invocations}",
    ]
    if result.diagnostics and result.diagnostics.signals:
        parts.append(f"Diagnostic signals: {len(result.diagnostics.signals)}")
    console.print(" | ".join(parts))


def _print_json(result: AnalysisResult) -> None:
    """Print JSON output."""
    data = asdict(result)
    for session in data.get("sessions", []):
        if "session_path" in session:
            session["session_path"] = str(session["session_path"])
    # DiagnosticsResult is Pydantic; asdict() can't recurse into it.
    diag = result.diagnostics
    if diag:
        data["diagnostics"] = diag.model_dump(mode="json")
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
        help="Show detailed behavior diagnostics.",
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
        err_console.print(f"[red]Project not found:[/red] {project}")
        err_console.print("Use [bold]agentfluent list[/bold] to see available projects.")
        raise typer.Exit(code=2)

    session_infos = project_info.sessions
    if not session_infos:
        name = project_info.display_name
        err_console.print(f"[yellow]No sessions found for project:[/yellow] {name}")
        raise typer.Exit(code=2)

    if session:
        session_infos = [s for s in session_infos if s.filename == session]
        if not session_infos:
            err_console.print(f"[red]Session not found:[/red] {session}")
            raise typer.Exit(code=2)

    if latest is not None and latest > 0:
        session_infos = session_infos[:latest]

    paths = [s.path for s in session_infos]

    result = analyze_sessions(paths, agent_filter=agent)

    all_invocations = [inv for s in result.sessions for inv in s.invocations]
    total_subagent_traces = sum(si.subagent_count for si in session_infos)

    if all_invocations:
        result.diagnostics = run_diagnostics(
            all_invocations, subagent_trace_count=total_subagent_traces,
        )
    elif result.agent_metrics.total_invocations == 0 and diagnostics:
        console.print(
            "[dim]No agent invocations found -- "
            "diagnostics require agent activity.[/dim]"
        )

    if format == "json":
        _print_json(result)
    elif quiet:
        _print_quiet(result)
    else:
        format_analysis_table(
            console, result, verbose=verbose, show_diagnostics=diagnostics,
        )
