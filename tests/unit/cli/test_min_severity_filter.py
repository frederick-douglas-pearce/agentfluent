"""Tests for `agentfluent analyze --min-severity` (#205).

The flag filters ``aggregated_recommendations`` (default table surface)
and ``recommendations`` (per-invocation, ``--verbose`` surface). Signals
are not filtered — the user opted to suppress recommendations, not
observations.
"""

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
    def test_default_no_filter_passes_all_severities(
        self,
        runner: CliRunner,
        cli_app: typer.Typer,
        populated_home_with_traces: Path,
    ) -> None:
        payload = _run_json(runner, cli_app)
        diag = payload["data"]["diagnostics"]
        # Baseline: at least one recommendation surfaces in this fixture.
        assert diag["aggregated_recommendations"], (
            "fixture should produce diagnostics; flag baseline broken"
        )

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
        # Signals are observations, not recommendations — the filter is
        # documented as scoped to recommendations only.
        assert (
            baseline["data"]["diagnostics"]["signals"]
            == filtered["data"]["diagnostics"]["signals"]
        )

    def test_invalid_severity_value_rejected(
        self,
        runner: CliRunner,
        cli_app: typer.Typer,
        populated_home_with_traces: Path,
    ) -> None:
        result = runner.invoke(
            cli_app,
            [
                "analyze", "--project", "project",
                "--min-severity", "blocker",
            ],
        )
        assert result.exit_code != 0

    def test_case_insensitive_severity_value(
        self,
        runner: CliRunner,
        cli_app: typer.Typer,
        populated_home_with_traces: Path,
    ) -> None:
        # case_sensitive=False on the typer.Option enables this.
        result = runner.invoke(
            cli_app,
            [
                "analyze", "--project", "project",
                "--diagnostics", "--format", "json",
                "--min-severity", "WARNING",
            ],
        )
        assert result.exit_code == 0, result.stdout + result.stderr
