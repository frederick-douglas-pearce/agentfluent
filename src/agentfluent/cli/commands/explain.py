"""agentfluent explain -- terminal-native glossary lookup.

Three usage paths:

* ``agentfluent explain <term>`` -- exact match, falls back to fuzzy on miss.
* ``agentfluent explain --list`` (or no args) -- flat table of every term.
* ``agentfluent explain --category <category>`` -- table filtered by category.
"""

from __future__ import annotations

from typing import Optional

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

from agentfluent.cli.exit_codes import EXIT_OK, EXIT_USER_ERROR
from agentfluent.glossary import (
    GLOSSARY_CATEGORIES,
    GlossaryEntry,
    load_glossary,
)
from agentfluent.glossary.loader import find_term, fuzzy_match

EXPLAIN_EPILOG = """\
Examples:

  agentfluent explain token_outlier
      Look up a term seen in CLI output.

  agentfluent explain tokens-outliers
      Fuzzy match -- normalization + substring match handles typos and
      hyphen/underscore mixups.

  agentfluent explain --list
      Show every term grouped by category.

  agentfluent explain --category signal_type
      Show only the signal-type terms.
"""

app = typer.Typer(help="Look up an AgentFluent term.")
console = Console()
err_console = Console(stderr=True)


def _category_label(category: str) -> str:
    """Return the display label for a category, falling back to the raw key."""
    for cat, label in GLOSSARY_CATEGORIES:
        if cat == category:
            return label
    return category


def _render_entry(entry: GlossaryEntry, *, prefix_note: str = "") -> None:
    """Render a single entry as a Rich panel."""
    body_parts: list[str] = []
    if prefix_note:
        body_parts.append(f"_{prefix_note}_\n")
    body_parts.append(f"**{entry.short.strip()}**")
    if entry.long:
        body_parts.append(entry.long.rstrip())
    if entry.example:
        body_parts.append("**Example:**\n```\n" + entry.example.rstrip() + "\n```")
    if entry.severity_range:
        body_parts.append(f"**Severity:** {entry.severity_range}")
    if entry.threshold:
        body_parts.append(f"**Threshold:** {entry.threshold}")
    if entry.recommendation_target:
        body_parts.append(
            f"**Recommendation target:** `{entry.recommendation_target}`",
        )
    if entry.aliases:
        alias_str = ", ".join(f"`{a}`" for a in entry.aliases)
        body_parts.append(f"**Aliases:** {alias_str}")
    if entry.related:
        related_str = ", ".join(f"`{r}`" for r in entry.related)
        body_parts.append(f"**Related:** {related_str}")

    title = f"[bold cyan]{entry.name}[/bold cyan] [dim]({_category_label(entry.category)})[/dim]"
    console.print(Panel(Markdown("\n\n".join(body_parts)), title=title, expand=False))


def _render_table(entries: list[GlossaryEntry], *, title: str) -> None:
    """Render a flat list of entries grouped by category section."""
    if not entries:
        console.print(f"[yellow]{title}: no terms found.[/yellow]")
        return
    table = Table(title=title, show_lines=False, expand=False)
    table.add_column("Term", style="cyan", no_wrap=True)
    table.add_column("Category", style="dim")
    table.add_column("Short", overflow="fold")

    by_cat: dict[str, list[GlossaryEntry]] = {}
    for entry in entries:
        by_cat.setdefault(entry.category, []).append(entry)
    for cat, _label in GLOSSARY_CATEGORIES:
        for entry in by_cat.get(cat, []):
            table.add_row(entry.name, _category_label(cat), entry.short.strip())
    console.print(table)


def _resolve_category(value: str) -> str | None:
    """Return the canonical category key for ``value`` or None if unknown.

    Match is case-insensitive against either the canonical key or the
    display label.
    """
    needle = value.strip().lower()
    for cat, label in GLOSSARY_CATEGORIES:
        if cat.lower() == needle or label.lower() == needle:
            return cat
    return None


def _explain_lookup(term: str, entries: list[GlossaryEntry]) -> int:
    """Look up a term, falling back to fuzzy match. Returns an exit code."""
    exact = find_term(term, entries)
    if exact is not None:
        _render_entry(exact)
        return EXIT_OK

    candidates = fuzzy_match(term, entries)
    if len(candidates) == 1:
        only = candidates[0]
        note = (
            f"No exact match for {term!r}. Closest: `{only.name}`."
        )
        _render_entry(only, prefix_note=note)
        return EXIT_OK
    if 2 <= len(candidates) <= 5:
        err_console.print(
            f"[yellow]No exact match for {term!r}. Did you mean:[/yellow]",
        )
        for c in candidates:
            err_console.print(f"  [cyan]{c.name}[/cyan] -- {c.short.strip()}")
        return EXIT_USER_ERROR
    err_console.print(
        f"[red]Term {term!r} not found.[/red] "
        "Run [cyan]agentfluent explain --list[/cyan] to see all terms.",
    )
    return EXIT_USER_ERROR


@app.callback(invoke_without_command=True, epilog=EXPLAIN_EPILOG)
def explain_cmd(
    term: Optional[str] = typer.Argument(  # noqa: UP007, UP045
        None,
        help="Term to look up (exact or fuzzy match).",
    ),
    list_all: bool = typer.Option(
        False,
        "--list",
        help="List every term, grouped by category.",
    ),
    category: Optional[str] = typer.Option(  # noqa: UP007, UP045
        None,
        "--category",
        "-c",
        help="Filter by category (e.g., 'signal_type').",
    ),
) -> None:
    """Look up an AgentFluent term."""
    entries = load_glossary()

    if category:
        canonical = _resolve_category(category)
        if canonical is None:
            valid = ", ".join(c for c, _ in GLOSSARY_CATEGORIES)
            err_console.print(
                f"[red]Unknown category {category!r}.[/red] Valid: {valid}",
            )
            raise typer.Exit(code=EXIT_USER_ERROR)
        filtered = [e for e in entries if e.category == canonical]
        _render_table(
            filtered,
            title=f"Terms in category: {_category_label(canonical)}",
        )
        return

    if list_all or term is None:
        _render_table(entries, title="AgentFluent glossary")
        return

    raise typer.Exit(code=_explain_lookup(term, entries))
