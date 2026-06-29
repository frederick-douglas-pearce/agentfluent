#!/usr/bin/env python3
"""PreToolUse hook: protect append-only spec logs from entry-dropping writes.

Receives the PreToolUse event JSON on stdin. For a `Write` targeting a
registered append-only file (currently `.claude/specs/decisions.md`), it
compares the set of decision-entry IDs (`## Dxxx` headings) already on disk
against the set in the proposed `content`. If the write would drop any
existing entry, the call is denied.

Background: an append-only decision log was once clobbered by an agent whose
`Write` tool does full-file replacement -- a pm intending to append one
`Dxxx` entry replaced the entire file, dropping D001-D042 in the working copy
(GitHub issue #500). This hook is the durable, agent-agnostic guard.

Scope and bypass surface (deliberately bounded):
- Guards the `Write` tool only. `Edit` is surgical (it cannot easily drop the
  whole file) and simulating its result to detect drops is fragile, so it is
  not guarded.
- It does NOT cover `Bash` redirection (`cat > file`, `tee`, `sed -i`), which
  an agent with Bash could use to clobber the file. This hook therefore
  protects *entry existence against full-file Writes*, not body content, and
  is not an absolute "any tool" guarantee. A Bash-command scan is the natural
  follow-up extension if that guarantee is needed.

Emits a JSON decision on stdout and exits 0 (the modern pattern); exit 2 +
stderr is the fail-closed fallback for an unparseable event, matching
`block_secret_reads.py`.

Cross-platform: stdlib only, no shell dependencies.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path, PurePath
from typing import Any

# Registry of append-only files to protect, keyed by path SUFFIX (not bare
# basename, to avoid false positives on unrelated `decisions.md` files
# elsewhere in the tree). Each value is the anchored, multiline regex that
# captures the entry IDs whose existence must be preserved across a write.
# To protect another append-only spec, add its suffix + ID pattern here.
DECISION_ID_PATTERN = re.compile(r"^##\s+(D\d+)", re.MULTILINE)

APPEND_ONLY_FILES: dict[str, re.Pattern[str]] = {
    ".claude/specs/decisions.md": DECISION_ID_PATTERN,
}

DENY_REASON = (
    "Blocked by AgentFluent append-only guard hook "
    "(.claude/hooks/guard_append_only.py). "
    "This Write would drop one or more existing entries from an append-only "
    "log, which is a data-loss risk (see issue #500). "
    "The guard protects entry existence (`## Dxxx` headings), not body text -- "
    "editing the body of an existing entry is fine as long as no entry is "
    "removed. To add an entry, append to the file (e.g. read the current "
    "content and write it back with the new entry appended, or use Edit), "
    "preserving every existing `## Dxxx` heading."
)


def normalize(path_str: str) -> str:
    """Return a forward-slash path string for suffix matching."""
    return PurePath(path_str).as_posix()


def match_registered_file(path_str: str) -> re.Pattern[str] | None:
    """Return the ID pattern for a registered append-only file, else None."""
    if not path_str:
        return None
    normalized = normalize(path_str)
    for suffix, pattern in APPEND_ONLY_FILES.items():
        if normalized.endswith(suffix):
            return pattern
    return None


def extract_ids(text: str, pattern: re.Pattern[str]) -> set[str]:
    """Extract the set of entry IDs from text using the registered pattern."""
    return set(pattern.findall(text))


def evaluate(
    existing_content: str, proposed_content: str, pattern: re.Pattern[str]
) -> tuple[bool, str]:
    """Decide whether a write should be blocked. Pure (no I/O).

    Returns (blocked, reason). Blocks when any ID present in the existing
    content is absent from the proposed content.
    """
    existing_ids = extract_ids(existing_content, pattern)
    if not existing_ids:
        # Nothing to protect yet (file exists but has no recognized entries).
        return False, ""
    proposed_ids = extract_ids(proposed_content, pattern)
    missing = sorted(existing_ids - proposed_ids)
    if missing:
        return True, f"{DENY_REASON} (would drop: {', '.join(missing)})"
    return False, ""


def check(event: dict[str, Any]) -> tuple[bool, str]:
    """Inspect a PreToolUse event; return (blocked, reason)."""
    if event.get("tool_name", "") != "Write":
        return False, ""

    tool_input = event.get("tool_input") or {}
    path = tool_input.get("file_path") or ""
    pattern = match_registered_file(path)
    if pattern is None:
        return False, ""

    proposed_content = tool_input.get("content") or ""

    try:
        existing_content = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        # New file: there is nothing to drop.
        return False, ""
    except (OSError, UnicodeDecodeError) as e:
        # A real read error on an existing protected file means we cannot
        # verify the write is safe. Fail closed -- deny rather than risk a
        # silent clobber. (Such errors on a local file are vanishingly rare.)
        return True, (
            f"{DENY_REASON} (could not read the existing file to verify no "
            f"entries are dropped: {e})"
        )

    return evaluate(existing_content, proposed_content, pattern)


def emit_decision(decision: str, reason: str) -> None:
    payload = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": reason,
        }
    }
    sys.stdout.write(json.dumps(payload))


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError) as e:
        # Fail closed: if we can't parse the event we can't confirm the write
        # is safe, so deny rather than allow through a malformed event.
        print(
            f"guard_append_only: failed to parse hook event JSON, "
            f"denying by default: {e}",
            file=sys.stderr,
        )
        return 2

    blocked, reason = check(event)
    if blocked:
        emit_decision("deny", reason)
    return 0


if __name__ == "__main__":
    sys.exit(main())
