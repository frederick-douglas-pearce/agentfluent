"""Discovery of subagent trace files under a Claude Code project tree.

Subagent traces live at
``<project_path>/<session-uuid>/subagents/agent-<agentId>.jsonl``. This
module enumerates those files and extracts ``agentId`` from the
filename. Parsing of trace contents is deferred to the trace parser
module; this layer only walks directories and names files.

The ``session_id`` keys returned by ``discover_subagent_files`` match the
directory name, which equals the session JSONL filename's stem — the same
key used by ``core.discovery.SessionInfo``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from agentfluent.core.paths import SUBAGENTS_SUBDIR

AGENT_FILENAME_PATTERN = re.compile(r"^agent-(.+)\.jsonl$")


@dataclass
class SubagentFileInfo:
    """Metadata for one subagent trace JSONL file."""

    path: Path
    agent_id: str


def discover_session_subagents(session_dir: Path) -> list[SubagentFileInfo]:
    """Return subagent file info for one session directory.

    ``session_dir`` is the directory counterpart to a session JSONL file
    (e.g. ``<project>/<session-uuid>/``). Returns an empty list when the
    ``subagents/`` subdirectory is missing or contains no files matching
    the ``agent-<agentId>.jsonl`` pattern.
    """
    subagents_dir = session_dir / SUBAGENTS_SUBDIR
    if not subagents_dir.is_dir():
        return []

    files: list[SubagentFileInfo] = []
    for entry in sorted(subagents_dir.iterdir()):
        if not entry.is_file():
            continue
        match = AGENT_FILENAME_PATTERN.match(entry.name)
        if match is None:
            continue
        files.append(SubagentFileInfo(path=entry, agent_id=match.group(1)))
    return files


def discover_subagent_files(
    project_path: Path,
) -> dict[str, list[SubagentFileInfo]]:
    """Return a ``session_id -> subagent files`` mapping for a project.

    Walks each direct subdirectory of ``project_path`` and collects its
    subagent files via ``discover_session_subagents``. Sessions with no
    subagent directory (or an empty one) are omitted from the mapping.
    """
    if not project_path.is_dir():
        return {}

    results: dict[str, list[SubagentFileInfo]] = {}
    for entry in sorted(project_path.iterdir()):
        if not entry.is_dir():
            continue
        subagent_files = discover_session_subagents(entry)
        if subagent_files:
            results[entry.name] = subagent_files
    return results
