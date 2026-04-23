"""Path resolution for Claude config directories.

Single source of truth for the Claude config root and its named
subdirectories. Callers compose paths from the helpers below rather
than repeating the `"projects"` / `"agents"` subdirectory names inline,
so an override replaces the root once and every subdirectory follows.
"""

from __future__ import annotations

from pathlib import Path

DEFAULT_CLAUDE_CONFIG_DIR = Path.home() / ".claude"

CLAUDE_CONFIG_DIR_ENV_VAR = "CLAUDE_CONFIG_DIR"

PROJECTS_SUBDIR = "projects"
AGENTS_SUBDIR = "agents"
SUBAGENTS_SUBDIR = "subagents"
"""The per-session directory that holds subagent trace JSONL files:
``<project>/<session-uuid>/subagents/agent-<agentId>.jsonl``."""


def projects_dir_for(config_root: Path | None) -> Path | None:
    """Derive the projects subdirectory from a config root override.

    Returns ``None`` when no override applies, so callers can pass the
    result directly to ``discover_projects(base_path=...)`` which falls
    back to ``DEFAULT_PROJECTS_DIR``.
    """
    return (config_root / PROJECTS_SUBDIR) if config_root else None


def agents_dir_for(config_root: Path | None) -> Path | None:
    """Derive the user agents subdirectory from a config root override.

    Returns ``None`` when no override applies, so callers can pass the
    result directly to ``scan_agents(user_path=...)`` which falls back
    to ``DEFAULT_USER_AGENTS_DIR``.
    """
    return (config_root / AGENTS_SUBDIR) if config_root else None


def claude_json_for(config_root: Path | None) -> Path:
    """Path to the user's ``.claude.json`` file.

    Claude Code stores the primary user config (including top-level
    ``mcpServers`` and the per-project ``projects[<path>]`` section)
    in ``$HOME/.claude.json`` — a sibling of the ``.claude/``
    directory, not a child of it.

    When ``config_root`` is given (e.g., from ``--claude-config-dir``
    pointing at ``/custom/.claude/``), the companion ``.claude.json``
    is resolved at the override's parent (``/custom/.claude.json``).
    This matches the pattern of overriding the whole Claude Code
    config hierarchy — callers who want to test against an alternate
    dataset expect both ``.claude/`` and ``.claude.json`` to move
    together.
    """
    if config_root is None:
        return Path.home() / ".claude.json"
    return config_root.parent / ".claude.json"


def validate_claude_config_dir(override: Path | None) -> Path | None:
    """Validate an override path for the Claude config directory.

    The caller (CLI or programmatic) is responsible for providing the
    override value — typically resolved from a `--claude-config-dir` flag
    or `$CLAUDE_CONFIG_DIR` env var via Typer's envvar binding.

    Returns the resolved absolute path when `override` is provided and
    valid, or `None` when no override applies (caller uses the default).

    Raises:
        FileNotFoundError: Override path does not exist.
        NotADirectoryError: Override path exists but is not a directory.
    """
    if override is None:
        return None

    if not override.exists():
        msg = f"Claude config directory not found: {override}"
        raise FileNotFoundError(msg)

    if not override.is_dir():
        msg = f"Claude config directory path is not a directory: {override}"
        raise NotADirectoryError(msg)

    return override.resolve()
