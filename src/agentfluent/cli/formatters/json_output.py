"""JSON output envelope for CLI commands.

All commands emit JSON in a stable envelope:

    {
      "version": "1",
      "command": "<list|analyze|config-check>",
      "data": { ... command-specific payload ... }
    }

`version` is a string and bumps independently of the package version when the
schema changes. Consumers should check `command` before parsing `data`.
"""

from __future__ import annotations

import json
from typing import Any, Literal

SCHEMA_VERSION = "1"

CommandName = Literal[
    "list-projects", "list-sessions", "analyze", "config-check", "diff",
]


def format_json_output(command: CommandName, data: Any) -> str:
    """Wrap a command payload in the versioned JSON envelope."""
    envelope = {
        "version": SCHEMA_VERSION,
        "command": command,
        "data": data,
    }
    return json.dumps(envelope, indent=2, default=str)


def parse_json_output(
    text: str, *, expected_command: CommandName | None = None,
) -> Any:
    """Validate the envelope and return its `data` payload.

    Raises ValueError on schema violation (missing keys, wrong version,
    wrong command).
    """
    envelope = json.loads(text)
    missing = {"version", "command", "data"} - envelope.keys()
    if missing:
        msg = f"JSON envelope missing keys: {sorted(missing)}"
        raise ValueError(msg)
    if envelope["version"] != SCHEMA_VERSION:
        msg = (
            f"JSON envelope version {envelope['version']!r} does not match "
            f"SCHEMA_VERSION {SCHEMA_VERSION!r}"
        )
        raise ValueError(msg)
    if expected_command is not None and envelope["command"] != expected_command:
        msg = (
            f"JSON envelope command {envelope['command']!r} does not match "
            f"expected {expected_command!r}"
        )
        raise ValueError(msg)
    return envelope["data"]
