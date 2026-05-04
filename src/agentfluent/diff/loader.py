"""Load and validate `analyze --json` envelopes from disk.

Wraps :func:`agentfluent.cli.formatters.json_output.parse_json_output`
with a typed exception that the CLI maps to a user-friendly error.
``--quiet`` envelopes are rejected because they omit the diagnostics +
per-model breakdowns the diff needs.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agentfluent.cli.formatters.json_output import parse_json_output


class EnvelopeLoadError(ValueError):
    """Raised when a baseline/current file isn't a valid analyze envelope.

    The CLI catches this and surfaces ``str(error)`` to the user. Causes:
    file missing, malformed JSON, schema-version mismatch, wrong
    ``command``, or a ``--quiet`` envelope (lacks the diff inputs).
    """


_REQUIRED_KEYS = ("token_metrics", "agent_metrics")


def load_envelope(path: Path) -> dict[str, Any]:
    """Load ``path``, validate the envelope, return the ``data`` payload.

    Returns the raw dict (not a Pydantic model) — :func:`compute_diff`
    operates on dicts so it doesn't need to round-trip through the full
    ``AnalysisResult`` model and stay coupled to optional fields.
    """
    if not path.exists():
        msg = f"File not found: {path}"
        raise EnvelopeLoadError(msg)

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        msg = f"Could not read {path}: {e}"
        raise EnvelopeLoadError(msg) from e

    try:
        data = parse_json_output(text, expected_command="analyze")
    except json.JSONDecodeError as e:
        msg = f"Invalid JSON in {path}: {e.msg}"
        raise EnvelopeLoadError(msg) from e
    except ValueError as e:
        msg = f"{path}: {e}"
        raise EnvelopeLoadError(msg) from e

    if not isinstance(data, dict):
        msg = f"{path}: envelope 'data' is not an object"
        raise EnvelopeLoadError(msg)

    missing = [k for k in _REQUIRED_KEYS if k not in data]
    if missing:
        keys = ", ".join(missing)
        msg = (
            f"{path}: envelope is missing required keys [{keys}]. "
            "The diff command needs full `analyze --json` output — re-run "
            "without --quiet."
        )
        raise EnvelopeLoadError(msg)

    return data
