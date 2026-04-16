"""agentfluent config-check -- assess agent configuration quality."""

from __future__ import annotations

import json
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from agentfluent.config import assess_agents
from agentfluent.config.models import ConfigScore, Severity

app = typer.Typer(help="Check agent configuration quality.")
console = Console()

# Severity -> Rich color mapping
_SEVERITY_COLORS: dict[Severity, str] = {
    Severity.CRITICAL: "red",
    Severity.WARNING: "yellow",
    Severity.INFO: "cyan",
}


def _score_color(score: int) -> str:
    """Return a Rich color based on score value."""
    if score >= 80:
        return "green"
    if score >= 50:
        return "yellow"
    return "red"


def _print_quiet(scores: list[ConfigScore]) -> None:
    """Print a one-line summary."""
    avg = sum(s.overall_score for s in scores) // len(scores) if scores else 0
    total_recs = sum(len(s.recommendations) for s in scores)
    console.print(
        f"Agents: {len(scores)} | "
        f"Avg score: {avg}/100 | "
        f"Recommendations: {total_recs}"
    )


def _print_table(scores: list[ConfigScore], *, verbose: bool = False) -> None:
    """Print Rich-formatted scoring tables."""
    # Summary table
    summary = Table(title="Agent Configuration Scores", show_header=True)
    summary.add_column("Agent", style="cyan")
    summary.add_column("Score", justify="right")
    summary.add_column("Description", justify="right")
    summary.add_column("Tools", justify="right")
    summary.add_column("Model", justify="right")
    summary.add_column("Prompt", justify="right")
    summary.add_column("Recs", justify="right")

    for s in scores:
        color = _score_color(s.overall_score)
        summary.add_row(
            s.agent_name,
            f"[{color}]{s.overall_score}/100[/{color}]",
            f"{s.dimension_scores.get('description', 0)}/25",
            f"{s.dimension_scores.get('tool_restrictions', 0)}/25",
            f"{s.dimension_scores.get('model_selection', 0)}/25",
            f"{s.dimension_scores.get('prompt_body', 0)}/25",
            str(len(s.recommendations)),
        )
    console.print(summary)

    # Recommendations
    all_recs = [(s.agent_name, r) for s in scores for r in s.recommendations]
    if all_recs:
        rec_table = Table(title="Recommendations", show_header=True)
        rec_table.add_column("Agent", style="cyan")
        rec_table.add_column("Severity")
        rec_table.add_column("Recommendation")
        if verbose:
            rec_table.add_column("Action")

        for agent_name, rec in all_recs:
            color = _SEVERITY_COLORS.get(rec.severity, "white")
            row = [
                agent_name,
                f"[{color}]{rec.severity.value}[/{color}]",
                rec.message,
            ]
            if verbose:
                row.append(rec.suggested_action)
            rec_table.add_row(*row)
        console.print(rec_table)

    # Summary line
    avg = sum(s.overall_score for s in scores) // len(scores) if scores else 0
    console.print(
        f"\n[bold]Agents scanned:[/bold] {len(scores)}, "
        f"[bold]average score:[/bold] {avg}/100, "
        f"[bold]recommendations:[/bold] {len(all_recs)}"
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
        console.print(f"[red]Invalid scope:[/red] {scope}")
        console.print("Valid scopes: user, project, all")
        raise SystemExit(2)

    scores = assess_agents(scope, agent_filter=agent)

    if not scores:
        if agent:
            console.print(f"[yellow]No agent found named:[/yellow] {agent}")
        else:
            console.print("[yellow]No agent definition files found.[/yellow]")
            console.print(
                "Agent definitions are `.md` files in "
                "`~/.claude/agents/` (user) or `.claude/agents/` (project)."
            )
        raise SystemExit(2)

    if format == "json":
        _print_json(scores)
    elif quiet:
        _print_quiet(scores)
    else:
        _print_table(scores, verbose=verbose)
