"""Top-level --help / --version and per-command --help examples."""

from __future__ import annotations

import typer
from typer.testing import CliRunner

from agentfluent import __version__


class TestVersion:
    def test_version_flag(self, runner: CliRunner, cli_app: typer.Typer) -> None:
        result = runner.invoke(cli_app, ["--version"])
        assert result.exit_code == 0
        assert __version__ in result.stdout


class TestHelp:
    def test_top_level_help(self, runner: CliRunner, cli_app: typer.Typer) -> None:
        result = runner.invoke(cli_app, ["--help"])
        assert result.exit_code == 0
        assert "list" in result.stdout
        assert "analyze" in result.stdout
        assert "config-check" in result.stdout
        assert "explain" in result.stdout

    def test_list_help_has_examples(
        self, runner: CliRunner, cli_app: typer.Typer,
    ) -> None:
        result = runner.invoke(cli_app, ["list", "--help"])
        assert result.exit_code == 0
        assert "Examples" in result.stdout

    def test_analyze_help_has_examples(
        self, runner: CliRunner, cli_app: typer.Typer,
    ) -> None:
        result = runner.invoke(cli_app, ["analyze", "--help"])
        assert result.exit_code == 0
        assert "Examples" in result.stdout

    def test_config_check_help_has_examples(
        self, runner: CliRunner, cli_app: typer.Typer,
    ) -> None:
        result = runner.invoke(cli_app, ["config-check", "--help"])
        assert result.exit_code == 0
        assert "Examples" in result.stdout
