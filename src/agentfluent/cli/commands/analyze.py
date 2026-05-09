"""agentfluent analyze -- compute execution analytics and diagnostics."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from agentfluent import __version__
from agentfluent.analytics.pipeline import AnalysisResult, analyze_sessions
from agentfluent.cli._time_args import parse_time_window
from agentfluent.cli.exit_codes import EXIT_NO_DATA, EXIT_USER_ERROR
from agentfluent.cli.formatters.helpers import format_cost, format_tokens
from agentfluent.cli.formatters.json_output import format_json_output
from agentfluent.cli.formatters.table import format_analysis_table
from agentfluent.config.mcp_discovery import resolve_project_disk_path
from agentfluent.config.models import SEVERITY_RANK, Severity
from agentfluent.core.discovery import SessionInfo, find_project
from agentfluent.core.filtering import WindowMetadata, filter_sessions_by_time
from agentfluent.core.paths import projects_dir_for
from agentfluent.diagnostics import run_diagnostics
from agentfluent.diagnostics.delegation import (
    DEFAULT_MIN_CLUSTER_SIZE,
    DEFAULT_MIN_SIMILARITY,
    SKLEARN_AVAILABLE,
)


def _apply_time_window(
    session_infos: list[SessionInfo],
    parsed_since: datetime | None,
    parsed_until: datetime | None,
    *,
    verbose: bool,
    err_console: Console,
) -> tuple[list[SessionInfo], WindowMetadata | None]:
    """Filter to ``[parsed_since, parsed_until)``; raise ``EXIT_NO_DATA`` on empty.

    Returns ``(session_infos, None)`` when neither bound is supplied so
    JSON consumers see ``window: null`` for unfiltered runs. Verbose
    mode prints a dim stderr note derived from the same metadata.
    """
    if parsed_since is None and parsed_until is None:
        return session_infos, None
    pre_filter_count = len(session_infos)
    filtered = filter_sessions_by_time(session_infos, parsed_since, parsed_until)
    if not filtered:
        err_console.print(
            "[yellow]No sessions found in the specified time window.[/yellow] "
            "Use [bold]agentfluent list --project P --since X --until Y[/bold] "
            "to preview which sessions fall in a window.",
        )
        raise typer.Exit(code=EXIT_NO_DATA)
    window = WindowMetadata(
        since=parsed_since,
        until=parsed_until,
        session_count_before_filter=pre_filter_count,
        session_count_after_filter=len(filtered),
    )
    if verbose:
        since_label = window.since.isoformat() if window.since else "—"
        until_label = window.until.isoformat() if window.until else "—"
        err_console.print(
            f"[dim]Filtering: sessions from {since_label} to {until_label} "
            f"({window.session_count_after_filter} of "
            f"{window.session_count_before_filter} sessions)[/dim]",
        )
    return filtered, window


def _apply_min_severity(result: AnalysisResult, min_severity: Severity) -> None:
    """Drop recommendations below the severity threshold (in place).

    Signals are left intact — the user opted to filter recommendations,
    not observations.
    """
    if result.diagnostics is None:
        return
    threshold = SEVERITY_RANK[min_severity]
    result.diagnostics.recommendations = [
        r for r in result.diagnostics.recommendations
        if SEVERITY_RANK[r.severity] >= threshold
    ]
    result.diagnostics.aggregated_recommendations = [
        a for a in result.diagnostics.aggregated_recommendations
        if SEVERITY_RANK[a.severity] >= threshold
    ]

ANALYZE_EPILOG = """\
Examples:

  agentfluent analyze --project codefluent
      Analyze all sessions in the codefluent project.

  agentfluent analyze --project codefluent --agent pm
      Analyze only PM agent invocations.

  agentfluent analyze --project codefluent --latest 5 --diagnostics
      Analyze the 5 most recent sessions with behavior diagnostics.

  agentfluent analyze --project codefluent --since 7d
      Analyze sessions whose first message landed in the last 7 days.

  agentfluent analyze --project codefluent --since 2026-05-01 --until 2026-05-08
      Analyze sessions in the half-open interval [2026-05-01, 2026-05-08).

  agentfluent analyze --project codefluent --since 7d --until 1d --diagnostics
      Analyze sessions from 7 days ago up to (but not including) yesterday.

  agentfluent analyze --project codefluent --since 2026-05-01 --json > baseline.json
      Generate a time-scoped baseline for `agentfluent diff` comparison.

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
    since: Optional[str] = typer.Option(  # noqa: UP007, UP045
        None,
        "--since",
        help=(
            "Restrict to sessions whose first message landed at or after "
            "this time. Accepts ISO 8601, date-only, or relative (7d, "
            "12h, 30m). Mutually exclusive with --session."
        ),
    ),
    until: Optional[str] = typer.Option(  # noqa: UP007, UP045
        None,
        "--until",
        help=(
            "Restrict to sessions whose first message landed strictly "
            "before this time (half-open interval). Same formats as "
            "--since. Mutually exclusive with --session."
        ),
    ),
    diagnostics: bool = typer.Option(
        True,
        "--diagnostics/--no-diagnostics",
        "-d/-D",
        help=(
            "Show detailed behavior diagnostics (default: on). "
            "Pass --no-diagnostics to skip the diagnostics pipeline."
        ),
    ),
    format: str = typer.Option(
        "table",
        "--format",
        "-f",
        help="Output format: table or json. Shortcut: --json.",
    ),
    json_flag: bool = typer.Option(
        False,
        "--json",
        help="Shortcut for --format json. Overrides --format when set.",
    ),
    min_cluster_size: Optional[int] = typer.Option(  # noqa: UP007, UP045
        None,
        "--min-cluster-size",
        help=(
            "Delegation clustering: minimum invocations per cluster "
            f"(default {DEFAULT_MIN_CLUSTER_SIZE}). Requires "
            "agentfluent[clustering]."
        ),
    ),
    min_similarity: Optional[float] = typer.Option(  # noqa: UP007, UP045
        None,
        "--min-similarity",
        help=(
            "Delegation dedup: cosine similarity threshold against existing "
            f"agents (default {DEFAULT_MIN_SIMILARITY}). Requires "
            "agentfluent[clustering]."
        ),
    ),
    top_n: int = typer.Option(
        5,
        "--top-n",
        help=(
            "Number of top-priority recommendations to summarize above the "
            "Recommendations table. Pass 0 to disable the summary block."
        ),
    ),
    min_severity: Optional[Severity] = typer.Option(  # noqa: UP007, UP045
        None,
        "--min-severity",
        case_sensitive=False,
        help=(
            "Drop recommendations below this severity. "
            "Choices: info, warning, critical. Filters both the default "
            "Recommendations table and the per-invocation --verbose surface; "
            "Diagnostic Signals are not affected."
        ),
    ),
    show_negative_savings: bool = typer.Option(
        False,
        "--show-negative-savings",
        help=(
            "Include offload candidates whose savings is zero or negative "
            "(offloading would cost MORE than staying on the parent thread). "
            "Hidden by default — these patterns are informational, not "
            "actionable. JSON output always carries the full list."
        ),
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show detailed output."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Show summary only."),
) -> None:
    """Analyze agent sessions for token usage, cost, and behavior diagnostics."""
    if verbose and quiet:
        raise typer.BadParameter("--verbose and --quiet are mutually exclusive")

    if session is not None and (since is not None or until is not None):
        err_console.print(
            "[red]Error:[/red] --since/--until cannot be combined with "
            "--session (which selects a specific file).",
        )
        raise typer.Exit(code=EXIT_USER_ERROR)

    parsed_since, parsed_until = parse_time_window(
        since, until, err_console=err_console,
    )

    if json_flag:
        format = "json"

    # Fail fast if the user explicitly asked for clustering-tuning behavior
    # but the optional extra is not installed. When both flags are left at
    # their defaults (None), clustering is silently skipped if sklearn is
    # absent — the lean install stays usable.
    if (
        min_cluster_size is not None or min_similarity is not None
    ) and not SKLEARN_AVAILABLE:
        err_console.print(
            "[red]Error:[/red] Delegation clustering requires scikit-learn. "
            "Install with: [bold]uv pip install 'agentfluent[clustering]'[/bold]",
        )
        raise typer.Exit(code=EXIT_USER_ERROR)

    config_dir: Path | None = ctx.obj.claude_config_dir if ctx.obj else None

    project_info = find_project(project, base_path=projects_dir_for(config_dir))
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

    session_infos, window_metadata = _apply_time_window(
        session_infos, parsed_since, parsed_until,
        verbose=verbose, err_console=err_console,
    )

    if latest is not None and latest > 0:
        session_infos = session_infos[:latest]

    paths = [s.path for s in session_infos]

    result = analyze_sessions(paths, agent_filter=agent)

    all_invocations = [inv for s in result.sessions for inv in s.invocations]
    all_mcp_calls = [c for s in result.sessions for c in s.mcp_tool_calls]
    all_messages = [m for s in result.sessions for m in s.messages]

    if all_invocations and diagnostics:
        # `project_info.path` is the ~/.claude/projects/<slug>/ dir, not
        # the original project source path. MCP discovery needs the
        # real path (for .mcp.json and ~/.claude.json:projects[<abs>]
        # lookups); resolve it via the slug.
        project_disk_path = resolve_project_disk_path(
            project_info.slug, claude_config_dir=config_dir,
        )
        result.diagnostics = run_diagnostics(
            all_invocations,
            min_cluster_size=(
                min_cluster_size if min_cluster_size is not None
                else DEFAULT_MIN_CLUSTER_SIZE
            ),
            min_similarity=(
                min_similarity if min_similarity is not None
                else DEFAULT_MIN_SIMILARITY
            ),
            mcp_tool_calls=all_mcp_calls,
            claude_config_dir=config_dir,
            project_dir=project_disk_path,
            parent_messages=all_messages,
        )
    elif result.agent_metrics.total_invocations == 0 and diagnostics:
        err_console.print(
            "[dim]No agent invocations found -- "
            "diagnostics require agent activity.[/dim]"
        )

    if min_severity is not None:
        _apply_min_severity(result, min_severity)

    result.window = window_metadata
    result.diagnostics_version = __version__

    if format == "json":
        _print_json(result, quiet=quiet, project_name=project_info.display_name)
    elif quiet:
        _print_quiet(result, project_info.display_name)
    else:
        format_analysis_table(
            console, result, verbose=verbose, show_diagnostics=diagnostics,
            top_n=top_n, show_negative_savings=show_negative_savings,
        )
