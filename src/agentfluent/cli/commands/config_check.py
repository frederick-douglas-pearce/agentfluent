"""agentfluent config-check -- assess agent configuration quality."""

from __future__ import annotations

import json
from typing import Optional

import typer
from rich.console import Console

from agentfluent.cli.formatters.table import format_config_check_table
from agentfluent.config import assess_agents
from agentfluent.config.models import ConfigScore

app = typer.Typer(help="Check agent configuration quality.")
console = Console()
err_console = Console(stderr=True)


def _print_quiet(scores: list[ConfigScore]) -> None:
    """Print a one-line summary."""
    avg = sum(s.overall_score for s in scores) // len(scores) if scores else 0
    total_recs = sum(len(s.recommendations) for s in scores)
    console.print(
        f"Agents: {len(scores)} | "
        f"Avg score: {avg}/100 | "
        f"Recommendations: {total_recs}"
    )


def _print_json(scores: list[ConfigScore]) -> None:
    """Print JSON output."""
    data = [s.model_dump(mode="json") for s in scores]
    console.print_json(json.dumps(data, default=str))


@app.callback(invoke_without_command=True)
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
    if scope not in ("user", "project", "all"):
        err_console.print(f"[red]Invalid scope:[/red] {scope}")
        err_console.print("Valid scopes: user, project, all")
        raise typer.Exit(code=2)

    scores = assess_agents(scope, agent_filter=agent)

    if not scores:
        if agent:
            err_console.print(f"[yellow]No agent found named:[/yellow] {agent}")
        else:
            err_console.print("[yellow]No agent definition files found.[/yellow]")
            err_console.print(
                "Agent definitions are `.md` files in "
                "`~/.claude/agents/` (user) or `.claude/agents/` (project)."
            )
        raise typer.Exit(code=2)

    if format == "json":
        _print_json(scores)
    elif quiet:
        _print_quiet(scores)
    else:
        format_config_check_table(console, scores, verbose=verbose)
