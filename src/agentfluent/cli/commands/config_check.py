"""agentfluent config-check -- assess agent configuration quality."""

from __future__ import annotations

from typing import Optional

import typer
from rich.console import Console

from agentfluent.cli.exit_codes import EXIT_NO_DATA, EXIT_USER_ERROR
from agentfluent.cli.formatters.helpers import average_score
from agentfluent.cli.formatters.json_output import format_json_output
from agentfluent.cli.formatters.table import format_config_check_table
from agentfluent.config import assess_agents
from agentfluent.config.models import ConfigScore

CONFIG_CHECK_EPILOG = """\
Examples:

  agentfluent config-check
      Score all user and project agent definitions.

  agentfluent config-check --scope user
      Check only user-level agents in ~/.claude/agents/.

  agentfluent config-check --agent pm --verbose
      Score a specific agent with detailed recommendations.

  agentfluent config-check --format json | jq '.data.scores[] | select(.overall_score < 60)'
      Find agents that need improvement.
"""

app = typer.Typer(help="Check agent configuration quality.")
console = Console()
err_console = Console(stderr=True)


def _print_quiet(scores: list[ConfigScore]) -> None:
    """Print a one-line summary."""
    total_recs = sum(len(s.recommendations) for s in scores)
    console.print(
        f"Agents: {len(scores)} | "
        f"Avg score: {average_score(scores)}/100 | "
        f"Recommendations: {total_recs}"
    )


def _print_json(scores: list[ConfigScore], *, quiet: bool) -> None:
    """Print JSON output. Quiet emits a minimal summary; default emits all scores."""
    if quiet:
        payload: dict[str, object] = {
            "agent_count": len(scores),
            "average_score": average_score(scores),
            "recommendation_count": sum(len(s.recommendations) for s in scores),
        }
    else:
        payload = {"scores": [s.model_dump(mode="json") for s in scores]}
    print(format_json_output("config-check", payload))


@app.callback(invoke_without_command=True, epilog=CONFIG_CHECK_EPILOG)
def config_check(
    scope: str = typer.Option(
        "all",
        "--scope",
        help="Scanning scope: 'user', 'project', or 'all'.",
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
        help="Output format: table or json.",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show detailed output."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Show summary only."),
) -> None:
    """Scan agent definitions and score them against best practices."""
    if verbose and quiet:
        raise typer.BadParameter("--verbose and --quiet are mutually exclusive")

    if scope not in ("user", "project", "all"):
        err_console.print(f"[red]Invalid scope:[/red] {scope}")
        err_console.print("Valid scopes: user, project, all")
        raise typer.Exit(code=EXIT_USER_ERROR)

    scores = assess_agents(scope, agent_filter=agent)

    if not scores:
        if agent:
            err_console.print(f"[yellow]No agent found named:[/yellow] {agent}")
            raise typer.Exit(code=EXIT_USER_ERROR)
        err_console.print("[yellow]No agent definition files found.[/yellow]")
        err_console.print(
            "Agent definitions are `.md` files in "
            "`~/.claude/agents/` (user) or `.claude/agents/` (project)."
        )
        raise typer.Exit(code=EXIT_NO_DATA)

    if format == "json":
        _print_json(scores, quiet=quiet)
    elif quiet:
        _print_quiet(scores)
    else:
        format_config_check_table(console, scores, verbose=verbose)
