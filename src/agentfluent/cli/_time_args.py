"""Shared CLI helpers for ``--since`` / ``--until`` argument parsing.

Centralizes datetime parsing and inverted-interval validation so every
command exposing time filters surfaces the same error messages and exit
code. The downstream filter (``core.filtering.filter_sessions_by_time``)
already handles half-open semantics and timezone normalization once the
strings are parsed; this module covers the CLI-layer concerns above it.
"""

from __future__ import annotations

from datetime import datetime

import typer
from rich.console import Console

from agentfluent.cli.exit_codes import EXIT_USER_ERROR
from agentfluent.core.timeutil import parse_datetime


def parse_time_window(
    since: str | None,
    until: str | None,
    *,
    err_console: Console,
) -> tuple[datetime | None, datetime | None]:
    """Parse ``--since`` and ``--until`` strings into UTC-aware datetimes.

    Returns ``(parsed_since, parsed_until)`` with ``None`` for omitted
    bounds. Raises ``typer.Exit(EXIT_USER_ERROR)`` and prints a
    user-friendly message for:

    - Unparseable input (re-surfaces ``parse_datetime``'s ``ValueError``)
    - Inverted or empty intervals: when both bounds are supplied and
      ``parsed_since >= parsed_until``. The half-open ``[since, until)``
      semantics make ``since == until`` produce an empty result, which
      is almost always a foot-gun rather than intent — so we reject
      both ``>`` and ``==``.
    """
    parsed_since = _parse_or_exit(since, "--since", err_console)
    parsed_until = _parse_or_exit(until, "--until", err_console)
    if (
        parsed_since is not None
        and parsed_until is not None
        and parsed_since >= parsed_until
    ):
        err_console.print(
            f"[red]Error:[/red] --since ({since}) is at or after "
            f"--until ({until}); did you swap them?",
        )
        raise typer.Exit(code=EXIT_USER_ERROR)
    return parsed_since, parsed_until


def _parse_or_exit(
    value: str | None, flag: str, err_console: Console,
) -> datetime | None:
    if value is None:
        return None
    try:
        return parse_datetime(value)
    except ValueError as exc:
        err_console.print(f"[red]Error:[/red] {flag} {value!r}: {exc}")
        raise typer.Exit(code=EXIT_USER_ERROR) from None
