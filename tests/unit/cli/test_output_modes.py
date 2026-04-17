"""Verbose/quiet mode tests: line count + mutual exclusivity."""

from __future__ import annotations

from pathlib import Path

import typer
from typer.testing import CliRunner


def _visible_line_count(output: str) -> int:
    """Count non-empty lines from command output."""
    return sum(1 for line in output.splitlines() if line.strip())


class TestQuietLineCount:
    """AC: --quiet output is <= 5 lines."""

    def test_list_projects(
        self, runner: CliRunner, cli_app: typer.Typer, populated_home: Path,
    ) -> None:
        result = runner.invoke(cli_app, ["list", "--quiet"])
        assert result.exit_code == 0
        assert _visible_line_count(result.stdout) <= 5

    def test_list_sessions(
        self, runner: CliRunner, cli_app: typer.Typer, populated_home: Path,
    ) -> None:
        result = runner.invoke(cli_app, ["list", "--project", "project", "--quiet"])
        assert result.exit_code == 0
        assert _visible_line_count(result.stdout) <= 5

    def test_analyze(
        self, runner: CliRunner, cli_app: typer.Typer, populated_home: Path,
    ) -> None:
        result = runner.invoke(
            cli_app, ["analyze", "--project", "project", "--quiet"],
        )
        assert result.exit_code == 0
        assert _visible_line_count(result.stdout) <= 5

    def test_config_check(
        self, runner: CliRunner, cli_app: typer.Typer, populated_home: Path,
    ) -> None:
        result = runner.invoke(cli_app, ["config-check", "--scope", "user", "--quiet"])
        assert result.exit_code == 0
        assert _visible_line_count(result.stdout) <= 5


class TestVerboseQuietMutualExclusion:
    def test_list(
        self, runner: CliRunner, cli_app: typer.Typer, populated_home: Path,
    ) -> None:
        result = runner.invoke(cli_app, ["list", "--verbose", "--quiet"])
        assert result.exit_code == 2
        assert "mutually exclusive" in result.stderr

    def test_analyze(
        self, runner: CliRunner, cli_app: typer.Typer, populated_home: Path,
    ) -> None:
        result = runner.invoke(
            cli_app,
            ["analyze", "--project", "project", "--verbose", "--quiet"],
        )
        assert result.exit_code == 2
        assert "mutually exclusive" in result.stderr

    def test_config_check(
        self, runner: CliRunner, cli_app: typer.Typer, populated_home: Path,
    ) -> None:
        result = runner.invoke(cli_app, ["config-check", "--verbose", "--quiet"])
        assert result.exit_code == 2
        assert "mutually exclusive" in result.stderr
