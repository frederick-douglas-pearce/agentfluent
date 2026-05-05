"""Tests for `agentfluent analyze --min-severity` (#205)."""

from __future__ import annotations

import json
from pathlib import Path

import typer
from typer.testing import CliRunner


def _run_json(
    runner: CliRunner,
    cli_app: typer.Typer,
    *extra: str,
) -> dict:
    result = runner.invoke(
        cli_app,
        [
            "analyze",
            "--project", "project",
            "--diagnostics",
            "--format", "json",
            *extra,
        ],
    )
    assert result.exit_code == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


class TestMinSeverityFilter:
    def test_min_severity_critical_drops_info_and_warning(
        self,
        runner: CliRunner,
        cli_app: typer.Typer,
        populated_home_with_traces: Path,
    ) -> None:
        payload = _run_json(
            runner, cli_app, "--min-severity", "critical",
        )
        diag = payload["data"]["diagnostics"]
        assert all(
            r["severity"] == "critical"
            for r in diag["aggregated_recommendations"]
        )
        assert all(
            r["severity"] == "critical" for r in diag["recommendations"]
        )

    def test_min_severity_warning_keeps_warning_and_critical(
        self,
        runner: CliRunner,
        cli_app: typer.Typer,
        populated_home_with_traces: Path,
    ) -> None:
        payload = _run_json(
            runner, cli_app, "--min-severity", "warning",
        )
        diag = payload["data"]["diagnostics"]
        for r in diag["aggregated_recommendations"]:
            assert r["severity"] in {"warning", "critical"}
        for r in diag["recommendations"]:
            assert r["severity"] in {"warning", "critical"}

    def test_min_severity_does_not_affect_signals(
        self,
        runner: CliRunner,
        cli_app: typer.Typer,
        populated_home_with_traces: Path,
    ) -> None:
        baseline = _run_json(runner, cli_app)
        filtered = _run_json(
            runner, cli_app, "--min-severity", "critical",
        )
        assert (
            baseline["data"]["diagnostics"]["signals"]
            == filtered["data"]["diagnostics"]["signals"]
        )

    def test_case_insensitive_severity_value(
        self,
        runner: CliRunner,
        cli_app: typer.Typer,
        populated_home_with_traces: Path,
    ) -> None:
        result = runner.invoke(
            cli_app,
            [
                "analyze", "--project", "project",
                "--diagnostics", "--format", "json",
                "--min-severity", "WARNING",
            ],
        )
        assert result.exit_code == 0, result.stdout + result.stderr
