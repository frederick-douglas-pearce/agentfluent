"""agentfluent report -- render an analyze --json snapshot as Markdown.

Per D031, ``report`` is a separate subcommand (composable) rather than
``analyze --format markdown``: ``analyze --project P --json > snap.json``
followed by ``report snap.json > report.md`` keeps the rendering layer
decoupled from session ingestion and lets snapshots round-trip through
file storage, PR comments, and CI artifact pipelines.

The command dispatches on the envelope's ``command`` field so future
report consumers (notably ``diff`` envelopes, deferred to v0.8 per
``prd-v0.7.md`` OQ3) can be added by registering one more renderer in
``_RENDERERS`` -- no module restructuring needed.

Section ordering for analyze reports follows D030:
Summary -> Token Metrics -> Agent Metrics -> Diagnostics -> Offload ->
Footer. Renderers in this story emit section headers only; #354
implements the section bodies.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import typer
from rich.console import Console

from agentfluent.cli.commands.report_renderers import (
    render_agent_metrics,
    render_diagnostics,
    render_footer,
    render_offload,
    render_summary,
    render_token_metrics,
)
from agentfluent.cli.exit_codes import EXIT_USER_ERROR
from agentfluent.cli.formatters.json_output import parse_json_output

REPORT_EPILOG = """\
Examples:

  agentfluent analyze --project P --json > snap.json
  agentfluent report snap.json
      Render a Markdown report to stdout from an analyze snapshot.

  agentfluent report snap.json --output report.md
      Write the report to a file.

  agentfluent analyze --project P --json | agentfluent report /dev/stdin
      Pipe directly without an intermediate file.

Exit codes:
  0  Report rendered successfully.
  1  User error (file missing, malformed JSON, unsupported envelope).
"""

err_console = Console(stderr=True)


class EnvelopeError(ValueError):
    """User-surfaced report-input error: file/JSON/envelope problem.

    The CLI catches this and prints ``str(error)`` so the user sees a
    clean message instead of a traceback.
    """


def _load_envelope(path: Path) -> tuple[str, dict[str, Any]]:
    """Read ``path``, validate the envelope, return ``(command, data)``.

    Accepts any valid versioned envelope; the caller dispatches on
    ``command``. Reusing
    :func:`agentfluent.cli.formatters.json_output.parse_json_output`
    keeps the version/keys contract defined in exactly one place.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as e:
        msg = f"File not found: {path}"
        raise EnvelopeError(msg) from e
    except OSError as e:
        msg = f"Could not read {path}: {e}"
        raise EnvelopeError(msg) from e

    try:
        envelope = json.loads(text)
    except json.JSONDecodeError as e:
        msg = f"Invalid JSON in {path}: {e.msg}"
        raise EnvelopeError(msg) from e

    if not isinstance(envelope, dict):
        msg = f"{path}: top-level JSON value is not an object"
        raise EnvelopeError(msg)

    try:
        data = parse_json_output(text, expected_command=None)
    except ValueError as e:
        msg = f"{path}: {e}"
        raise EnvelopeError(msg) from e

    command = envelope.get("command")
    if not isinstance(command, str):
        msg = f"{path}: envelope 'command' is not a string"
        raise EnvelopeError(msg)
    if not isinstance(data, dict):
        msg = f"{path}: envelope 'data' is not an object"
        raise EnvelopeError(msg)
    return command, data


# Section bodies live in ``report_renderers``. Keep dispatch wiring here
# so adding a renderer for a new envelope command (e.g., ``diff`` in
# v0.8) stays a one-line change in ``_RENDERERS`` and doesn't pull
# rendering helpers into this module.

# Body renderers share the ``(data) -> str`` signature; ``render_footer``
# additionally accepts an injected ``now`` so snapshot tests get
# deterministic output. Keeping it out of this tuple lets the
# ``_render_analyze_report`` loop stay uniform.
ANALYZE_BODY_SECTIONS: tuple[Callable[[dict[str, Any]], str], ...] = (
    render_summary,
    render_token_metrics,
    render_agent_metrics,
    render_diagnostics,
    render_offload,
)


def _render_analyze_report(
    data: dict[str, Any],
    *,
    now: datetime | None = None,
) -> str:
    """Assemble an analyze report from the section renderers in D030 order.

    ``now`` is forwarded only to :func:`render_footer` so snapshot tests
    can pin the reproduction timestamp.
    """
    parts = ["# AgentFluent Report\n"]
    for renderer in ANALYZE_BODY_SECTIONS:
        section = renderer(data)
        if section:
            parts.append(section)
    footer = render_footer(data, now=now)
    if footer:
        parts.append(footer)
    return "\n".join(parts)


_RENDERERS: dict[str, Callable[[dict[str, Any]], str]] = {
    "analyze": _render_analyze_report,
}


def report(
    snapshot: Path = typer.Argument(
        ...,
        help="Path to an `analyze --json` snapshot file.",
        exists=False,
        dir_okay=False,
    ),
    output: Optional[Path] = typer.Option(  # noqa: UP007, UP045
        None,
        "--output",
        "-o",
        help="Write report to this file (default: stdout).",
    ),
) -> None:
    """Render an `analyze --json` snapshot as Markdown."""
    try:
        command, data = _load_envelope(snapshot)
    except EnvelopeError as e:
        err_console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(code=EXIT_USER_ERROR) from None

    renderer = _RENDERERS.get(command)
    if renderer is None:
        supported = ", ".join(sorted(_RENDERERS))
        err_console.print(
            f"[red]Error:[/red] {snapshot}: report does not support envelope "
            f"command {command!r}. Supported: {supported}.",
        )
        raise typer.Exit(code=EXIT_USER_ERROR)

    text = renderer(data)
    if not text.endswith("\n"):
        text += "\n"

    if output is None:
        sys.stdout.write(text)
        return

    try:
        output.write_text(text, encoding="utf-8")
    except OSError as e:
        err_console.print(f"[red]Error:[/red] Could not write {output}: {e}")
        raise typer.Exit(code=EXIT_USER_ERROR) from None
