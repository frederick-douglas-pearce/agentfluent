"""Path resolution for Claude config directories.

Single source of truth for the Claude config root. Subdirectory constants
(e.g., `DEFAULT_PROJECTS_DIR`, `DEFAULT_USER_AGENTS_DIR`) live with the
modules that use them, but derive from this root so an override replaces
the root once and all subdirectories compose from it.
"""

from __future__ import annotations

from pathlib import Path

DEFAULT_CLAUDE_CONFIG_DIR = Path.home() / ".claude"

CLAUDE_CONFIG_DIR_ENV_VAR = "CLAUDE_CONFIG_DIR"


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
