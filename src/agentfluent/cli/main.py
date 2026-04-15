"""AgentFluent CLI application."""

from typing import Optional

import typer

from agentfluent import __version__
from agentfluent.cli.commands import analyze, config_check, list_cmd

app = typer.Typer(
    name="agentfluent",
    help="Local-first agent analytics with prompt diagnostics.",
    no_args_is_help=True,
)

app.add_typer(list_cmd.app, name="list")
app.add_typer(analyze.app, name="analyze")
app.add_typer(config_check.app, name="config-check")


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
