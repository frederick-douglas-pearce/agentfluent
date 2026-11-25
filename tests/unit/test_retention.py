"""Tests for cleanupPeriodDays retention detection (config/retention.py).

Covers the three states called out in #481 -- missing key (Claude
Code's 30-day default), explicit default/low value, and an explicit
long retention -- plus the settings precedence chain
(``settings.local.json`` > ``settings.json`` > user
``~/.claude/settings.json``), the 31-364 "deliberate choice" band, and
defensive handling of malformed / non-int values.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentfluent.config.models import EnvironmentWarning, Severity
from agentfluent.config.retention import (
    RECOMMENDED_RETENTION_DAYS,
    _load_settings,
    check_cleanup_retention,
    resolve_cleanup_period_days,
)


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> None:
    """Reset the per-path settings cache so each test reads fresh files."""
    _load_settings.cache_clear()


def _write_settings(path: Path, **keys: object) -> None:
    """Write a ``settings.json``-style file with the given top-level keys."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(keys))


def _user_cfg(tmp_path: Path) -> Path:
    """Create and return a stand-in for ``~/.claude/`` under ``tmp_path``."""
    cfg = tmp_path / "user" / ".claude"
    cfg.mkdir(parents=True)
    return cfg


# --- The three AC states ---------------------------------------------------


def test_missing_key_warns_with_default_30(tmp_path: Path) -> None:
    cfg = _user_cfg(tmp_path)
    warning = check_cleanup_retention(claude_config_dir=cfg, project_dir=None)

    assert isinstance(warning, EnvironmentWarning)
    assert warning.code == "cleanup_period_truncation"
    assert warning.severity is Severity.WARNING
    assert "30 days" in warning.message
    assert "unset" in warning.message
    # Recommends the long-retention value and quotes the user settings path.
    assert str(RECOMMENDED_RETENTION_DAYS) in warning.message
    assert str(cfg / "settings.json") in warning.message
    assert warning.remediation_path == cfg / "settings.json"


def test_explicit_default_value_warns(tmp_path: Path) -> None:
    cfg = _user_cfg(tmp_path)
    _write_settings(cfg / "settings.json", cleanupPeriodDays=30)

    warning = check_cleanup_retention(claude_config_dir=cfg, project_dir=None)

    assert warning is not None
    assert "30 days" in warning.message
    assert "unset" not in warning.message


def test_long_retention_no_warning(tmp_path: Path) -> None:
    cfg = _user_cfg(tmp_path)
    _write_settings(cfg / "settings.json", cleanupPeriodDays=3650)

    assert check_cleanup_retention(claude_config_dir=cfg, project_dir=None) is None


# --- Threshold bands -------------------------------------------------------


def test_low_explicit_value_warns(tmp_path: Path) -> None:
    cfg = _user_cfg(tmp_path)
    _write_settings(cfg / "settings.json", cleanupPeriodDays=7)

    warning = check_cleanup_retention(claude_config_dir=cfg, project_dir=None)

    assert warning is not None
    assert "7 days" in warning.message


@pytest.mark.parametrize("days", [31, 60, 364])
def test_mid_band_no_warning(tmp_path: Path, days: int) -> None:
    """31-364 days is a deliberate raise above default -- stays quiet."""
    cfg = _user_cfg(tmp_path)
    _write_settings(cfg / "settings.json", cleanupPeriodDays=days)

    assert check_cleanup_retention(claude_config_dir=cfg, project_dir=None) is None


def test_boundary_365_no_warning(tmp_path: Path) -> None:
    cfg = _user_cfg(tmp_path)
    _write_settings(cfg / "settings.json", cleanupPeriodDays=365)

    assert check_cleanup_retention(claude_config_dir=cfg, project_dir=None) is None


# --- Precedence chain ------------------------------------------------------


def test_project_local_overrides_user(tmp_path: Path) -> None:
    """settings.local.json wins over a warn-worthy user value (no warning)."""
    cfg = _user_cfg(tmp_path)
    _write_settings(cfg / "settings.json", cleanupPeriodDays=30)
    proj = tmp_path / "proj"
    _write_settings(proj / ".claude" / "settings.local.json", cleanupPeriodDays=3650)

    value, remediation = resolve_cleanup_period_days(cfg, proj)

    assert value == 3650
    # Remediation always points at the user file -- the durable fix.
    assert remediation == cfg / "settings.json"
    assert check_cleanup_retention(cfg, proj) is None


def test_project_shared_overrides_user(tmp_path: Path) -> None:
    """settings.json (shared) overrides user; a low value still warns."""
    cfg = _user_cfg(tmp_path)
    _write_settings(cfg / "settings.json", cleanupPeriodDays=3650)
    proj = tmp_path / "proj"
    _write_settings(proj / ".claude" / "settings.json", cleanupPeriodDays=7)

    value, _ = resolve_cleanup_period_days(cfg, proj)
    assert value == 7

    warning = check_cleanup_retention(cfg, proj)
    assert warning is not None
    assert "7 days" in warning.message


def test_local_beats_shared(tmp_path: Path) -> None:
    cfg = _user_cfg(tmp_path)
    proj = tmp_path / "proj"
    _write_settings(proj / ".claude" / "settings.json", cleanupPeriodDays=10)
    _write_settings(proj / ".claude" / "settings.local.json", cleanupPeriodDays=3650)

    value, _ = resolve_cleanup_period_days(cfg, proj)
    assert value == 3650


# --- Defensive parsing -----------------------------------------------------


def test_malformed_json_treated_as_missing(tmp_path: Path) -> None:
    cfg = _user_cfg(tmp_path)
    (cfg / "settings.json").write_text("{ not valid json")

    # Falls through to "missing" -> warns at the 30-day default.
    warning = check_cleanup_retention(claude_config_dir=cfg, project_dir=None)
    assert warning is not None
    assert "unset" in warning.message


@pytest.mark.parametrize("bad_value", ["30", True, 30.5, None, [30]])
def test_non_int_value_treated_as_missing(tmp_path: Path, bad_value: object) -> None:
    cfg = _user_cfg(tmp_path)
    _write_settings(cfg / "settings.json", cleanupPeriodDays=bad_value)

    value, _ = resolve_cleanup_period_days(cfg, None)
    assert value is None


def test_float_integer_value_accepted(tmp_path: Path) -> None:
    """A whole-number float (e.g. JSON ``3650.0``) is read as that int."""
    cfg = _user_cfg(tmp_path)
    _write_settings(cfg / "settings.json", cleanupPeriodDays=3650.0)

    value, _ = resolve_cleanup_period_days(cfg, None)
    assert value == 3650
    assert check_cleanup_retention(cfg, None) is None
