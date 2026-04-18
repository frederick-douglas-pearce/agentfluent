"""Cost-label UI tests (#76): verify API-rate labeling and footnote render.

The JSON output schema still uses `total_cost` -- it is an API contract and
must NOT change. Only the human-readable table labels get the "(API rate)"
qualifier plus an explanatory footnote.
"""

from __future__ import annotations

from pathlib import Path

import typer
from typer.testing import CliRunner


class TestCostLabeling:
    """AC: table output labels cost as API-rate and prints the footnote."""

    def test_total_cost_row_renamed(
        self,
        runner: CliRunner,
        cli_app: typer.Typer,
        populated_home: Path,
    ) -> None:
        result = runner.invoke(cli_app, ["analyze", "--project", "project"])
        assert result.exit_code == 0
        assert "Total cost (API rate)" in result.stdout

    def test_footnote_rendered(
        self,
        runner: CliRunner,
        cli_app: typer.Typer,
        populated_home: Path,
    ) -> None:
        result = runner.invoke(cli_app, ["analyze", "--project", "project"])
        assert result.exit_code == 0
        assert "API rate" in result.stdout
        assert "pay-per-token equivalent" in result.stdout
        assert "Subscription plans" in result.stdout

    def test_footnote_printed_once(
        self,
        runner: CliRunner,
        cli_app: typer.Typer,
        populated_home: Path,
    ) -> None:
        result = runner.invoke(
            cli_app,
            ["analyze", "--project", "project", "--verbose"],
        )
        assert result.exit_code == 0
        # The footnote sentinel "pay-per-token equivalent" appears exactly once
        # even in verbose mode where multiple cost-bearing tables render.
        assert result.stdout.count("pay-per-token equivalent") == 1


class TestJsonSchemaUnchanged:
    """Guardrail: JSON output key stays `total_cost` -- API contract."""

    def test_json_quiet_still_uses_total_cost_key(
        self,
        runner: CliRunner,
        cli_app: typer.Typer,
        populated_home: Path,
    ) -> None:
        result = runner.invoke(
            cli_app,
            ["analyze", "--project", "project", "--format", "json", "--quiet"],
        )
        assert result.exit_code == 0
        assert '"total_cost"' in result.stdout
        # The UI-layer label should not leak into JSON output.
        assert "API rate" not in result.stdout
