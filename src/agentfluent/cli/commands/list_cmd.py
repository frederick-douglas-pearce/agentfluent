"""agentfluent list -- discover projects and sessions."""

from __future__ import annotations

from typing import Optional

import typer
from rich.console import Console

from agentfluent.cli.formatters.json_output import format_json_output
from agentfluent.cli.formatters.table import (
    format_projects_table,
    format_sessions_table,
)
from agentfluent.core.discovery import discover_projects, find_project
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


def _list_projects_table() -> None:
    """Display all projects as a Rich table."""
    try:
        projects = discover_projects()
    except FileNotFoundError as e:
        err_console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1) from None

    format_projects_table(console, projects)


def _list_projects_json() -> None:
    """Output all projects as JSON."""
    try:
        projects = discover_projects()
    except FileNotFoundError as e:
        err_console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1) from None

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
                "latest_session": p.latest_session.isoformat() if p.latest_session else None,
            }
            for p in projects
        ]
    }
    print(format_json_output("list-projects", payload))


def _list_sessions_table(project_slug: str) -> None:
    """Display sessions for a project as a Rich table."""
    project = find_project(project_slug)
    if project is None:
        err_console.print(f"[red]Project not found: {project_slug}[/red]")
        raise typer.Exit(code=1)

    sessions = [(s, len(parse_session(s.path))) for s in project.sessions]
    format_sessions_table(console, project.display_name, sessions)


def _list_sessions_json(project_slug: str) -> None:
    """Output sessions for a project as JSON."""
    project = find_project(project_slug)
    if project is None:
        err_console.print(f"[red]Project not found: {project_slug}[/red]")
        raise typer.Exit(code=1)

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
    if project:
        if format == "json":
            _list_sessions_json(project)
        else:
            _list_sessions_table(project)
    else:
        if format == "json":
            _list_projects_json()
        else:
            _list_projects_table()
