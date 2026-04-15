"""agentfluent config-check -- assess agent configuration quality."""

from typing import Optional

import typer

app = typer.Typer(help="Check agent configuration quality.")


@app.callback(invoke_without_command=True)
def config_check(
    scope: str = typer.Option(
        "all",
        "--scope",
        help="Scanning scope: 'user', 'project', or 'all'.",
        show_choices=True,
    ),
    agent: Optional[str] = typer.Option(  # noqa: UP007, UP045
        None,
        "--agent",
        "-a",
        help="Score a specific agent by name.",
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
    """Scan agent definitions and score them against best practices."""
    typer.echo("Not yet implemented: config-check")
