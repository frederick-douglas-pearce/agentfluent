"""Exit code contract tests.

The invariant: user named something specific and it's wrong = 1;
system searched and found nothing = 2. `typer.BadParameter` exits 2
by Click convention; that's intentional and out of the invariant's scope.
"""

from __future__ import annotations

from pathlib import Path

import typer
from typer.testing import CliRunner

from agentfluent.cli.exit_codes import EXIT_NO_DATA, EXIT_OK, EXIT_USER_ERROR


class TestListExitCodes:
    def test_success_lists_projects(
        self, runner: CliRunner, cli_app: typer.Typer, populated_home: Path,
    ) -> None:
        result = runner.invoke(cli_app, ["list"])
        assert result.exit_code == EXIT_OK

    def test_missing_project(
        self, runner: CliRunner, cli_app: typer.Typer, populated_home: Path,
    ) -> None:
        result = runner.invoke(cli_app, ["list", "--project", "does-not-exist"])
        assert result.exit_code == EXIT_USER_ERROR

    def test_no_projects_dir(
        self, runner: CliRunner, cli_app: typer.Typer, isolated_home: Path,
    ) -> None:
        (isolated_home / "projects").rmdir()
        result = runner.invoke(cli_app, ["list"])
        assert result.exit_code == EXIT_NO_DATA


class TestAnalyzeExitCodes:
    def test_success(
        self, runner: CliRunner, cli_app: typer.Typer, populated_home: Path,
    ) -> None:
        result = runner.invoke(cli_app, ["analyze", "--project", "project"])
        assert result.exit_code == EXIT_OK

    def test_missing_project(
        self, runner: CliRunner, cli_app: typer.Typer, populated_home: Path,
    ) -> None:
        result = runner.invoke(
            cli_app, ["analyze", "--project", "does-not-exist"],
        )
        assert result.exit_code == EXIT_USER_ERROR

    def test_missing_named_session(
        self, runner: CliRunner, cli_app: typer.Typer, populated_home: Path,
    ) -> None:
        result = runner.invoke(
            cli_app,
            ["analyze", "--project", "project", "--session", "no-such.jsonl"],
        )
        assert result.exit_code == EXIT_USER_ERROR

    def test_project_with_no_sessions(
        self, runner: CliRunner, cli_app: typer.Typer, isolated_home: Path,
    ) -> None:
        empty_project = isolated_home / "projects" / "-home-user-empty"
        empty_project.mkdir()
        result = runner.invoke(cli_app, ["analyze", "--project", "empty"])
        assert result.exit_code == EXIT_NO_DATA


class TestConfigCheckExitCodes:
    def test_success(
        self, runner: CliRunner, cli_app: typer.Typer, populated_home: Path,
    ) -> None:
        result = runner.invoke(cli_app, ["config-check", "--scope", "user"])
        assert result.exit_code == EXIT_OK

    def test_invalid_scope(
        self, runner: CliRunner, cli_app: typer.Typer, populated_home: Path,
    ) -> None:
        result = runner.invoke(cli_app, ["config-check", "--scope", "nonsense"])
        assert result.exit_code == EXIT_USER_ERROR

    def test_missing_named_agent(
        self, runner: CliRunner, cli_app: typer.Typer, populated_home: Path,
    ) -> None:
        result = runner.invoke(
            cli_app, ["config-check", "--agent", "does-not-exist"],
        )
        assert result.exit_code == EXIT_USER_ERROR

    def test_no_agents_found(
        self, runner: CliRunner, cli_app: typer.Typer, isolated_home: Path,
    ) -> None:
        result = runner.invoke(cli_app, ["config-check", "--scope", "user"])
        assert result.exit_code == EXIT_NO_DATA


class TestBadParameter:
    """Typer's BadParameter exits 2 by Click convention. Documented exception."""

    def test_verbose_and_quiet_exits_2(
        self, runner: CliRunner, cli_app: typer.Typer, populated_home: Path,
    ) -> None:
        result = runner.invoke(cli_app, ["list", "--verbose", "--quiet"])
        assert result.exit_code == 2
        assert "mutually exclusive" in result.stderr
