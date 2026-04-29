"""End-to-end tests for ``agentfluent explain``."""

from __future__ import annotations

import typer
from typer.testing import CliRunner


class TestExactLookup:
    def test_known_term_renders(
        self, runner: CliRunner, cli_app: typer.Typer,
    ) -> None:
        result = runner.invoke(cli_app, ["explain", "token_outlier"])
        assert result.exit_code == 0
        assert "token_outlier" in result.stdout
        assert "Severity" in result.stdout

    def test_alias_resolves(self, runner: CliRunner, cli_app: typer.Typer) -> None:
        # `prompt` is declared as an alias for `target_prompt` in terms.yaml.
        result = runner.invoke(cli_app, ["explain", "prompt"])
        assert result.exit_code == 0
        assert "target_prompt" in result.stdout


class TestFuzzyLookup:
    def test_single_fuzzy_match_auto_renders(
        self, runner: CliRunner, cli_app: typer.Typer,
    ) -> None:
        result = runner.invoke(cli_app, ["explain", "token-outlier"])
        assert result.exit_code == 0
        assert "Closest" in result.stdout
        assert "token_outlier" in result.stdout

    def test_ambiguous_fuzzy_lists_candidates(
        self, runner: CliRunner, cli_app: typer.Typer,
    ) -> None:
        result = runner.invoke(cli_app, ["explain", "tools"])
        assert result.exit_code != 0
        # `tools` substring matches at least target_tools and concern_tools
        # (and others); the "did you mean" branch uses stderr.
        combined = result.stdout + result.stderr
        assert "Did you mean" in combined
        assert "target_tools" in combined
        assert "concern_tools" in combined

    def test_unknown_term_exits_with_user_error(
        self, runner: CliRunner, cli_app: typer.Typer,
    ) -> None:
        result = runner.invoke(cli_app, ["explain", "definitely_not_a_term"])
        assert result.exit_code == 1
        combined = result.stdout + result.stderr
        assert "not found" in combined


class TestListing:
    def test_explain_no_args_lists_all(
        self, runner: CliRunner, cli_app: typer.Typer,
    ) -> None:
        result = runner.invoke(cli_app, ["explain"])
        assert result.exit_code == 0
        # Listing should show the section labels for at least the most
        # heavily used categories.
        assert "Token types" in result.stdout
        assert "Signal types" in result.stdout

    def test_explain_list_flag(
        self, runner: CliRunner, cli_app: typer.Typer,
    ) -> None:
        result = runner.invoke(cli_app, ["explain", "--list"])
        assert result.exit_code == 0
        assert "Token types" in result.stdout

    def test_category_filter(
        self, runner: CliRunner, cli_app: typer.Typer,
    ) -> None:
        result = runner.invoke(cli_app, ["explain", "--category", "signal_type"])
        assert result.exit_code == 0
        assert "Signal types" in result.stdout
        assert "token_outlier" in result.stdout
        # Should not show terms from other categories
        assert "Token types" not in result.stdout

    def test_unknown_category_errors(
        self, runner: CliRunner, cli_app: typer.Typer,
    ) -> None:
        result = runner.invoke(cli_app, ["explain", "--category", "nonsense"])
        assert result.exit_code == 1
        combined = result.stdout + result.stderr
        assert "Unknown category" in combined


class TestHelp:
    def test_explain_help_has_examples(
        self, runner: CliRunner, cli_app: typer.Typer,
    ) -> None:
        result = runner.invoke(cli_app, ["explain", "--help"])
        assert result.exit_code == 0
        assert "Examples" in result.stdout
