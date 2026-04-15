"""AgentFluent CLI application."""

from typing import Optional

import typer

from agentfluent import __version__

app = typer.Typer(
    name="agentfluent",
    help="Local-first agent analytics with prompt diagnostics.",
    no_args_is_help=True,
)


def version_callback(value: bool) -> None:
    if value:
        typer.echo(f"agentfluent {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Optional[bool] = typer.Option(  # noqa: UP007, UP045
        None,
        "--version",
        "-V",
        help="Show version and exit.",
        callback=version_callback,
        is_eager=True,
    ),
) -> None:
    """Local-first agent analytics with prompt diagnostics."""
