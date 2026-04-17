"""AgentFluent CLI application."""

from typing import Optional

import typer

from agentfluent import __version__
from agentfluent.cli.commands import analyze, config_check, list_cmd

TOP_LEVEL_HELP = """\
Local-first agent analytics for the Claude Agent SDK and Claude Code subagents.

AgentFluent analyzes session JSONL files in ~/.claude/projects/ to diagnose
agent quality -- token usage, tool patterns, behavior signals, and config
health -- and produces specific recommendations for improving agent prompts,
tool access, model selection, and other configuration surfaces.
"""

TOP_LEVEL_EPILOG = """\
Common workflows:

  agentfluent list
      Discover which projects have session data.

  agentfluent analyze --project <slug> --diagnostics
      Full analytics with behavior diagnostics.

  agentfluent config-check
      Score agent definitions against best practices.

Run any command with --help for command-specific options and examples.
"""

app = typer.Typer(
    name="agentfluent",
    help=TOP_LEVEL_HELP,
    epilog=TOP_LEVEL_EPILOG,
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
