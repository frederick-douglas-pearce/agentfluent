"""End-to-end pipeline test: ``analyze --json`` -> ``report``.

Covers the composition contract: a snapshot produced by ``analyze --json``
on the same machine round-trips through ``report`` without losing fields
the renderers expect. Uses the existing ``populated_home`` /
``populated_home_with_traces`` fixtures (anonymized, deterministic) so
the test runs in CI rather than depending on real session data.

Per the project's CI policy (`tests/integration/` is reserved for tests
that need real ``~/.claude/projects/`` data), this lives under
``tests/unit/cli/`` and is not marked ``integration``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner


class TestAnalyzeReportPipeline:
    @pytest.mark.usefixtures("populated_home")
    def test_analyze_json_roundtrips_through_report(
        self,
        runner: CliRunner,
        cli_app: typer.Typer,
        tmp_path: Path,
    ) -> None:
        analyze_result = runner.invoke(
            cli_app,
            ["analyze", "--project", "project", "--no-diagnostics", "--json"],
        )
        assert analyze_result.exit_code == 0, analyze_result.output

        snap_file = tmp_path / "snap.json"
        snap_file.write_text(analyze_result.stdout)

        report_result = runner.invoke(cli_app, ["report", str(snap_file)])
        assert report_result.exit_code == 0, report_result.output

        out = report_result.stdout
        assert "# AgentFluent Report" in out
        idx_summary = out.index("## Summary")
        idx_tokens = out.index("## Token Metrics")
        idx_agents = out.index("## Agent Metrics")
        idx_diag = out.index("## Diagnostics")
        idx_footer = out.index("## Reproduction")
        assert idx_summary < idx_tokens < idx_agents < idx_diag < idx_footer
        assert "agentfluent analyze --project" in out

    @pytest.mark.usefixtures("populated_home_with_traces")
    def test_diagnostics_path_renders_through_pipeline(
        self,
        runner: CliRunner,
        cli_app: typer.Typer,
        tmp_path: Path,
    ) -> None:
        """The diagnostics-on path runs without crashing.

        Asserts only that the Diagnostics section is present -- the
        exact recommendations depend on upstream rule weights, which
        would make a deeper assertion brittle.
        """
        analyze_result = runner.invoke(
            cli_app, ["analyze", "--project", "project", "--json"],
        )
        assert analyze_result.exit_code == 0, analyze_result.output

        snap_file = tmp_path / "snap.json"
        snap_file.write_text(analyze_result.stdout)

        report_result = runner.invoke(cli_app, ["report", str(snap_file)])
        assert report_result.exit_code == 0, report_result.output

        out = report_result.stdout
        assert "## Diagnostics" in out
        assert "Traceback" not in out
