"""JSON envelope schema + ANSI-escape tests (#39 contract)."""

from __future__ import annotations

from pathlib import Path

import typer
from typer.testing import CliRunner

from agentfluent.cli.formatters.json_output import parse_json_output

ANSI_ESC = "\x1b"


def _assert_clean_envelope(stdout: str, expected_command: str) -> object:
    """Assert no ANSI escapes, valid envelope, expected command; return data."""
    assert ANSI_ESC not in stdout, "JSON output must not contain ANSI escape sequences"
    return parse_json_output(stdout, expected_command=expected_command)  # type: ignore[arg-type]


class TestListJsonEnvelope:
    def test_projects_envelope(
        self, runner: CliRunner, cli_app: typer.Typer, populated_home: Path,
    ) -> None:
        result = runner.invoke(cli_app, ["list", "--format", "json"])
        assert result.exit_code == 0
        data = _assert_clean_envelope(result.stdout, "list-projects")
        assert "projects" in data

    def test_sessions_envelope(
        self, runner: CliRunner, cli_app: typer.Typer, populated_home: Path,
    ) -> None:
        result = runner.invoke(
            cli_app, ["list", "--project", "project", "--format", "json"],
        )
        assert result.exit_code == 0
        data = _assert_clean_envelope(result.stdout, "list-sessions")
        assert "sessions" in data

    def test_projects_quiet_envelope(
        self, runner: CliRunner, cli_app: typer.Typer, populated_home: Path,
    ) -> None:
        result = runner.invoke(cli_app, ["list", "--format", "json", "--quiet"])
        assert result.exit_code == 0
        data = _assert_clean_envelope(result.stdout, "list-projects")
        assert data == {"project_count": 1, "total_sessions": 1}


class TestAnalyzeJsonEnvelope:
    def test_envelope(
        self, runner: CliRunner, cli_app: typer.Typer, populated_home: Path,
    ) -> None:
        result = runner.invoke(
            cli_app, ["analyze", "--project", "project", "--format", "json"],
        )
        assert result.exit_code == 0
        data = _assert_clean_envelope(result.stdout, "analyze")
        assert "token_metrics" in data
        assert "sessions" in data

    def test_quiet_envelope(
        self, runner: CliRunner, cli_app: typer.Typer, populated_home: Path,
    ) -> None:
        result = runner.invoke(
            cli_app,
            ["analyze", "--project", "project", "--format", "json", "--quiet"],
        )
        assert result.exit_code == 0
        data = _assert_clean_envelope(result.stdout, "analyze")
        assert set(data.keys()) == {
            "project",
            "session_count",
            "total_cost",
            "total_tokens",
            "total_invocations",
            "diagnostic_signal_count",
        }


class TestConfigCheckJsonEnvelope:
    def test_envelope(
        self, runner: CliRunner, cli_app: typer.Typer, populated_home: Path,
    ) -> None:
        result = runner.invoke(
            cli_app, ["config-check", "--scope", "user", "--format", "json"],
        )
        assert result.exit_code == 0
        data = _assert_clean_envelope(result.stdout, "config-check")
        assert "scores" in data

    def test_quiet_envelope(
        self, runner: CliRunner, cli_app: typer.Typer, populated_home: Path,
    ) -> None:
        result = runner.invoke(
            cli_app,
            ["config-check", "--scope", "user", "--format", "json", "--quiet"],
        )
        assert result.exit_code == 0
        data = _assert_clean_envelope(result.stdout, "config-check")
        assert set(data.keys()) == {
            "agent_count",
            "average_score",
            "recommendation_count",
        }
