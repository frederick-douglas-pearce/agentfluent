"""Top-level --help / --version and per-command --help examples."""

from __future__ import annotations

import re

import typer
from typer.testing import CliRunner

from agentfluent import __version__

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")


def _flatten_help(stdout: str) -> str:
    """Strip ANSI color codes and collapse whitespace runs.

    Rich wraps long help/epilog lines at terminal-width boundaries, so
    a literal substring like ``"7 days ago"`` may be split across lines
    in the raw stdout. ``' '.join(s.split())`` collapses any whitespace
    run (including newlines) to a single space, letting tests assert on
    semantic substrings without coupling to a specific rendering width.
    """
    return " ".join(_ANSI_RE.sub("", stdout).split())


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

    def test_analyze_accepts_show_negative_savings_flag(
        self, runner: CliRunner, cli_app: typer.Typer,
    ) -> None:
        """``--show-negative-savings`` is wired (#344). Click's CliRunner
        truncates the rendered ``--help`` block past a certain option
        count, so asserting on help-text presence is unreliable; instead,
        invoke with a known-bad project and check the flag itself doesn't
        produce a 'No such option' parse error before the project lookup
        fails."""
        result = runner.invoke(
            cli_app,
            ["analyze", "--project", "__nonexistent__", "--show-negative-savings"],
        )
        # Project not found, but the flag was accepted by the parser.
        assert "No such option" not in result.output
        assert "Unknown option" not in result.output

    def test_analyze_help_documents_since_until(
        self, runner: CliRunner, cli_app: typer.Typer,
    ) -> None:
        """`analyze --help` shows the date-range epilog examples (#299).

        Assertions target distinctive **epilog prose** matched against
        whitespace-flattened stdout, so any line wrapping Rich applies
        at the rendered width doesn't break the substring check.
        """
        result = runner.invoke(cli_app, ["analyze", "--help"])
        assert result.exit_code == 0
        flat = _flatten_help(result.stdout)
        # Distinctive epilog content unique to #299.
        assert "baseline.json" in flat
        assert "7 days ago" in flat
        # Half-open semantics surfaced in the dual-flag epilog example
        # ("Analyze sessions in the half-open interval ...").
        assert "half-open" in flat

    def test_list_help_documents_since_until(
        self, runner: CliRunner, cli_app: typer.Typer,
    ) -> None:
        """`list --help` shows the date-range epilog examples (#299)."""
        result = runner.invoke(cli_app, ["list", "--help"])
        assert result.exit_code == 0
        flat = _flatten_help(result.stdout)
        # Half-open semantics surfaced in the list epilog example
        # ("Sessions in the half-open interval ...").
        assert "half-open" in flat

    def test_top_level_help_shows_time_scoped_workflow(
        self, runner: CliRunner, cli_app: typer.Typer,
    ) -> None:
        """Top-level `--help` surfaces the time-scoped analyze workflow
        so the date-range pattern is discoverable without drilling into
        per-command help (#299)."""
        result = runner.invoke(cli_app, ["--help"])
        assert result.exit_code == 0
        flat = _flatten_help(result.stdout)
        # Distinctive prose from the new top-level workflow example.
        assert "current.json" in flat
        assert "Scope analysis" in flat
