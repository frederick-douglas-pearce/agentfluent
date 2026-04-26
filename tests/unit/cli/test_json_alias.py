"""--json shortcut: must match --format json output (#196)."""

from __future__ import annotations

import re
from pathlib import Path

import typer
from typer.testing import CliRunner

# Rich/Typer style each character of an option with separate ANSI escapes
# when FORCE_COLOR is set (CI does this), so a literal "--json" substring
# search fails on raw stdout. Strip color codes before asserting.
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


class TestJsonAliasMatchesFormatJson:
    """`--json` produces byte-identical output to `--format json`."""

    def test_list_projects(
        self, runner: CliRunner, cli_app: typer.Typer, populated_home: Path,
    ) -> None:
        canonical = runner.invoke(cli_app, ["list", "--format", "json"])
        alias = runner.invoke(cli_app, ["list", "--json"])
        assert canonical.exit_code == 0
        assert alias.exit_code == 0
        assert alias.stdout == canonical.stdout

    def test_list_sessions(
        self, runner: CliRunner, cli_app: typer.Typer, populated_home: Path,
    ) -> None:
        canonical = runner.invoke(
            cli_app, ["list", "--project", "project", "--format", "json"],
        )
        alias = runner.invoke(cli_app, ["list", "--project", "project", "--json"])
        assert canonical.exit_code == 0
        assert alias.exit_code == 0
        assert alias.stdout == canonical.stdout

    def test_analyze(
        self, runner: CliRunner, cli_app: typer.Typer, populated_home: Path,
    ) -> None:
        canonical = runner.invoke(
            cli_app, ["analyze", "--project", "project", "--format", "json"],
        )
        alias = runner.invoke(
            cli_app, ["analyze", "--project", "project", "--json"],
        )
        assert canonical.exit_code == 0
        assert alias.exit_code == 0
        assert alias.stdout == canonical.stdout

    def test_config_check(
        self, runner: CliRunner, cli_app: typer.Typer, populated_home: Path,
    ) -> None:
        canonical = runner.invoke(
            cli_app, ["config-check", "--scope", "user", "--format", "json"],
        )
        alias = runner.invoke(
            cli_app, ["config-check", "--scope", "user", "--json"],
        )
        assert canonical.exit_code == 0
        assert alias.exit_code == 0
        assert alias.stdout == canonical.stdout


class TestJsonAliasOverridesFormat:
    """`--json` wins when both flags are present (documented precedence)."""

    def test_analyze_json_overrides_table(
        self, runner: CliRunner, cli_app: typer.Typer, populated_home: Path,
    ) -> None:
        result = runner.invoke(
            cli_app,
            [
                "analyze",
                "--project", "project",
                "--format", "table",
                "--json",
            ],
        )
        assert result.exit_code == 0
        assert result.stdout.lstrip().startswith("{")


class TestJsonAliasInHelp:
    """--help text mentions the alias on each subcommand."""

    def test_list_help(
        self, runner: CliRunner, cli_app: typer.Typer,
    ) -> None:
        result = runner.invoke(cli_app, ["list", "--help"])
        assert result.exit_code == 0
        assert "--json" in _strip_ansi(result.stdout)

    def test_analyze_help(
        self, runner: CliRunner, cli_app: typer.Typer,
    ) -> None:
        result = runner.invoke(cli_app, ["analyze", "--help"])
        assert result.exit_code == 0
        assert "--json" in _strip_ansi(result.stdout)

    def test_config_check_help(
        self, runner: CliRunner, cli_app: typer.Typer,
    ) -> None:
        result = runner.invoke(cli_app, ["config-check", "--help"])
        assert result.exit_code == 0
        assert "--json" in _strip_ansi(result.stdout)
