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

    def test_analyze_help_documents_since_until(
        self, runner: CliRunner, cli_app: typer.Typer,
    ) -> None:
        """`--since` and `--until` must be discoverable via `analyze --help`
        with both their option help and at least one usage example in the
        epilog (#299)."""
        result = runner.invoke(cli_app, ["analyze", "--help"])
        assert result.exit_code == 0
        assert "--since" in result.stdout
        assert "--until" in result.stdout
        # Half-open interval semantics surfaced in the epilog example.
        assert "half-open" in result.stdout
        # Baseline-for-diff workflow surfaced.
        assert "baseline.json" in result.stdout

    def test_list_help_documents_since_until(
        self, runner: CliRunner, cli_app: typer.Typer,
    ) -> None:
        """`list --help` documents `--since`/`--until` and shows a
        time-scoped session-preview example (#299)."""
        result = runner.invoke(cli_app, ["list", "--help"])
        assert result.exit_code == 0
        assert "--since" in result.stdout
        assert "--until" in result.stdout

    def test_top_level_help_shows_time_scoped_workflow(
        self, runner: CliRunner, cli_app: typer.Typer,
    ) -> None:
        """Top-level `--help` surfaces the time-scoped analyze workflow
        so `--since`/`--until` are discoverable without drilling into
        per-command help (#299)."""
        result = runner.invoke(cli_app, ["--help"])
        assert result.exit_code == 0
        assert "--since" in result.stdout
