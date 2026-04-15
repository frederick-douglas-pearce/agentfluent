"""agentfluent analyze -- compute execution analytics and diagnostics."""

from typing import Optional

import typer

app = typer.Typer(help="Analyze agent sessions.")


@app.callback(invoke_without_command=True)
def analyze(
    project: str = typer.Option(
        ...,
        "--project",
        "-p",
        help="Project slug to analyze.",
    ),
    session: Optional[str] = typer.Option(  # noqa: UP007, UP045
        None,
        "--session",
        "-s",
        help="Specific session file to analyze.",
    ),
    agent: Optional[str] = typer.Option(  # noqa: UP007, UP045
        None,
        "--agent",
        "-a",
        help="Filter to a specific agent type (e.g., 'pm').",
    ),
    diagnostics: bool = typer.Option(
        False,
        "--diagnostics",
        "-d",
        help="Show detailed diagnostics.",
    ),
    format: str = typer.Option(
        "table",
        "--format",
        "-f",
        help="Output format.",
        show_choices=True,
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show detailed output."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Show summary only."),
) -> None:
    """Analyze agent sessions for token usage, cost, and behavior diagnostics."""
    typer.echo("Not yet implemented: analyze")
