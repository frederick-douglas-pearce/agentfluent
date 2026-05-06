"""Tests for ``agentfluent list --since/--until`` (#296).

The seeded fixture session in ``populated_home`` has its first message
at 2026-04-10T10:00:00Z, so windows are anchored to that.
"""

from __future__ import annotations

import json
from pathlib import Path

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
    def test_since_after_session_excludes_it(
        self, runner: CliRunner, cli_app: typer.Typer, populated_home: Path,
    ) -> None:
        # Session is at 2026-04-10; --since 2026-05-01 is after, so the
        # session is excluded.
        result = runner.invoke(
            cli_app,
            [
                "list", "--project", "project",
                "--since", "2026-05-01",
                "--quiet",
            ],
        )
        assert result.exit_code == EXIT_OK
        assert "0 sessions" in result.stdout

    def test_since_before_session_includes_it(
        self, runner: CliRunner, cli_app: typer.Typer, populated_home: Path,
    ) -> None:
        result = runner.invoke(
            cli_app,
            [
                "list", "--project", "project",
                "--since", "2026-04-01",
                "--quiet",
            ],
        )
        assert result.exit_code == EXIT_OK
        assert "1 sessions" in result.stdout

    def test_until_before_session_excludes_it(
        self, runner: CliRunner, cli_app: typer.Typer, populated_home: Path,
    ) -> None:
        result = runner.invoke(
            cli_app,
            [
                "list", "--project", "project",
                "--until", "2026-04-01",
                "--quiet",
            ],
        )
        assert result.exit_code == EXIT_OK
        assert "0 sessions" in result.stdout

    def test_window_brackets_session(
        self, runner: CliRunner, cli_app: typer.Typer, populated_home: Path,
    ) -> None:
        result = runner.invoke(
            cli_app,
            [
                "list", "--project", "project",
                "--since", "2026-04-01",
                "--until", "2026-05-01",
                "--quiet",
            ],
        )
        assert result.exit_code == EXIT_OK
        assert "1 sessions" in result.stdout


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
