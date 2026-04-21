"""AgentFluent CLI application."""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from agentfluent import __version__
from agentfluent.cli.commands import analyze, config_check, list_cmd
from agentfluent.cli.exit_codes import EXIT_USER_ERROR
from agentfluent.core.paths import validate_claude_config_dir

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

  agentfluent --claude-config-dir /custom/path list
      Point at a non-default Claude config directory (also honors
      $CLAUDE_CONFIG_DIR).

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

err_console = Console(stderr=True)


@dataclass
class CliState:
    """State passed from the top-level callback to subcommands via ``ctx.obj``."""

    claude_config_dir: Path | None = None


def version_callback(value: bool) -> None:
    if value:
        typer.echo(f"agentfluent {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    ctx: typer.Context,
    version: Optional[bool] = typer.Option(  # noqa: UP007, UP045
        None,
        "--version",
        "-V",
        help="Show version and exit.",
        callback=version_callback,
        is_eager=True,
    ),
    claude_config_dir: Optional[Path] = typer.Option(  # noqa: UP007, UP045
        None,
        "--claude-config-dir",
        envvar="CLAUDE_CONFIG_DIR",
        help=(
            "Override the Claude config directory. Defaults to ~/.claude. "
            "Project-scope paths (.claude/ under the current directory) are "
            "not affected."
        ),
    ),
) -> None:
    """Local-first agent analytics with prompt diagnostics."""
    try:
        resolved = validate_claude_config_dir(claude_config_dir)
    except (FileNotFoundError, NotADirectoryError) as e:
        err_console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=EXIT_USER_ERROR) from None

    ctx.obj = CliState(claude_config_dir=resolved)
