"""Path resolution for Claude config directories.

Single source of truth for the Claude config root and its named
subdirectories. Callers compose paths from the helpers below rather
than repeating the `"projects"` / `"agents"` subdirectory names inline,
so an override replaces the root once and every subdirectory follows.
"""

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_CLAUDE_CONFIG_DIR = Path.home() / ".claude"

CLAUDE_CONFIG_DIR_ENV_VAR = "CLAUDE_CONFIG_DIR"

PROJECTS_SUBDIR = "projects"
AGENTS_SUBDIR = "agents"
SUBAGENTS_SUBDIR = "subagents"
"""The per-session directory that holds subagent trace JSONL files:
``<project>/<session-uuid>/subagents/agent-<agentId>.jsonl``."""

AGENTFLUENT_SUBDIR = "agentfluent"
XDG_CONFIG_HOME_ENV_VAR = "XDG_CONFIG_HOME"
XDG_CACHE_HOME_ENV_VAR = "XDG_CACHE_HOME"


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


def settings_path_for(config_root: Path | None) -> Path:
    """Path to the user's ``~/.claude/settings.json`` file.

    Claude Code stores user-scope settings (hooks, ``cleanupPeriodDays``,
    and other knobs) in ``settings.json`` *inside* the ``.claude/``
    directory — unlike ``.claude.json``, which is a sibling of it.

    When ``config_root`` is given (e.g., from ``--claude-config-dir``),
    the settings file resolves inside the override
    (``<config_root>/settings.json``); otherwise the default
    ``~/.claude/settings.json`` applies. This is the file the
    ``cleanupPeriodDays`` retention warning quotes as the place to add a
    longer-retention override.
    """
    root = config_root if config_root else DEFAULT_CLAUDE_CONFIG_DIR
    return root / "settings.json"


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


def agentfluent_config_dir() -> Path:
    """Canonical AgentFluent config root.

    Honors ``$XDG_CONFIG_HOME`` when set, else falls back to
    ``~/.config/agentfluent``. This is AgentFluent's own config tree —
    distinct from Claude Code's ``~/.claude/`` — and is where persistent
    user state (consent records, future tool config) lives. Established
    as the canonical config root in v0.8 with the Tier 3 consent file;
    additional consent surfaces and future config files (if any) live
    here.
    """
    xdg = os.environ.get(XDG_CONFIG_HOME_ENV_VAR)
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / AGENTFLUENT_SUBDIR


def agentfluent_cache_dir() -> Path:
    """Canonical AgentFluent cache root.

    Honors ``$XDG_CACHE_HOME`` when set, else falls back to
    ``~/.cache/agentfluent``. Used for the Tier 3 GitHub response cache
    (``<root>/github/<sha256>.json``); other ephemeral state can live
    in sibling subdirectories. Distinct from ``agentfluent_config_dir``
    because XDG separates ephemeral (cache) from persistent (config).
    """
    xdg = os.environ.get(XDG_CACHE_HOME_ENV_VAR)
    base = Path(xdg) if xdg else Path.home() / ".cache"
    return base / AGENTFLUENT_SUBDIR
