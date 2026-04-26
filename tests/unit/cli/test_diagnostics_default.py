"""Verify #202: --diagnostics defaults to on, --no-diagnostics opts out.

Diagnostics is the core value proposition; running ``agentfluent analyze``
without flags should surface diagnostic signals automatically. The
``--no-diagnostics`` opt-out exists for token-only / CI use cases where
the diagnostics pipeline would be overhead.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import typer
from typer.testing import CliRunner

# Rich/Typer style each char of an option with separate ANSI escapes when
# FORCE_COLOR is set (CI does this), so a literal substring search fails
# on raw stdout. Strip color codes before asserting.
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


class TestDiagnosticsDefaultOn:
    def test_no_flag_runs_diagnostics(
        self,
        runner: CliRunner,
        cli_app: typer.Typer,
        populated_home_with_traces: Path,
    ) -> None:
        result = runner.invoke(cli_app, ["analyze", "--project", "project"])
        assert result.exit_code == 0
        assert "Diagnostic Signals" in result.stdout

    def test_explicit_diagnostics_flag_still_works(
        self,
        runner: CliRunner,
        cli_app: typer.Typer,
        populated_home_with_traces: Path,
    ) -> None:
        """Backwards compatibility: ``--diagnostics`` still parses and is a no-op
        relative to the new default."""
        result = runner.invoke(
            cli_app, ["analyze", "--project", "project", "--diagnostics"],
        )
        assert result.exit_code == 0
        assert "Diagnostic Signals" in result.stdout

    def test_no_flag_includes_diagnostics_in_json(
        self,
        runner: CliRunner,
        cli_app: typer.Typer,
        populated_home_with_traces: Path,
    ) -> None:
        result = runner.invoke(
            cli_app, ["analyze", "--project", "project", "--format", "json"],
        )
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        diagnostics = payload["data"].get("diagnostics")
        assert diagnostics is not None
        assert diagnostics.get("signals"), "expected signals in default analyze"


class TestNoDiagnosticsOptOut:
    def test_no_diagnostics_skips_signal_table(
        self,
        runner: CliRunner,
        cli_app: typer.Typer,
        populated_home_with_traces: Path,
    ) -> None:
        result = runner.invoke(
            cli_app, ["analyze", "--project", "project", "--no-diagnostics"],
        )
        assert result.exit_code == 0
        assert "Diagnostic Signals" not in result.stdout

    def test_no_diagnostics_omits_diagnostics_in_json(
        self,
        runner: CliRunner,
        cli_app: typer.Typer,
        populated_home_with_traces: Path,
    ) -> None:
        result = runner.invoke(
            cli_app,
            [
                "analyze",
                "--project", "project",
                "--no-diagnostics",
                "--format", "json",
            ],
        )
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        diagnostics = payload["data"].get("diagnostics")
        assert diagnostics is None

    def test_short_flag_negation(
        self,
        runner: CliRunner,
        cli_app: typer.Typer,
        populated_home_with_traces: Path,
    ) -> None:
        """``-D`` is the short form of ``--no-diagnostics``."""
        result = runner.invoke(
            cli_app, ["analyze", "--project", "project", "-D"],
        )
        assert result.exit_code == 0
        assert "Diagnostic Signals" not in result.stdout


class TestHelpReflectsDefault:
    def test_help_mentions_default_on(
        self,
        runner: CliRunner,
        cli_app: typer.Typer,
    ) -> None:
        result = runner.invoke(cli_app, ["analyze", "--help"])
        assert result.exit_code == 0
        plain = _strip_ansi(result.stdout)
        assert "--no-diagnostics" in plain
        assert "default: on" in plain.lower()
