"""agentfluent diff -- compare two `analyze --json` envelopes.

User manages baselines explicitly (no internal caching). Exits with
:data:`agentfluent.cli.exit_codes.EXIT_REGRESSION` (3) when a new
recommendation at or above ``--fail-on`` severity appears in the
current run; useful as a CI gate.
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

from agentfluent.cli.exit_codes import (
    EXIT_OK,
    EXIT_REGRESSION,
    EXIT_USER_ERROR,
)
from agentfluent.cli.formatters.diff_table import format_diff_table
from agentfluent.cli.formatters.json_output import format_json_output
from agentfluent.config.models import Severity
from agentfluent.diff import (
    EnvelopeLoadError,
    compute_diff,
    load_envelope,
)
from agentfluent.diff.models import DiffResult

DIFF_EPILOG = """\
Examples:

  agentfluent diff baseline.json current.json
      Compare two analyze runs (default: warn-level fail threshold).

  agentfluent diff baseline.json current.json --fail-on critical
      CI gate: exit 3 only when new critical findings appear.

  agentfluent diff baseline.json current.json --json | jq '.data.regression_detected'
      Programmatic consumption.

Exit codes:
  0  No regression (or --fail-on disabled).
  1  User error (file missing, malformed JSON, schema mismatch).
  3  Regression detected at or above --fail-on threshold.
"""

console = Console()
err_console = Console(stderr=True)


def _resolve_fail_on(value: str | None) -> Severity | None:
    """Map ``--fail-on`` string to a Severity (or None for "off")."""
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized in {"off", "none", ""}:
        return None
    try:
        return Severity(normalized)
    except ValueError as e:
        valid = ", ".join(s.value for s in Severity)
        msg = f"Invalid --fail-on value: {value!r}. Choose one of: {valid}, off."
        raise typer.BadParameter(msg) from e


def _print_json(result: DiffResult, *, quiet: bool) -> None:
    if quiet:
        payload: dict[str, object] = {
            "new_count": result.new_count,
            "resolved_count": result.resolved_count,
            "persisting_count": result.persisting_count,
            "total_cost_delta": result.token_metrics.total_cost_delta,
            "total_tokens_delta": result.token_metrics.total_tokens_delta,
            "regression_detected": result.regression_detected,
            "fail_on": result.fail_on.value if result.fail_on else None,
        }
    else:
        payload = result.model_dump(mode="json")
    print(format_json_output("diff", payload))


def _print_quiet(result: DiffResult) -> None:
    cost_delta = result.token_metrics.total_cost_delta
    sign = "+" if cost_delta >= 0 else "-"
    console.print(
        f"Diff: {result.new_count} new, {result.resolved_count} resolved, "
        f"{result.persisting_count} persisting | "
        f"cost {sign}${abs(cost_delta):.4f}"
        + (" | REGRESSION" if result.regression_detected else ""),
    )


def diff(
    baseline: Path = typer.Argument(
        ...,
        help="Baseline `analyze --json` output file.",
        exists=False,  # we surface a richer error than typer's default
        dir_okay=False,
    ),
    current: Path = typer.Argument(
        ...,
        help="Current `analyze --json` output file.",
        exists=False,
        dir_okay=False,
    ),
    fail_on: str = typer.Option(
        "warning",
        "--fail-on",
        help=(
            "Exit with code 3 when a new recommendation at or above this "
            "severity appears. Choices: info, warning, critical, off. "
            "Default: warning."
        ),
    ),
    format: str = typer.Option(
        "table",
        "--format",
        "-f",
        help="Output format: table or json. Shortcut: --json.",
    ),
    json_flag: bool = typer.Option(
        False,
        "--json",
        help="Shortcut for --format json.",
    ),
    top_n: int = typer.Option(
        5,
        "--top-n",
        help=(
            "Truncate each recommendations table (new / resolved / persisting) "
            "to the top N by priority. Pass 0 to show all rows."
        ),
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Include zero-delta agent rows.",
    ),
    quiet: bool = typer.Option(
        False, "--quiet", "-q", help="One-line summary instead of full tables.",
    ),
) -> None:
    """Compare two analyze runs and report new / resolved / persisting findings."""
    if verbose and quiet:
        raise typer.BadParameter("--verbose and --quiet are mutually exclusive")

    if json_flag:
        format = "json"

    fail_on_severity = _resolve_fail_on(fail_on)

    try:
        baseline_data = load_envelope(baseline)
        current_data = load_envelope(current)
    except EnvelopeLoadError as e:
        err_console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(code=EXIT_USER_ERROR) from None

    result = compute_diff(baseline_data, current_data, fail_on=fail_on_severity)

    if format == "json":
        _print_json(result, quiet=quiet)
    elif quiet:
        _print_quiet(result)
    else:
        format_diff_table(console, result, top_n=top_n, verbose=verbose)

    exit_code = EXIT_REGRESSION if result.regression_detected else EXIT_OK
    if exit_code != EXIT_OK:
        raise typer.Exit(code=exit_code)
