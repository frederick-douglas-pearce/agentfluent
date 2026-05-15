"""End-to-end pipeline test: ``analyze --json`` -> ``report`` (#355).

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

import typer
from typer.testing import CliRunner


class TestAnalyzeReportPipeline:
    def test_analyze_json_roundtrips_through_report(
        self,
        runner: CliRunner,
        cli_app: typer.Typer,
        populated_home: Path,  # noqa: ARG002 — patches discovery paths
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
        # Every always-emitted section header is present, in the D030 order.
        assert "# AgentFluent Report" in out
        idx_summary = out.index("## Summary")
        idx_tokens = out.index("## Token Metrics")
        idx_agents = out.index("## Agent Metrics")
        idx_diag = out.index("## Diagnostics")
        idx_footer = out.index("## Reproduction")
        assert idx_summary < idx_tokens < idx_agents < idx_diag < idx_footer

        # The reproduction footer should echo a runnable analyze command.
        assert "agentfluent analyze --project" in out

    def test_diagnostics_path_renders_through_pipeline(
        self,
        runner: CliRunner,
        cli_app: typer.Typer,
        populated_home_with_traces: Path,  # noqa: ARG002
        tmp_path: Path,
    ) -> None:
        """The diagnostics-on path produces a populated Diagnostics section.

        Uses the trace-carrying fixture so the diagnostics pipeline has
        signal evidence to aggregate. Asserts only that the section
        renders without crashing -- the exact recommendations depend on
        upstream rule weights, which would make this test brittle.
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
