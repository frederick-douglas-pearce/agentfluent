"""Rich tables for ``agentfluent diff`` output.

Three sections — Recommendations (new / resolved / persisting), Token
Metrics, Per-Agent — laid out so a regression jumps off the page in
table mode but everything serializes losslessly to JSON.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.markup import escape
from rich.table import Table

from agentfluent.cli.formatters.helpers import (
    AXIS_COLORS,
    GLOBAL_AGENT_LABEL,
    axis_label,
    format_cost,
    format_tokens,
    severity_cell,
    truncate,
)

if TYPE_CHECKING:
    from rich.console import Console

    from agentfluent.diff.models import (
        DiffResult,
        ModelTokenDelta,
        RecommendationDelta,
    )


def format_diff_table(
    console: Console,
    result: DiffResult,
    *,
    top_n: int,
    verbose: bool = False,
) -> None:
    """Render the full diff to ``console``.

    ``top_n`` truncates the new/resolved/persisting recommendation
    sections; pass ``0`` to show all. ``verbose`` shows zero-delta agent
    rows (default hides them as noise).
    """
    _render_summary(console, result)
    _render_recommendations(console, result, top_n=top_n)
    _render_token_metrics(console, result)
    _render_agent_metrics(console, result, verbose=verbose)
    _render_regression_footer(console, result)


def _render_summary(console: Console, result: DiffResult) -> None:
    pieces = [
        f"[bold]New:[/bold] {result.new_count}",
        f"[bold]Resolved:[/bold] {result.resolved_count}",
        f"[bold]Persisting:[/bold] {result.persisting_count}",
    ]
    if result.baseline_session_count or result.current_session_count:
        pieces.append(
            f"[dim]Sessions: {result.baseline_session_count} "
            f"→ {result.current_session_count}[/dim]",
        )
    console.print("  ".join(pieces))
    console.print()


def _render_recommendations(
    console: Console,
    result: DiffResult,
    *,
    top_n: int,
) -> None:
    new = [r for r in result.recommendations if r.status == "new"]
    resolved = [r for r in result.recommendations if r.status == "resolved"]
    persisting = [r for r in result.recommendations if r.status == "persisting"]

    if new:
        _print_rec_table(
            console,
            "New Recommendations",
            new,
            top_n=top_n,
            show_delta=False,
        )
    if resolved:
        _print_rec_table(
            console,
            "Resolved Recommendations",
            resolved,
            top_n=top_n,
            show_delta=False,
        )
    if persisting:
        _print_rec_table(
            console,
            "Persisting Recommendations",
            persisting,
            top_n=top_n,
            show_delta=True,
        )

    if not result.recommendations:
        console.print("[dim]No recommendation changes.[/dim]")
        console.print()


def _print_rec_table(
    console: Console,
    title: str,
    rows: list[RecommendationDelta],
    *,
    top_n: int,
    show_delta: bool,
) -> None:
    table = Table(title=title, show_lines=False)
    table.add_column("Severity", style="bold")
    table.add_column("Agent", style="cyan")
    table.add_column("Target")
    if show_delta:
        table.add_column("Count Δ", justify="right")
        table.add_column("Priority Δ", justify="right")
    else:
        table.add_column("Count", justify="right")
        table.add_column("Priority", justify="right")
    table.add_column("Message")

    truncated = rows if top_n <= 0 else rows[:top_n]

    for row in truncated:
        agent = row.agent_type or GLOBAL_AGENT_LABEL
        if show_delta:
            count_cell = _signed_int(row.count_delta)
            priority_cell = _signed_float(row.priority_score_delta, precision=1)
        else:
            base_count = (
                row.current_count if row.status == "new" else row.baseline_count
            )
            base_priority = (
                row.current_priority_score
                if row.status == "new"
                else row.baseline_priority_score
            )
            count_cell = str(base_count)
            priority_cell = f"{base_priority:.1f}"

        prefix = _axis_prefix(row)
        message_body = truncate(escape(row.representative_message), 80)
        message_cell = (
            f"{prefix} {message_body}" if prefix else message_body
        )
        table.add_row(
            severity_cell(row.severity),
            agent,
            row.target,
            count_cell,
            priority_cell,
            message_cell,
        )

    console.print(table)
    if top_n > 0 and len(rows) > top_n:
        console.print(
            f"[dim]… {len(rows) - top_n} more rows; use --top-n 0 to see all.[/dim]",
        )
    console.print()


def _render_token_metrics(console: Console, result: DiffResult) -> None:
    tm = result.token_metrics
    table = Table(title="Token Metrics")
    table.add_column("Metric")
    table.add_column("Baseline", justify="right")
    table.add_column("Current", justify="right")
    table.add_column("Delta", justify="right")

    table.add_row(
        "Total cost",
        format_cost(tm.baseline_total_cost),
        format_cost(tm.current_total_cost),
        _signed_cost(tm.total_cost_delta),
    )
    table.add_row(
        "Total tokens",
        format_tokens(tm.baseline_total_tokens),
        format_tokens(tm.current_total_tokens),
        _signed_int(tm.total_tokens_delta),
    )
    table.add_row(
        "Cache efficiency",
        f"{tm.baseline_cache_efficiency:.1f}%",
        f"{tm.current_cache_efficiency:.1f}%",
        _signed_float(tm.cache_efficiency_delta, precision=1, suffix="%"),
    )
    console.print(table)
    console.print()

    if tm.by_model:
        _render_by_model(console, tm.by_model)


def _render_by_model(console: Console, rows: list[ModelTokenDelta]) -> None:
    table = Table(title="Token Metrics by Model")
    table.add_column("Model", style="cyan")
    table.add_column("Baseline cost", justify="right")
    table.add_column("Current cost", justify="right")
    table.add_column("Cost Δ", justify="right")
    table.add_column("Tokens Δ", justify="right")

    for row in rows:
        table.add_row(
            row.model,
            format_cost(row.baseline_cost),
            format_cost(row.current_cost),
            _signed_cost(row.cost_delta),
            _signed_int(row.total_tokens_delta),
        )
    console.print(table)
    console.print()


def _render_agent_metrics(
    console: Console,
    result: DiffResult,
    *,
    verbose: bool,
) -> None:
    rows = [
        d for d in result.by_agent_type
        if verbose
        or d.invocation_count_delta != 0
        or d.total_tokens_delta != 0
        or d.estimated_cost_delta_usd != 0
    ]
    if not rows:
        return

    table = Table(title="Per-Agent Deltas")
    table.add_column("Agent", style="cyan")
    table.add_column("Invocations Δ", justify="right")
    table.add_column("Tokens Δ", justify="right")
    table.add_column("Cost Δ", justify="right")

    rows.sort(
        key=lambda d: (-abs(d.estimated_cost_delta_usd), d.agent_type),
    )

    for row in rows:
        table.add_row(
            row.agent_type,
            _signed_int(row.invocation_count_delta),
            _signed_int(row.total_tokens_delta),
            _signed_cost(row.estimated_cost_delta_usd),
        )
    console.print(table)
    console.print()


def _render_regression_footer(console: Console, result: DiffResult) -> None:
    if result.fail_on is None:
        return
    if result.regression_detected:
        console.print(
            f"[red]Regression detected:[/red] new findings at or above "
            f"severity '{result.fail_on.value}' (--fail-on={result.fail_on.value}).",
        )
    else:
        console.print(
            f"[green]No regressions[/green] at or above severity "
            f"'{result.fail_on.value}'.",
        )


# ---------------------------------------------------------------------------
# Cell formatters
# ---------------------------------------------------------------------------


def _axis_prefix(row: RecommendationDelta) -> str:
    """Render the axis-attribution prefix for a delta's message cell.

    Three shapes:

    - ``status='new'``: ``[<current_axis>]`` using the current axis color.
    - ``status='resolved'``: ``[<baseline_axis>]`` using the baseline axis
      color (the side that has the rec).
    - ``status='persisting'``: ``[<axis>]`` when both sides agree, or
      ``[<baseline> → <current>]`` when ``axis_shifted``. Both axes
      retain their respective colors so the shift is visually scannable.

    Returns an empty string when neither side carries a ``primary_axis``
    (e.g., diffing two pre-v0.6 envelopes — no info to show).
    """
    if row.axis_shifted:
        # ``axis_shifted`` is only True when both sides are non-None.
        # The leading ``[`` is escaped via ``\[`` so Rich emits it as
        # plain text instead of consuming ``[<axis-name>`` as an unknown
        # style tag. The trailing ``]`` is fine as-is.
        baseline = row.baseline_primary_axis or ""
        current = row.current_primary_axis or ""
        baseline_color = AXIS_COLORS.get(baseline, "white")
        current_color = AXIS_COLORS.get(current, "white")
        return (
            f"\\[[{baseline_color}]{baseline}[/{baseline_color}] → "
            f"[{current_color}]{current}[/{current_color}]]"
        )
    axis = row.current_primary_axis or row.baseline_primary_axis
    if axis is None:
        return ""
    return axis_label(axis)


def _signed(value: float, formatted_abs: str) -> str:
    """Wrap a pre-formatted magnitude in red (+) / green (-) markup.

    Positive deltas are red because in the diff context they represent
    growth in undesirable metrics (cost, regressions); zero is uncolored.
    """
    if value > 0:
        return f"[red]+{formatted_abs}[/red]"
    if value < 0:
        return f"[green]-{formatted_abs}[/green]"
    return formatted_abs


def _signed_int(value: int) -> str:
    return _signed(value, f"{abs(value):,}")


def _signed_cost(value: float) -> str:
    return _signed(value, format_cost(abs(value)))


def _signed_float(value: float, *, precision: int, suffix: str = "") -> str:
    return _signed(value, f"{abs(value):.{precision}f}{suffix}")
