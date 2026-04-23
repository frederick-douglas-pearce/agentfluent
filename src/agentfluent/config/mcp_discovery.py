"""Discover configured MCP servers from Claude Code config files.

Reads MCP server entries from three effective sources and dedups by
server name using the precedence ``project_local > project_shared >
user`` (matches Claude Code's ``local > project > user`` resolution
order). This module is the config-layer counterpart to
``config/scanner.py``: it discovers what the user *configured*, not
what the agent *did* — that lives in ``diagnostics/mcp_assessment.py``.

The three sources:

- **User** — top-level ``mcpServers`` key in ``~/.claude.json``.
- **Project-shared** — ``mcpServers`` in ``.mcp.json`` at the project
  root (committed to the repo; gated per-user by the project-local
  ``enabledMcpjsonServers`` / ``disabledMcpjsonServers`` lists).
- **Project-local** — per-project ``mcpServers`` inside
  ``~/.claude.json:projects[<project_dir>]``.

The ``settings.json`` files (``~/.claude/settings.json``,
``.claude/settings.json``) carry hooks and other settings but no
``mcpServers`` key — not read by this module.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from agentfluent.config.models import McpScope, McpServerConfig
from agentfluent.core.paths import claude_json_for

logger = logging.getLogger(__name__)


MCP_PROJECT_FILENAME = ".mcp.json"


def discover_mcp_servers(
    claude_config_dir: Path | None,
    project_dir: Path | None,
) -> list[McpServerConfig]:
    """Discover configured MCP servers across user, project-shared,
    and project-local scopes.

    Returns one ``McpServerConfig`` per unique ``server_name``; when
    the same name appears in multiple scopes, the highest-precedence
    entry wins (``project_local > project_shared > user``). The
    canonical record's ``source_file`` points at the winning source.

    When ``project_dir`` is ``None``, only user-scope is consulted —
    both project scopes require a project directory. This matches the
    "programmatic call without --project" case.

    ``claude_config_dir`` overrides the location of ``~/.claude.json``
    via ``claude_json_for``; ``.mcp.json`` is always at ``project_dir``.

    Missing files return an empty contribution (silent skip). Malformed
    JSON logs a warning and skips that file.
    """
    claude_json_path = claude_json_for(claude_config_dir)
    user_servers = _read_user_mcp_servers(claude_json_path)

    project_local_servers: list[McpServerConfig] = []
    enabled_whitelist: list[str] | None = None
    disabled_list: list[str] = []
    project_shared_servers: list[McpServerConfig] = []

    if project_dir is not None:
        (
            project_local_servers,
            enabled_whitelist,
            disabled_list,
        ) = _read_project_local_mcp_servers(claude_json_path, project_dir)
        project_shared_servers = _read_project_shared_mcp_servers(
            project_dir,
            enabled_whitelist=enabled_whitelist,
            disabled_list=disabled_list,
        )

    return _dedup_by_name_with_precedence(
        user_servers, project_shared_servers, project_local_servers,
    )


def _load_json(path: Path) -> dict[str, Any] | None:
    """Read and decode a JSON object file.

    Returns ``None`` for missing files (silent), logs a warning and
    returns ``None`` for malformed JSON or non-object roots.
    """
    if not path.exists():
        return None
    try:
        with path.open() as f:
            data = json.load(f)
    except json.JSONDecodeError:
        logger.warning("Malformed JSON in MCP config file: %s", path)
        return None
    except OSError:
        logger.warning("Could not read MCP config file: %s", path, exc_info=True)
        return None
    if not isinstance(data, dict):
        logger.warning("MCP config file root is not an object: %s", path)
        return None
    return data


def _parse_server_entries(
    raw_servers: Any,
    *,
    source_file: Path,
    scope: McpScope,
    disabled_override: bool = False,
) -> list[McpServerConfig]:
    """Coerce a ``mcpServers`` dict into a list of ``McpServerConfig``.

    Handles the common shape ``{ "<name>": { ... } }``. Per-server
    entries that aren't dicts are skipped with a warning. Missing
    ``disabled`` and ``tools`` fields are treated as defaults
    (``False`` and ``None``). ``disabled_override=True`` forces
    ``enabled=False`` regardless of the per-entry flag — used by the
    project_shared reader when a server is gated off by the
    project-local ``disabledMcpjsonServers`` or falls outside an
    ``enabledMcpjsonServers`` whitelist.
    """
    if not isinstance(raw_servers, dict):
        return []

    servers: list[McpServerConfig] = []
    for name, raw in raw_servers.items():
        if not isinstance(raw, dict):
            logger.warning(
                "Skipping non-object MCP server entry %r in %s", name, source_file,
            )
            continue
        per_entry_disabled = bool(raw.get("disabled", False))
        tools = raw.get("tools")
        configured_tools = (
            [str(t) for t in tools] if isinstance(tools, list) else None
        )
        servers.append(
            McpServerConfig(
                server_name=str(name),
                enabled=not (per_entry_disabled or disabled_override),
                configured_tools=configured_tools,
                source_file=source_file,
                scope=scope,
            ),
        )
    return servers


def _read_user_mcp_servers(claude_json_path: Path) -> list[McpServerConfig]:
    """Read top-level ``mcpServers`` from ``~/.claude.json``."""
    data = _load_json(claude_json_path)
    if data is None:
        return []
    return _parse_server_entries(
        data.get("mcpServers"),
        source_file=claude_json_path,
        scope="user",
    )


def _read_project_local_mcp_servers(
    claude_json_path: Path,
    project_dir: Path,
) -> tuple[list[McpServerConfig], list[str] | None, list[str]]:
    """Read per-project section from ``~/.claude.json``.

    Returns ``(servers, enabled_whitelist, disabled_list)`` where
    ``enabled_whitelist`` is ``None`` when the field is absent
    (meaning "all servers enabled") and a list of server names when
    present (whitelist semantics). ``disabled_list`` is a list of
    server names explicitly gated off; empty when the field is absent.

    The enabled / disabled lists drive project_shared gating — the
    caller passes them to ``_read_project_shared_mcp_servers``.
    """
    data = _load_json(claude_json_path)
    if data is None:
        return [], None, []

    projects = data.get("projects")
    if not isinstance(projects, dict):
        return [], None, []

    # Project path may be stored as an absolute string key; match via
    # string equality on the resolved absolute path.
    project_key = str(project_dir.resolve())
    entry = projects.get(project_key)
    if not isinstance(entry, dict):
        return [], None, []

    servers = _parse_server_entries(
        entry.get("mcpServers"),
        source_file=claude_json_path,
        scope="project_local",
    )
    enabled_raw = entry.get("enabledMcpjsonServers")
    enabled_whitelist = (
        [str(n) for n in enabled_raw] if isinstance(enabled_raw, list) else None
    )
    disabled_raw = entry.get("disabledMcpjsonServers")
    disabled_list = (
        [str(n) for n in disabled_raw] if isinstance(disabled_raw, list) else []
    )
    return servers, enabled_whitelist, disabled_list


def _read_project_shared_mcp_servers(
    project_dir: Path,
    *,
    enabled_whitelist: list[str] | None,
    disabled_list: list[str],
) -> list[McpServerConfig]:
    """Read ``.mcp.json`` at ``project_dir``, applying per-user gating.

    A project_shared server is emitted with ``enabled=False`` when
    either:

    - It appears in ``disabled_list``
      (``disabledMcpjsonServers`` from the project-local section), or
    - ``enabled_whitelist`` is a non-None list and the server name is
      not in it (whitelist semantics — when the list is present,
      unlisted servers are considered off for this user).

    Gated servers are kept (not filtered out) so the missing-server
    audit still sees they exist in config — the audit differentiates
    "server that's off" from "server that was never configured."
    """
    path = project_dir / MCP_PROJECT_FILENAME
    data = _load_json(path)
    if data is None:
        return []

    raw_servers = data.get("mcpServers")
    if not isinstance(raw_servers, dict):
        return []

    servers: list[McpServerConfig] = []
    for name, raw in raw_servers.items():
        if not isinstance(raw, dict):
            logger.warning("Skipping non-object MCP server entry %r in %s", name, path)
            continue
        gated_off = name in disabled_list or (
            enabled_whitelist is not None and name not in enabled_whitelist
        )
        # Reuse the single-entry builder by wrapping in a one-key dict.
        [entry] = _parse_server_entries(
            {name: raw},
            source_file=path,
            scope="project_shared",
            disabled_override=gated_off,
        )
        servers.append(entry)
    return servers


_PRECEDENCE_ORDER: tuple[McpScope, ...] = ("user", "project_shared", "project_local")
"""Lowest → highest precedence. Later scopes overwrite earlier ones."""


def _dedup_by_name_with_precedence(
    *scope_buckets: list[McpServerConfig],
) -> list[McpServerConfig]:
    """Merge multiple scope buckets into a precedence-ordered list.

    Callers pass buckets in increasing-priority order; a later bucket
    with the same ``server_name`` overwrites the earlier entry. Order
    within the returned list is insertion order of first-sighting —
    deterministic for stable snapshot testing.
    """
    by_name: dict[str, McpServerConfig] = {}
    for bucket in scope_buckets:
        for server in bucket:
            by_name[server.server_name] = server
    return list(by_name.values())
