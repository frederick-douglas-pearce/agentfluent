"""agentfluent list -- discover projects and sessions."""

from typing import Optional

import typer

app = typer.Typer(help="List projects and sessions.")


@app.callback(invoke_without_command=True)
def list_projects(
    project: Optional[str] = typer.Option(  # noqa: UP007, UP045
        None,
        "--project",
        "-p",
        help="Project slug to list sessions for.",
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
    """List available projects, or sessions within a project."""
    typer.echo("Not yet implemented: list")
