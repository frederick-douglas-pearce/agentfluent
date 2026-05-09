"""End-to-end tests for ``agentfluent report`` via Typer's CliRunner.

Covers the #353 acceptance surface: valid envelope ingestion (stdout +
``--output``), all four error paths (missing file, malformed JSON,
wrong envelope, top-level non-object JSON), and ``--help`` exposing
the workflow examples.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import typer
from typer.testing import CliRunner

from agentfluent.cli.formatters.json_output import format_json_output


def _analyze_data() -> dict[str, Any]:
    """Minimal valid analyze ``data`` payload.

    Intentionally sparse: the skeleton renderers don't read these
    fields, so we only need enough structure that the envelope passes
    the version/command/data check. Section bodies that consume these
    fields are #354's responsibility.
    """
    return {
        "session_count": 1,
        "token_metrics": {
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "total_cost": 0.0,
            "cache_efficiency": 0.0,
            "by_model": [],
        },
        "agent_metrics": {"by_agent_type": {}, "total_invocations": 0},
    }


@pytest.fixture()
def analyze_snapshot(tmp_path: Path) -> Path:
    path = tmp_path / "snap.json"
    path.write_text(format_json_output("analyze", _analyze_data()))
    return path


class TestRender:
    def test_renders_to_stdout_with_d030_section_order(
        self,
        runner: CliRunner,
        cli_app: typer.Typer,
        analyze_snapshot: Path,
    ) -> None:
        result = runner.invoke(cli_app, ["report", str(analyze_snapshot)])
        assert result.exit_code == 0, result.output
        out = result.stdout
        assert "# AgentFluent Report" in out
        # D030: Summary -> Token Metrics -> Agent Metrics -> Diagnostics ->
        # Offload. Verify ordering by index, not just presence.
        idx_summary = out.index("## Summary")
        idx_tokens = out.index("## Token Metrics")
        idx_agents = out.index("## Agent Metrics")
        idx_diag = out.index("## Diagnostics")
        idx_offload = out.index("## Offload Candidates")
        assert idx_summary < idx_tokens < idx_agents < idx_diag < idx_offload

    def test_output_flag_writes_file_and_no_stdout(
        self,
        runner: CliRunner,
        cli_app: typer.Typer,
        analyze_snapshot: Path,
        tmp_path: Path,
    ) -> None:
        out_file = tmp_path / "report.md"
        result = runner.invoke(
            cli_app,
            ["report", str(analyze_snapshot), "--output", str(out_file)],
        )
        assert result.exit_code == 0, result.output
        assert out_file.exists()
        contents = out_file.read_text()
        assert "# AgentFluent Report" in contents
        assert "## Summary" in contents
        assert result.stdout == ""

    def test_help_shows_examples(
        self,
        runner: CliRunner,
        cli_app: typer.Typer,
    ) -> None:
        result = runner.invoke(cli_app, ["report", "--help"])
        assert result.exit_code == 0
        assert "Examples:" in result.output
        assert "agentfluent analyze" in result.output


class TestEnvelopeErrors:
    def test_missing_file_surfaces_user_error(
        self,
        runner: CliRunner,
        cli_app: typer.Typer,
        tmp_path: Path,
    ) -> None:
        result = runner.invoke(cli_app, ["report", str(tmp_path / "missing.json")])
        assert result.exit_code == 1
        assert "File not found" in result.output

    def test_malformed_json_surfaces_user_error(
        self,
        runner: CliRunner,
        cli_app: typer.Typer,
        tmp_path: Path,
    ) -> None:
        bad = tmp_path / "bad.json"
        bad.write_text("not valid json {")
        result = runner.invoke(cli_app, ["report", str(bad)])
        assert result.exit_code == 1
        assert "Invalid JSON" in result.output

    def test_top_level_non_object_surfaces_user_error(
        self,
        runner: CliRunner,
        cli_app: typer.Typer,
        tmp_path: Path,
    ) -> None:
        # `42` parses fine but isn't a versioned envelope.
        bad = tmp_path / "scalar.json"
        bad.write_text("42")
        result = runner.invoke(cli_app, ["report", str(bad)])
        assert result.exit_code == 1
        assert "not an object" in result.output

    def test_diff_envelope_rejected_with_helpful_message(
        self,
        runner: CliRunner,
        cli_app: typer.Typer,
        tmp_path: Path,
    ) -> None:
        # A well-formed `diff` envelope should be rejected by `report`
        # in v0.7 (the analyze renderer is the only one registered);
        # adding a diff renderer is the v0.8 follow-up flagged in
        # prd-v0.7.md OQ3.
        diff_envelope = tmp_path / "diff.json"
        diff_envelope.write_text(format_json_output("diff", {"new_count": 0}))
        result = runner.invoke(cli_app, ["report", str(diff_envelope)])
        assert result.exit_code == 1
        assert "does not support" in result.output
        assert "'diff'" in result.output
        assert "analyze" in result.output

    def test_envelope_missing_required_keys(
        self,
        runner: CliRunner,
        cli_app: typer.Typer,
        tmp_path: Path,
    ) -> None:
        # JSON object without the version/command/data envelope keys.
        bad = tmp_path / "no_envelope.json"
        bad.write_text('{"foo": "bar"}')
        result = runner.invoke(cli_app, ["report", str(bad)])
        assert result.exit_code == 1
        assert "missing keys" in result.output
