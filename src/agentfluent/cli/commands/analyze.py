"""agentfluent analyze -- compute execution analytics and diagnostics."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from agentfluent.analytics.pipeline import AnalysisResult, analyze_sessions
from agentfluent.cli.exit_codes import EXIT_NO_DATA, EXIT_USER_ERROR
from agentfluent.cli.formatters.helpers import format_cost, format_tokens
from agentfluent.cli.formatters.json_output import format_json_output
from agentfluent.cli.formatters.table import format_analysis_table
from agentfluent.core.discovery import find_project
from agentfluent.diagnostics import run_diagnostics

ANALYZE_EPILOG = """\
Examples:

  agentfluent analyze --project codefluent
      Analyze all sessions in the codefluent project.

  agentfluent analyze --project codefluent --agent pm
      Analyze only PM agent invocations.

  agentfluent analyze --project codefluent --latest 5 --diagnostics
      Analyze the 5 most recent sessions with behavior diagnostics.

  agentfluent analyze --project codefluent --format json | jq '.data.token_metrics.total_cost'
      Extract total cost programmatically.
"""

app = typer.Typer(help="Analyze agent sessions.")
console = Console()
err_console = Console(stderr=True)


def _print_quiet(result: AnalysisResult, project_name: str) -> None:
    """Print a one-line summary."""
    tm = result.token_metrics
    am = result.agent_metrics
    signal_count = len(result.diagnostics.signals) if result.diagnostics else 0
    console.print(
        f"Project {project_name}: "
        f"{format_cost(tm.total_cost)} cost, "
        f"{format_tokens(tm.total_tokens)} tokens, "
        f"{am.total_invocations} agent invocations, "
        f"{signal_count} diagnostic signals"
    )


def _print_json(result: AnalysisResult, *, quiet: bool, project_name: str) -> None:
    """Print JSON output. Quiet emits a minimal summary; default emits the full tree."""
    if quiet:
        tm = result.token_metrics
        am = result.agent_metrics
        signal_count = len(result.diagnostics.signals) if result.diagnostics else 0
        payload: dict[str, object] = {
            "project": project_name,
            "session_count": result.session_count,
            "total_cost": tm.total_cost,
            "total_tokens": tm.total_tokens,
            "total_invocations": am.total_invocations,
            "diagnostic_signal_count": signal_count,
        }
    else:
        payload = result.model_dump(mode="json")
    print(format_json_output("analyze", payload))


@app.callback(invoke_without_command=True, epilog=ANALYZE_EPILOG)
def analyze(
    ctx: typer.Context,
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
    if verbose and quiet:
        raise typer.BadParameter("--verbose and --quiet are mutually exclusive")

    config_dir: Path | None = ctx.obj.claude_config_dir if ctx.obj else None
    projects_dir = (config_dir / "projects") if config_dir else None

    project_info = find_project(project, base_path=projects_dir)
    if project_info is None:
        err_console.print(f"[red]Project not found:[/red] {project}")
        err_console.print("Use [bold]agentfluent list[/bold] to see available projects.")
        raise typer.Exit(code=EXIT_USER_ERROR)

    session_infos = project_info.sessions
    if not session_infos:
        name = project_info.display_name
        err_console.print(f"[yellow]No sessions found for project:[/yellow] {name}")
        raise typer.Exit(code=EXIT_NO_DATA)

    if session:
        session_infos = [s for s in session_infos if s.filename == session]
        if not session_infos:
            err_console.print(f"[red]Session not found:[/red] {session}")
            raise typer.Exit(code=EXIT_USER_ERROR)

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
        _print_json(result, quiet=quiet, project_name=project_info.display_name)
    elif quiet:
        _print_quiet(result, project_info.display_name)
    else:
        format_analysis_table(
            console, result, verbose=verbose, show_diagnostics=diagnostics,
        )
