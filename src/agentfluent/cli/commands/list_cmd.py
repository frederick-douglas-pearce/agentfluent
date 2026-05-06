"""agentfluent list -- discover projects and sessions."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from agentfluent.cli._time_args import parse_time_window
from agentfluent.cli.exit_codes import EXIT_NO_DATA, EXIT_USER_ERROR
from agentfluent.cli.formatters.json_output import format_json_output
from agentfluent.cli.formatters.table import (
    format_projects_table,
    format_sessions_table,
)
from agentfluent.core.discovery import (
    ProjectInfo,
    SessionInfo,
    discover_projects,
    find_project,
)
from agentfluent.core.filtering import filter_sessions_by_time
from agentfluent.core.parser import parse_session
from agentfluent.core.paths import projects_dir_for

LIST_EPILOG = """\
Examples:

  agentfluent list
      List all projects in ~/.claude/projects/.

  agentfluent list --project codefluent
      List sessions in the codefluent project.

  agentfluent list --project codefluent --since 7d
      Sessions whose first message landed in the last 7 days.

  agentfluent list --project codefluent --since 2026-05-01 --until 2026-05-08
      Sessions in the half-open interval [2026-05-01, 2026-05-08).

  agentfluent list --format json | jq '.data.projects[].name'
      Extract project names (command is "list-projects").

  agentfluent list --project codefluent --format json | jq '.data.sessions[].filename'
      Extract session filenames (command is "list-sessions").
"""

app = typer.Typer(help="List projects and sessions.")
console = Console()
err_console = Console(stderr=True)


def _discover_or_exit(config_dir: Path | None) -> list[ProjectInfo]:
    """Discover projects; print error and exit on failure."""
    try:
        return discover_projects(base_path=projects_dir_for(config_dir))
    except FileNotFoundError as e:
        err_console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=EXIT_NO_DATA) from None


def _list_projects_table(
    *, config_dir: Path | None, verbose: bool, quiet: bool,
) -> None:
    """Display all projects as a Rich table."""
    projects = _discover_or_exit(config_dir)
    if quiet:
        total_sessions = sum(p.session_count for p in projects)
        console.print(f"{len(projects)} projects, {total_sessions} total sessions")
        return
    format_projects_table(console, projects, verbose=verbose)


def _list_projects_json(*, config_dir: Path | None, quiet: bool) -> None:
    """Output all projects as JSON."""
    projects = _discover_or_exit(config_dir)
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


def _find_or_exit(project_slug: str, config_dir: Path | None) -> ProjectInfo:
    """Look up a project by slug; print error and exit if not found."""
    project = find_project(project_slug, base_path=projects_dir_for(config_dir))
    if project is None:
        err_console.print(f"[red]Project not found: {project_slug}[/red]")
        raise typer.Exit(code=EXIT_USER_ERROR)
    return project


def _list_sessions_table(
    project: ProjectInfo,
    sessions: list[SessionInfo],
    *,
    verbose: bool,
    quiet: bool,
) -> None:
    """Display sessions for a project as a Rich table."""
    if quiet:
        console.print(f"Project {project.display_name}: {len(sessions)} sessions")
        return
    rows = [(s, len(parse_session(s.path))) for s in sessions]
    format_sessions_table(console, project.display_name, rows, verbose=verbose)


def _list_sessions_json(
    project: ProjectInfo,
    sessions: list[SessionInfo],
    *,
    quiet: bool,
) -> None:
    """Output sessions for a project as JSON."""
    if quiet:
        payload: dict[str, object] = {
            "project": project.display_name,
            "session_count": len(sessions),
        }
    else:
        rows = []
        for s in sessions:
            messages = parse_session(s.path)
            rows.append(
                {
                    "filename": s.filename,
                    "size_bytes": s.size_bytes,
                    "modified": s.modified.isoformat(),
                    "message_count": len(messages),
                    "subagent_count": s.subagent_count,
                }
            )
        payload = {"project": project.display_name, "sessions": rows}
    print(format_json_output("list-sessions", payload))


@app.callback(invoke_without_command=True, epilog=LIST_EPILOG)
def list_cmd(
    ctx: typer.Context,
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
        help="Output format: 'table' or 'json'. Shortcut: --json.",
    ),
    json_flag: bool = typer.Option(
        False,
        "--json",
        help="Shortcut for --format json. Overrides --format when set.",
    ),
    since: Optional[str] = typer.Option(  # noqa: UP007, UP045
        None,
        "--since",
        help=(
            "Include sessions whose first message landed at or after this "
            "time. Accepts ISO 8601 (2026-05-05T12:00:00), date-only "
            "(2026-05-05), or relative (7d, 12h, 30m). Requires --project."
        ),
    ),
    until: Optional[str] = typer.Option(  # noqa: UP007, UP045
        None,
        "--until",
        help=(
            "Include sessions whose first message landed strictly before "
            "this time (half-open interval). Same formats as --since. "
            "Requires --project."
        ),
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show detailed output."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Show summary only."),
) -> None:
    """List available projects, or sessions within a project."""
    if verbose and quiet:
        raise typer.BadParameter("--verbose and --quiet are mutually exclusive")
    if (since is not None or until is not None) and project is None:
        err_console.print(
            "[red]Error:[/red] --since/--until require --project "
            "(time filtering applies to sessions, not the project list).",
        )
        raise typer.Exit(code=EXIT_USER_ERROR)
    if json_flag:
        format = "json"
    config_dir = ctx.obj.claude_config_dir if ctx.obj else None
    if project:
        project_info = _find_or_exit(project, config_dir)
        sessions = project_info.sessions
        if since is not None or until is not None:
            parsed_since, parsed_until = parse_time_window(
                since, until, err_console=err_console,
            )
            sessions = filter_sessions_by_time(
                sessions, parsed_since, parsed_until,
            )
        if format == "json":
            _list_sessions_json(project_info, sessions, quiet=quiet)
        else:
            _list_sessions_table(
                project_info, sessions, verbose=verbose, quiet=quiet,
            )
    else:
        if format == "json":
            _list_projects_json(config_dir=config_dir, quiet=quiet)
        else:
            _list_projects_table(
                config_dir=config_dir, verbose=verbose, quiet=quiet,
            )
