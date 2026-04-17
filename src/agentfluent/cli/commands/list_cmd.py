"""agentfluent list -- discover projects and sessions."""

from __future__ import annotations

from typing import Optional

import typer
from rich.console import Console

from agentfluent.cli.exit_codes import EXIT_NO_DATA, EXIT_USER_ERROR
from agentfluent.cli.formatters.json_output import format_json_output
from agentfluent.cli.formatters.table import (
    format_projects_table,
    format_sessions_table,
)
from agentfluent.core.discovery import (
    ProjectInfo,
    discover_projects,
    find_project,
)
from agentfluent.core.parser import parse_session

LIST_EPILOG = """\
Examples:

  agentfluent list
      List all projects in ~/.claude/projects/.

  agentfluent list --project codefluent
      List sessions in the codefluent project.

  agentfluent list --format json | jq '.data.projects[].name'
      Extract project names (command is "list-projects").

  agentfluent list --project codefluent --format json | jq '.data.sessions[].filename'
      Extract session filenames (command is "list-sessions").
"""

app = typer.Typer(help="List projects and sessions.")
console = Console()
err_console = Console(stderr=True)


def _discover_or_exit() -> list[ProjectInfo]:
    """Discover projects; print error and exit on failure."""
    try:
        return discover_projects()
    except FileNotFoundError as e:
        err_console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=EXIT_NO_DATA) from None


def _list_projects_table(*, verbose: bool, quiet: bool) -> None:
    """Display all projects as a Rich table."""
    projects = _discover_or_exit()
    if quiet:
        total_sessions = sum(p.session_count for p in projects)
        console.print(f"{len(projects)} projects, {total_sessions} total sessions")
        return
    format_projects_table(console, projects, verbose=verbose)


def _list_projects_json(*, quiet: bool) -> None:
    """Output all projects as JSON."""
    projects = _discover_or_exit()
    if quiet:
        payload: dict[str, object] = {
            "project_count": len(projects),
            "total_sessions": sum(p.session_count for p in projects),
        }
    else:
        payload = {
            "projects": [
                {
                    "name": p.display_name,
                    "slug": p.slug,
                    "session_count": p.session_count,
                    "total_size_bytes": p.total_size_bytes,
                    "earliest_session": (
                        p.earliest_session.isoformat() if p.earliest_session else None
                    ),
                    "latest_session": (
                        p.latest_session.isoformat() if p.latest_session else None
                    ),
                }
                for p in projects
            ]
        }
    print(format_json_output("list-projects", payload))


def _find_or_exit(project_slug: str) -> ProjectInfo:
    """Look up a project by slug; print error and exit if not found."""
    project = find_project(project_slug)
    if project is None:
        err_console.print(f"[red]Project not found: {project_slug}[/red]")
        raise typer.Exit(code=EXIT_USER_ERROR)
    return project


def _list_sessions_table(project_slug: str, *, verbose: bool, quiet: bool) -> None:
    """Display sessions for a project as a Rich table."""
    project = _find_or_exit(project_slug)
    if quiet:
        console.print(f"Project {project.display_name}: {len(project.sessions)} sessions")
        return
    sessions = [(s, len(parse_session(s.path))) for s in project.sessions]
    format_sessions_table(console, project.display_name, sessions, verbose=verbose)


def _list_sessions_json(project_slug: str, *, quiet: bool) -> None:
    """Output sessions for a project as JSON."""
    project = _find_or_exit(project_slug)
    if quiet:
        payload: dict[str, object] = {
            "project": project.display_name,
            "session_count": len(project.sessions),
        }
    else:
        sessions = []
        for s in project.sessions:
            messages = parse_session(s.path)
            sessions.append(
                {
                    "filename": s.filename,
                    "size_bytes": s.size_bytes,
                    "modified": s.modified.isoformat(),
                    "message_count": len(messages),
                    "subagent_count": s.subagent_count,
                }
            )
        payload = {"project": project.display_name, "sessions": sessions}
    print(format_json_output("list-sessions", payload))


@app.callback(invoke_without_command=True, epilog=LIST_EPILOG)
def list_cmd(
    project: Optional[str] = typer.Option(  # noqa: UP007, UP045
        None,
        "--project",
        "-p",
        help="Project slug or name to list sessions for.",
    ),
    format: str = typer.Option(
        "table",
        "--format",
        "-f",
        help="Output format: 'table' or 'json'.",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show detailed output."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Show summary only."),
) -> None:
    """List available projects, or sessions within a project."""
    if verbose and quiet:
        raise typer.BadParameter("--verbose and --quiet are mutually exclusive")
    if project:
        if format == "json":
            _list_sessions_json(project, quiet=quiet)
        else:
            _list_sessions_table(project, verbose=verbose, quiet=quiet)
    else:
        if format == "json":
            _list_projects_json(quiet=quiet)
        else:
            _list_projects_table(verbose=verbose, quiet=quiet)
