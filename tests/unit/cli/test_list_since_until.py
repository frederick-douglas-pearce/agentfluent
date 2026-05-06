"""Tests for ``agentfluent list --since/--until``.

The seeded fixture session in ``populated_home`` has its first message
at 2026-04-10T10:00:00Z, so windows are anchored to that.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from agentfluent.cli.exit_codes import EXIT_OK, EXIT_USER_ERROR


class TestRequiresProject:
    """``--since`` / ``--until`` are session-scope filters; without
    ``--project`` they have no meaningful target."""

    def test_since_without_project_errors(
        self, runner: CliRunner, cli_app: typer.Typer, populated_home: Path,
    ) -> None:
        result = runner.invoke(cli_app, ["list", "--since", "7d"])
        assert result.exit_code == EXIT_USER_ERROR
        assert "--since/--until require --project" in result.stderr

    def test_until_without_project_errors(
        self, runner: CliRunner, cli_app: typer.Typer, populated_home: Path,
    ) -> None:
        result = runner.invoke(cli_app, ["list", "--until", "2026-05-01"])
        assert result.exit_code == EXIT_USER_ERROR
        assert "--since/--until require --project" in result.stderr


class TestFilterApplied:
    """Session at 2026-04-10 is the only fixture session; each window
    bracket either includes (1 session) or excludes (0 sessions) it."""

    @pytest.mark.parametrize(
        ("flags", "expected_count_str"),
        [
            (["--since", "2026-05-01"], "0 sessions"),  # window after session
            (["--since", "2026-04-01"], "1 sessions"),  # session at-or-after since
            (["--until", "2026-04-01"], "0 sessions"),  # session after until
            (
                ["--since", "2026-04-01", "--until", "2026-05-01"],
                "1 sessions",
            ),  # session in window
        ],
    )
    def test_window_filters_session(
        self,
        runner: CliRunner,
        cli_app: typer.Typer,
        populated_home: Path,
        flags: list[str],
        expected_count_str: str,
    ) -> None:
        result = runner.invoke(
            cli_app,
            ["list", "--project", "project", *flags, "--quiet"],
        )
        assert result.exit_code == EXIT_OK
        assert expected_count_str in result.stdout


class TestErrors:
    def test_unparseable_since(
        self, runner: CliRunner, cli_app: typer.Typer, populated_home: Path,
    ) -> None:
        result = runner.invoke(
            cli_app,
            ["list", "--project", "project", "--since", "not-a-date"],
        )
        assert result.exit_code == EXIT_USER_ERROR
        assert "--since" in result.stderr
        assert "not-a-date" in result.stderr

    def test_inverted_interval(
        self, runner: CliRunner, cli_app: typer.Typer, populated_home: Path,
    ) -> None:
        result = runner.invoke(
            cli_app,
            [
                "list", "--project", "project",
                "--since", "2026-05-08",
                "--until", "2026-05-01",
            ],
        )
        assert result.exit_code == EXIT_USER_ERROR
        assert "swap" in result.stderr


class TestOutputFormats:
    def test_json_respects_filter(
        self, runner: CliRunner, cli_app: typer.Typer, populated_home: Path,
    ) -> None:
        # Session is at 2026-04-10; --since 2026-05-01 excludes it.
        result = runner.invoke(
            cli_app,
            [
                "list", "--project", "project",
                "--since", "2026-05-01",
                "--json",
            ],
        )
        assert result.exit_code == EXIT_OK
        envelope = json.loads(result.stdout)
        assert envelope["data"]["sessions"] == []

    def test_json_quiet_count_reflects_filter(
        self, runner: CliRunner, cli_app: typer.Typer, populated_home: Path,
    ) -> None:
        result = runner.invoke(
            cli_app,
            [
                "list", "--project", "project",
                "--since", "2026-05-01",
                "--json", "--quiet",
            ],
        )
        assert result.exit_code == EXIT_OK
        envelope = json.loads(result.stdout)
        assert envelope["data"]["session_count"] == 0
