"""CLI smoke tests for trace-based diagnostics output.

Complements the unit-level coverage in ``test_diagnostics_pipeline.py``
by exercising the full CLI → ``run_diagnostics`` → formatter chain
against a fixture project that carries linked subagent traces.
Runs in CI (not skipped), no dependency on real ``~/.claude/projects/``
data.

See issue #138 for why this lives alongside #109's unit-level
integration tests instead of replacing them: unit tests guard the
function-level contracts, smoke tests guard the end-to-end rendering
pipeline.
"""

from __future__ import annotations

import json
from pathlib import Path

import typer
from typer.testing import CliRunner


class TestDiagnosticsCLISmoke:
    """Runs `agentfluent analyze --project project --diagnostics` against
    a project with linked subagent traces. The fixture session has two
    Agent invocations (pm + Explore) whose agentIds link to subagent
    trace files exhibiting retry and stuck-pattern behavior."""

    def test_exits_zero_and_shows_signal_table(
        self,
        runner: CliRunner,
        cli_app: typer.Typer,
        populated_home_with_traces: Path,
    ) -> None:
        result = runner.invoke(
            cli_app, ["analyze", "--project", "project", "--diagnostics"],
        )
        assert result.exit_code == 0
        # The Diagnostic Signals table always renders above the deep-
        # diagnostics summary when any signal exists.
        assert "Diagnostic Signals" in result.stdout

    def test_trace_signals_surface_on_diagnostics_flag(
        self,
        runner: CliRunner,
        cli_app: typer.Typer,
        populated_home_with_traces: Path,
    ) -> None:
        result = runner.invoke(
            cli_app, ["analyze", "--project", "project", "--diagnostics"],
        )
        assert result.exit_code == 0
        # Either retry_loop (from agent-retry.jsonl) or stuck_pattern
        # (from agent-stuck.jsonl) should appear. Check both types of
        # evidence leaked through.
        assert "retry_loop" in result.stdout or "stuck_pattern" in result.stdout

    def test_verbose_renders_deep_diagnostics_section(
        self,
        runner: CliRunner,
        cli_app: typer.Typer,
        populated_home_with_traces: Path,
    ) -> None:
        result = runner.invoke(
            cli_app,
            ["analyze", "--project", "project", "--diagnostics", "--verbose"],
        )
        assert result.exit_code == 0
        assert "Deep Diagnostics" in result.stdout

    def test_json_output_includes_trace_signal_detail(
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
                "--diagnostics",
                "--format", "json",
            ],
        )
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        signals = payload["data"]["diagnostics"]["signals"]
        trace_types = {"retry_loop", "stuck_pattern", "tool_error_sequence",
                       "permission_failure"}
        trace_signals = [s for s in signals if s["signal_type"] in trace_types]
        assert trace_signals, "expected at least one trace-level signal"
        # Evidence is the contract #108 promised and #113 depends on.
        assert "tool_calls" in trace_signals[0]["detail"]

    def test_traceless_project_still_works(
        self,
        runner: CliRunner,
        cli_app: typer.Typer,
        populated_home: Path,
    ) -> None:
        """Regression guard: a project without subagent traces (the
        pre-#108 shape) must not crash the diagnostics path."""
        result = runner.invoke(
            cli_app, ["analyze", "--project", "project", "--diagnostics"],
        )
        assert result.exit_code == 0
        # No trace signals, so no Deep Diagnostics section.
        assert "Deep Diagnostics" not in result.stdout
