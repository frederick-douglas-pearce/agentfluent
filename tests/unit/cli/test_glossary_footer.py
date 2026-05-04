"""Verify #190: glossary footer renders on --diagnostics and config-check
output, but not on plain analyze, and never bleeds into JSON output.
"""

from __future__ import annotations

import json
from pathlib import Path

import typer
from typer.testing import CliRunner

GLOSSARY_FOOTNOTE = "See docs/GLOSSARY.md for term definitions."


class TestGlossaryFooterOnDiagnostics:
    def test_diagnostics_flag_renders_footer(
        self,
        runner: CliRunner,
        cli_app: typer.Typer,
        populated_home_with_traces: Path,
    ) -> None:
        result = runner.invoke(
            cli_app, ["analyze", "--project", "project", "--diagnostics"],
        )
        assert result.exit_code == 0
        assert GLOSSARY_FOOTNOTE in result.stdout

    def test_no_diagnostics_omits_footer(
        self,
        runner: CliRunner,
        cli_app: typer.Typer,
        populated_home_with_traces: Path,
    ) -> None:
        result = runner.invoke(
            cli_app,
            ["analyze", "--project", "project", "--no-diagnostics"],
        )
        assert result.exit_code == 0
        assert GLOSSARY_FOOTNOTE not in result.stdout

    def test_json_output_does_not_contain_footer_text(
        self,
        runner: CliRunner,
        cli_app: typer.Typer,
        populated_home_with_traces: Path,
    ) -> None:
        result = runner.invoke(
            cli_app,
            ["analyze", "--project", "project", "--format", "json"],
        )
        assert result.exit_code == 0
        # The footer is dim-styled stdout; JSON consumers must never see it.
        payload = json.loads(result.stdout)
        assert "GLOSSARY" not in json.dumps(payload)


class TestGlossaryFooterOnConfigCheck:
    def test_config_check_renders_footer(
        self,
        runner: CliRunner,
        cli_app: typer.Typer,
        populated_home: Path,
    ) -> None:
        result = runner.invoke(cli_app, ["config-check"])
        assert result.exit_code == 0
        assert GLOSSARY_FOOTNOTE in result.stdout

    def test_config_check_json_output_does_not_contain_footer_text(
        self,
        runner: CliRunner,
        cli_app: typer.Typer,
        populated_home: Path,
    ) -> None:
        result = runner.invoke(cli_app, ["config-check", "--format", "json"])
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert "GLOSSARY" not in json.dumps(payload)


class TestCostByModelTableShape:
    """#227 (Phase 2): Cost by Model table covers parent + subagent rows.

    Phase 1 (#188) renamed the table to "— Parent Session" and added a
    "subagent tokens are not broken out" footer note. Phase 2 closes
    the gap, so the title drops the qualifier and the footer goes away.
    """

    def test_table_title_drops_parent_session_qualifier(self) -> None:
        from io import StringIO

        from rich.console import Console

        from agentfluent.analytics.agent_metrics import AgentMetrics
        from agentfluent.analytics.pipeline import AnalysisResult
        from agentfluent.analytics.tokens import (
            ModelTokenBreakdown,
            TokenMetrics,
        )
        from agentfluent.analytics.tools import ToolMetrics
        from agentfluent.cli.formatters.table import format_analysis_table

        result = AnalysisResult(
            token_metrics=TokenMetrics(
                input_tokens=300,
                output_tokens=350,
                total_cost=0.75,
                by_model=[
                    ModelTokenBreakdown(
                        model="claude-opus-4-7", origin="parent",
                        input_tokens=100, output_tokens=200, cost=0.50,
                    ),
                    ModelTokenBreakdown(
                        model="claude-haiku-4-5-20251001", origin="subagent",
                        input_tokens=200, output_tokens=150, cost=0.25,
                    ),
                ],
            ),
            tool_metrics=ToolMetrics(),
            agent_metrics=AgentMetrics(),
            session_count=1,
        )

        buf = StringIO()
        format_analysis_table(
            Console(file=buf, width=160, force_terminal=False), result,
            verbose=True,
        )
        out = buf.getvalue()
        assert "Cost by Model" in out
        # Phase 1 qualifier and footer are gone now that subagents land
        # in the table directly.
        assert "Parent Session" not in out
        assert "Subagent tokens are not broken out" not in out
        # Origin column surfaces both rows.
        assert "parent" in out
        assert "subagent" in out
