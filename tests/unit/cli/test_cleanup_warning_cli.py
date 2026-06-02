"""End-to-end CLI coverage for the cleanupPeriodDays warning (#481).

Drives ``agentfluent analyze`` against a ``--claude-config-dir`` whose
``settings.json`` is missing, low, or long, and asserts the warning
banner appears (table) and the warning rides in the JSON envelope —
while a long-retention config stays silent.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import typer
from typer.testing import CliRunner

from agentfluent.cli.exit_codes import EXIT_OK
from agentfluent.cli.formatters.json_output import parse_json_output
from agentfluent.config.retention import _load_settings


def _make_config_with_project(root: Path, fixtures_dir: Path) -> None:
    project_dir = root / "projects" / "-home-user-test-project"
    project_dir.mkdir(parents=True)
    shutil.copy(fixtures_dir / "session_basic.jsonl", project_dir / "s1.jsonl")


def _analyze(
    runner: CliRunner, cli_app: typer.Typer, config_dir: Path, *extra: str,
) -> str:
    _load_settings.cache_clear()
    result = runner.invoke(
        cli_app,
        ["--claude-config-dir", str(config_dir), "analyze", "--project", "project", *extra],
    )
    assert result.exit_code == EXIT_OK, result.stdout
    return result.stdout


def test_missing_settings_warns_in_table(
    runner: CliRunner, cli_app: typer.Typer, tmp_path: Path, fixtures_dir: Path,
) -> None:
    cfg = tmp_path / "claude"
    _make_config_with_project(cfg, fixtures_dir)
    # No settings.json written -> Claude Code's 30-day default applies.

    out = _analyze(runner, cli_app, cfg)

    assert "⚠" in out
    assert "cleanupPeriodDays" in out
    # Rich may wrap the long remediation path across lines, so assert on
    # wrap-stable fragments; the full-path contract is covered at the
    # message level in test_retention.py.
    assert "settings.json" in out
    assert "3650" in out


def test_long_retention_no_warning_in_table(
    runner: CliRunner, cli_app: typer.Typer, tmp_path: Path, fixtures_dir: Path,
) -> None:
    cfg = tmp_path / "claude"
    _make_config_with_project(cfg, fixtures_dir)
    (cfg / "settings.json").write_text(json.dumps({"cleanupPeriodDays": 3650}))

    out = _analyze(runner, cli_app, cfg)

    assert "cleanupPeriodDays" not in out


def test_warning_in_json_envelope(
    runner: CliRunner, cli_app: typer.Typer, tmp_path: Path, fixtures_dir: Path,
) -> None:
    cfg = tmp_path / "claude"
    _make_config_with_project(cfg, fixtures_dir)

    out = _analyze(runner, cli_app, cfg, "--json")

    data = parse_json_output(out, expected_command="analyze")
    warnings = data["warnings"]
    assert len(warnings) == 1
    assert warnings[0]["code"] == "cleanup_period_truncation"
    assert warnings[0]["severity"] == "warning"
    assert warnings[0]["remediation_path"] == str(cfg / "settings.json")
