"""Detect Claude Code's session-retention setting (``cleanupPeriodDays``).

Claude Code deletes session JSONL files older than ``cleanupPeriodDays``
from ``~/.claude/projects/`` — and the default is **30 days**. Users
rarely know this setting exists, so the corpus AgentFluent can analyze
is silently bounded: multi-month longitudinal analysis (regression
detection, baselines) quietly loses its oldest data with no recovery
path (Claude Code deletes, it does not archive).

This module reads the effective ``cleanupPeriodDays`` at analysis time
and produces an :class:`EnvironmentWarning` when retention is at or
below the default — surfacing the problem the AgentFluent product story
is built on: *the tool tells you what your environment isn't telling
you*. See issue #481.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

from agentfluent.config.models import EnvironmentWarning, Severity
from agentfluent.core.paths import settings_path_for

logger = logging.getLogger(__name__)

SETTINGS_KEY = "cleanupPeriodDays"

DEFAULT_CLEANUP_PERIOD_DAYS = 30
"""Claude Code's default retention when ``cleanupPeriodDays`` is unset.
Sessions older than this are deleted, so a *missing* key is treated the
same as an explicit ``30``."""

WARN_THRESHOLD_DAYS = DEFAULT_CLEANUP_PERIOD_DAYS
"""Warn when the effective retention is no better than Claude Code's
unconfigured default — i.e. at or below ``DEFAULT_CLEANUP_PERIOD_DAYS``.
Above it (31+), the user has deliberately raised retention past the
default, so no warning fires (#481 AC: no warning at ``>= 365``; the
31–364 band is a deliberate user choice and likewise stays quiet).
Deriving from the default (rather than a second ``30`` literal) keeps
the "warn at-or-below default" relationship explicit and single-sourced."""

RECOMMENDED_RETENTION_DAYS = 3650
"""The long-retention value (~10 years) the warning recommends adding."""

PROJECT_SETTINGS_SUBDIR = ".claude"
PROJECT_SETTINGS_FILENAME = "settings.json"
PROJECT_SETTINGS_LOCAL_FILENAME = "settings.local.json"


@lru_cache(maxsize=16)
def _load_settings(path: Path) -> dict[str, Any] | None:
    """Read and decode a Claude Code ``settings.json``-style file.

    Returns ``None`` for missing files (silent — most projects have no
    local settings), logs a warning and returns ``None`` for malformed
    JSON or a non-object root. Cached per-path; tests that need a fresh
    read call ``_load_settings.cache_clear()`` in setup.
    """
    if not path.exists():
        return None
    try:
        with path.open() as f:
            data = json.load(f)
    except json.JSONDecodeError:
        logger.warning("Malformed JSON in Claude settings file: %s", path)
        return None
    except OSError:
        logger.warning("Could not read Claude settings file: %s", path, exc_info=True)
        return None
    if not isinstance(data, dict):
        logger.warning("Claude settings file root is not an object: %s", path)
        return None
    return data


def _read_cleanup_value(path: Path) -> int | None:
    """Return a valid integer ``cleanupPeriodDays`` from ``path``, else ``None``.

    ``None`` covers every "not configured here" case: the file is
    missing/malformed, the key is absent, or the value is not a usable
    integer (booleans and floats with a fractional part are rejected —
    a non-int retention is meaningless and treated as unset).
    """
    data = _load_settings(path)
    if data is None:
        return None
    value = data.get(SETTINGS_KEY)
    # bool is an int subclass; reject it explicitly.
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def resolve_cleanup_period_days(
    claude_config_dir: Path | None,
    project_dir: Path | None,
) -> tuple[int | None, Path]:
    """Resolve the effective ``cleanupPeriodDays`` and the remediation path.

    Mirrors Claude Code's settings precedence
    (``project_local > project_shared > user``): the first file in that
    order to define an integer ``cleanupPeriodDays`` wins. Reading
    ``settings.local.json`` matters — power users (the ones most likely
    to adopt AgentFluent) often set retention there, and skipping it
    would raise a false-positive warning.

    Returns ``(value_or_None, user_settings_path)``. ``value_or_None`` is
    ``None`` only when *no* source defined the key (Claude Code then
    applies its 30-day default). ``user_settings_path`` is always
    ``~/.claude/settings.json`` (or the ``--claude-config-dir``
    equivalent) — the file the warning tells the user to edit, since a
    user-scope override is the durable, project-independent fix.
    """
    user_settings_path = settings_path_for(claude_config_dir)

    candidates: list[Path] = []
    if project_dir is not None:
        project_claude = project_dir / PROJECT_SETTINGS_SUBDIR
        candidates.append(project_claude / PROJECT_SETTINGS_LOCAL_FILENAME)
        candidates.append(project_claude / PROJECT_SETTINGS_FILENAME)
    candidates.append(user_settings_path)

    for candidate in candidates:
        value = _read_cleanup_value(candidate)
        if value is not None:
            return value, user_settings_path

    return None, user_settings_path


def check_cleanup_retention(
    claude_config_dir: Path | None,
    project_dir: Path | None,
) -> EnvironmentWarning | None:
    """Return a retention warning when ``cleanupPeriodDays`` is at/below default.

    Fires when the effective value is missing (Claude Code's 30-day
    default applies) or explicitly ``<= 30``. Returns ``None`` when the
    user has raised retention above the default — they have made a
    deliberate choice and need no nudge.
    """
    value, user_settings_path = resolve_cleanup_period_days(
        claude_config_dir, project_dir,
    )

    if value is not None and value > WARN_THRESHOLD_DAYS:
        return None

    is_unset = value is None
    effective_days = DEFAULT_CLEANUP_PERIOD_DAYS if is_unset else value
    default_note = (
        f" (unset — Claude Code's {DEFAULT_CLEANUP_PERIOD_DAYS}-day default applies)"
        if is_unset
        else ""
    )

    message = (
        f"Claude Code `{SETTINGS_KEY}` is {effective_days} days{default_note}. "
        f"Sessions older than {effective_days} days have been deleted from "
        f"~/.claude/projects/ and cannot be recovered, so this analysis may "
        f"cover less history than expected. To preserve session data for "
        f'future analysis, add "{SETTINGS_KEY}": {RECOMMENDED_RETENTION_DAYS} '
        f"to {user_settings_path}."
    )

    return EnvironmentWarning(
        code="cleanup_period_truncation",
        severity=Severity.WARNING,
        message=message,
        remediation_path=user_settings_path,
    )
