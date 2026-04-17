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

CommandName = Literal["list-projects", "list-sessions", "analyze", "config-check"]


def format_json_output(command: CommandName, data: Any) -> str:
    """Wrap a command payload in the versioned JSON envelope."""
    envelope = {
        "version": SCHEMA_VERSION,
        "command": command,
        "data": data,
    }
    return json.dumps(envelope, indent=2, default=str)
