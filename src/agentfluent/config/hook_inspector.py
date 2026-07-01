"""Inspect agent hook scripts for references to specific hook-input fields.

The config scanner (``scanner.py``) inventories hook script *paths* from agent
frontmatter but never inspects their *content*. This module adds that capability:
given an ``AgentConfig``, a hook event, and a field name, it reports whether any
hook script wired to that event references the field.

The first consumer (story #425) checks ``PostToolUse`` for ``duration_ms`` so the
``DURATION_OUTLIER`` diagnostic can tell whether the agent already has a timing
hook. The interface generalizes to other fields via ``KNOWN_HOOK_FIELDS`` without
code changes.

Design notes:

- **Substring search, not AST.** Hook scripts are polyglot (Python, bash, jq).
  The question is "does this script reference ``duration_ms``?", not "does it use
  the value correctly." A literal-field-name substring search answers that
  reliably; false positives (a comment) are acceptable, false negatives are the
  real risk and near-zero. Transitive imports
  (``python3 -c "import m; m.run()"``) are an accepted false-negative limitation.
- **Agent-frontmatter hooks only.** Project-level hooks in
  ``.claude/settings.json`` are not inspected; recommendation copy (story #425)
  surfaces this caveat.
- **Single-pair result.** ``inspect_hook_field`` answers one ``(event, field)``
  question and returns one ``HookFieldCoverage`` -- exactly what the correlator
  needs. Callers that want a list (story #426) invoke this per pair.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from .models import AgentConfig, HookFieldCoverage

logger = logging.getLogger(__name__)

KNOWN_HOOK_FIELDS: dict[str, set[str]] = {
    "PostToolUse": {"duration_ms", "tool_input", "tool_response", "tool_name"},
    "Stop": {"background_tasks", "session_crons"},
}
"""Authoritative registry of hook-input fields each hook event supports.

Seeded with documented fields. ``Stop`` fields come from C-002 and are included
for forward-compatibility; no diagnostic chain exercises them yet. Future fields
add *data* here, not code.
"""

_SCRIPT_RE = re.compile(r"""[^\s"']+\.(?:py|sh|js)\b""")
"""Matches a script path token inside a hook command, tolerant of surrounding
quotes (real commands look like ``python3 "$CLAUDE_PROJECT_DIR/.../x.py"``)."""

_INLINE = "(inline)"


def _references_field(script_content: str, field: str) -> bool:
    """Return True if ``field`` appears literally in ``script_content``."""
    return field in script_content


def _resolve_script_path(command: str, root: Path) -> Path | None:
    """Extract a script path from a hook ``command`` and resolve it under ``root``.

    Handles quoted paths and ``$CLAUDE_PROJECT_DIR`` / ``${CLAUDE_PROJECT_DIR}``
    expansion. Returns ``None`` for pure inline commands (no script-file token).
    """
    match = _SCRIPT_RE.search(command)
    if match is None:
        return None
    candidate = match.group(0)
    candidate = candidate.replace("${CLAUDE_PROJECT_DIR}", str(root)).replace(
        "$CLAUDE_PROJECT_DIR", str(root)
    )
    path = Path(candidate)
    return path if path.is_absolute() else root / path


def _iter_commands(groups: object) -> list[str]:
    """Yield every ``command`` string from a hook event's matcher groups.

    Claude Code's hook schema nests commands two levels deep:
    ``event -> [{matcher, hooks: [{type, command}]}]``.
    """
    commands: list[str] = []
    if not isinstance(groups, list):
        return commands
    for group in groups:
        if not isinstance(group, dict):
            continue
        for hook in group.get("hooks", []):
            if isinstance(hook, dict):
                command = hook.get("command")
                if isinstance(command, str):
                    commands.append(command)
    return commands


def inspect_hook_field(
    config: AgentConfig,
    hook_event: str,
    field_name: str,
    project_root: Path | None = None,
) -> HookFieldCoverage:
    """Report whether ``config``'s hooks for ``hook_event`` reference ``field_name``.

    Inspects every hook command wired to ``hook_event``. External script files
    (``.py``/``.sh``/``.js``) are read and searched; inline commands are searched
    directly. Returns coverage for the first hook that references the field, else
    a not-covered result. A missing external script file is logged and treated as
    not covering (no crash). Does not modify ``config``.
    """
    root = project_root or Path.cwd()
    for command in _iter_commands(config.hooks.get(hook_event)):
        script_path = _resolve_script_path(command, root)
        if script_path is None:
            if _references_field(command, field_name):
                return HookFieldCoverage(
                    hook_event=hook_event,
                    field_name=field_name,
                    covered=True,
                    source=_INLINE,
                )
            continue
        try:
            content = script_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            logger.debug("Hook script not readable: %s", script_path)
            continue
        if _references_field(content, field_name):
            return HookFieldCoverage(
                hook_event=hook_event,
                field_name=field_name,
                covered=True,
                source=str(script_path),
            )
    return HookFieldCoverage(
        hook_event=hook_event,
        field_name=field_name,
        covered=False,
        source="",
    )
