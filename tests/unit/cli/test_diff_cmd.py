"""End-to-end tests for ``agentfluent diff`` via Typer's CliRunner.

Uses small synthetic envelope JSON files to exercise: arg validation,
envelope load errors (missing file / malformed / quiet envelope /
schema mismatch), table + JSON output, and the regression exit-code
semantics that CI consumers depend on.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import typer
from typer.testing import CliRunner

from agentfluent.cli.formatters.json_output import format_json_output


def _write_envelope(path: Path, data: dict[str, Any]) -> Path:
    path.write_text(format_json_output("analyze", data))
    return path


def _data(
    *,
    aggregated_recs: list[dict[str, Any]] | None = None,
    total_cost: float = 0.0,
) -> dict[str, Any]:
    return {
        "session_count": 1,
        "token_metrics": {
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "total_cost": total_cost,
            "cache_efficiency": 0.0,
            "by_model": {},
        },
        "agent_metrics": {"by_agent_type": {}, "total_invocations": 0},
        "diagnostics": {
            "aggregated_recommendations": aggregated_recs or [],
        },
    }


def _rec(*, severity: str = "warning", agent_type: str = "pm") -> dict[str, Any]:
    return {
        "agent_type": agent_type,
        "target": "prompt",
        "signal_types": ["retry_loop"],
        "severity": severity,
        "count": 1,
        "priority_score": 10.0,
        "representative_message": "Retry loop detected.",
        "is_builtin": False,
    }


@pytest.fixture()
def baseline_path(tmp_path: Path) -> Path:
    return _write_envelope(tmp_path / "baseline.json", _data())


@pytest.fixture()
def current_with_new_warning(tmp_path: Path) -> Path:
    return _write_envelope(
        tmp_path / "current.json",
        _data(aggregated_recs=[_rec(severity="warning")]),
    )


@pytest.fixture()
def current_with_new_info(tmp_path: Path) -> Path:
    return _write_envelope(
        tmp_path / "current.json",
        _data(aggregated_recs=[_rec(severity="info")]),
    )


class TestExitCodes:
    def test_no_diff_no_regression_exits_zero(
        self,
        runner: CliRunner,
        cli_app: typer.Typer,
        baseline_path: Path,
        tmp_path: Path,
    ) -> None:
        identical = _write_envelope(tmp_path / "current.json", _data())
        result = runner.invoke(cli_app, ["diff", str(baseline_path), str(identical)])
        assert result.exit_code == 0, result.output

    def test_regression_at_threshold_exits_three(
        self,
        runner: CliRunner,
        cli_app: typer.Typer,
        baseline_path: Path,
        current_with_new_warning: Path,
    ) -> None:
        result = runner.invoke(
            cli_app,
            [
                "diff", str(baseline_path), str(current_with_new_warning),
                "--fail-on", "warning",
            ],
        )
        assert result.exit_code == 3, result.output
        assert "Regression detected" in result.output

    def test_new_below_threshold_exits_zero(
        self,
        runner: CliRunner,
        cli_app: typer.Typer,
        baseline_path: Path,
        current_with_new_info: Path,
    ) -> None:
        result = runner.invoke(
            cli_app,
            [
                "diff", str(baseline_path), str(current_with_new_info),
                "--fail-on", "warning",
            ],
        )
        assert result.exit_code == 0, result.output

    def test_fail_on_off_disables_regression_check(
        self,
        runner: CliRunner,
        cli_app: typer.Typer,
        baseline_path: Path,
        current_with_new_warning: Path,
    ) -> None:
        result = runner.invoke(
            cli_app,
            [
                "diff", str(baseline_path), str(current_with_new_warning),
                "--fail-on", "off",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "Regression detected" not in result.output

    def test_invalid_fail_on_value_is_user_error(
        self,
        runner: CliRunner,
        cli_app: typer.Typer,
        baseline_path: Path,
        current_with_new_warning: Path,
    ) -> None:
        result = runner.invoke(
            cli_app,
            [
                "diff", str(baseline_path), str(current_with_new_warning),
                "--fail-on", "boom",
            ],
        )
        assert result.exit_code != 0
        # typer.BadParameter exits 2 by Click convention.
        assert result.exit_code == 2


class TestEnvelopeErrors:
    def test_missing_file_surfaces_user_error(
        self,
        runner: CliRunner,
        cli_app: typer.Typer,
        tmp_path: Path,
    ) -> None:
        good = _write_envelope(tmp_path / "good.json", _data())
        result = runner.invoke(
            cli_app, ["diff", str(tmp_path / "missing.json"), str(good)],
        )
        assert result.exit_code == 1
        assert "File not found" in result.output

    def test_malformed_json_surfaces_user_error(
        self,
        runner: CliRunner,
        cli_app: typer.Typer,
        tmp_path: Path,
    ) -> None:
        bad = tmp_path / "bad.json"
        bad.write_text("{not json")
        good = _write_envelope(tmp_path / "good.json", _data())
        result = runner.invoke(cli_app, ["diff", str(bad), str(good)])
        assert result.exit_code == 1
        assert "Invalid JSON" in result.output

    def test_wrong_command_in_envelope_surfaces_user_error(
        self,
        runner: CliRunner,
        cli_app: typer.Typer,
        tmp_path: Path,
    ) -> None:
        wrong = tmp_path / "wrong.json"
        wrong.write_text(json.dumps({"version": "1", "command": "list-projects", "data": {}}))
        good = _write_envelope(tmp_path / "good.json", _data())
        result = runner.invoke(cli_app, ["diff", str(wrong), str(good)])
        assert result.exit_code == 1
        assert "command" in result.output.lower()

    def test_quiet_envelope_rejected_with_explanation(
        self,
        runner: CliRunner,
        cli_app: typer.Typer,
        tmp_path: Path,
    ) -> None:
        # `analyze --json --quiet` payload omits token_metrics + agent_metrics.
        quiet_payload = {
            "project": "x", "session_count": 1, "total_cost": 0.0,
            "total_tokens": 0, "total_invocations": 0,
            "diagnostic_signal_count": 0,
        }
        quiet_path = _write_envelope(tmp_path / "quiet.json", quiet_payload)
        good = _write_envelope(tmp_path / "good.json", _data())
        result = runner.invoke(cli_app, ["diff", str(quiet_path), str(good)])
        assert result.exit_code == 1
        assert "without --quiet" in result.output


class TestOutputFormats:
    def test_json_output_contains_diff_envelope(
        self,
        runner: CliRunner,
        cli_app: typer.Typer,
        baseline_path: Path,
        current_with_new_warning: Path,
    ) -> None:
        result = runner.invoke(
            cli_app,
            [
                "diff", str(baseline_path), str(current_with_new_warning),
                "--json", "--fail-on", "off",
            ],
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["command"] == "diff"
        assert payload["data"]["new_count"] == 1
        assert payload["data"]["regression_detected"] is False

    def test_json_output_carries_regression_flag(
        self,
        runner: CliRunner,
        cli_app: typer.Typer,
        baseline_path: Path,
        current_with_new_warning: Path,
    ) -> None:
        result = runner.invoke(
            cli_app,
            [
                "diff", str(baseline_path), str(current_with_new_warning),
                "--json",  # default --fail-on warning
            ],
        )
        assert result.exit_code == 3
        payload = json.loads(result.output)
        assert payload["data"]["regression_detected"] is True
        assert payload["data"]["fail_on"] == "warning"

    def test_quiet_one_line_summary(
        self,
        runner: CliRunner,
        cli_app: typer.Typer,
        baseline_path: Path,
        current_with_new_warning: Path,
    ) -> None:
        result = runner.invoke(
            cli_app,
            [
                "diff", str(baseline_path), str(current_with_new_warning),
                "--quiet", "--fail-on", "off",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "1 new" in result.output

    def test_table_output_includes_section_headers(
        self,
        runner: CliRunner,
        cli_app: typer.Typer,
        baseline_path: Path,
        current_with_new_warning: Path,
    ) -> None:
        result = runner.invoke(
            cli_app,
            [
                "diff", str(baseline_path), str(current_with_new_warning),
                "--fail-on", "off",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "New Recommendations" in result.output
        assert "Token Metrics" in result.output

    def test_json_carries_window_and_version_fields(
        self,
        runner: CliRunner,
        cli_app: typer.Typer,
        tmp_path: Path,
    ) -> None:
        """#342/#347: ``diff --json`` surfaces baseline/current window
        and diagnostics_version at the top of ``data`` so CI consumers
        can branch on detector-version drift without re-running analyze."""
        window_v1 = {
            "since": "2026-04-25T00:00:00+00:00",
            "until": "2026-05-03T00:00:00+00:00",
            "session_count_before_filter": 12,
            "session_count_after_filter": 6,
        }
        window_v2 = {
            **window_v1,
            "since": "2026-05-03T00:00:00+00:00",
            "until": "2026-05-09T00:00:00+00:00",
            "session_count_after_filter": 11,
        }
        baseline = _write_envelope(
            tmp_path / "baseline.json",
            _data() | {"window": window_v1, "diagnostics_version": "0.6.1"},
        )
        current = _write_envelope(
            tmp_path / "current.json",
            _data() | {"window": window_v2, "diagnostics_version": "0.7.0"},
        )

        result = runner.invoke(
            cli_app,
            ["diff", str(baseline), str(current), "--json", "--fail-on", "off"],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)["data"]

        assert data["baseline_window"]["session_count_after_filter"] == 6
        assert data["current_window"]["session_count_after_filter"] == 11
        assert data["baseline_diagnostics_version"] == "0.6.1"
        assert data["current_diagnostics_version"] == "0.7.0"
        assert data["diagnostics_version_drift"] is True

    def test_help_includes_examples(
        self,
        runner: CliRunner,
        cli_app: typer.Typer,
    ) -> None:
        result = runner.invoke(cli_app, ["diff", "--help"])
        assert result.exit_code == 0
        assert "Examples" in result.output


def _rec_with_axis(
    *,
    severity: str = "warning",
    agent_type: str = "pm",
    primary_axis: str = "cost",
) -> dict[str, Any]:
    """Helper to build an aggregated rec with a primary_axis (#273)."""
    return {
        **_rec(severity=severity, agent_type=agent_type),
        "primary_axis": primary_axis,
        "axis_scores": {"cost": 0.0, "speed": 0.0, "quality": 0.0},
    }


class TestAxisLabelsInDiffOutput:
    """The diff table prefixes each delta's message with its axis label
    and renders a ``[old → new]`` shift indicator on persisting rows
    where the primary axis changed (#273)."""

    def test_new_rec_renders_axis_prefix(
        self,
        runner: CliRunner,
        cli_app: typer.Typer,
        tmp_path: Path,
    ) -> None:
        baseline = _write_envelope(tmp_path / "baseline.json", _data())
        current = _write_envelope(
            tmp_path / "current.json",
            _data(aggregated_recs=[
                _rec_with_axis(primary_axis="quality"),
            ]),
        )
        result = runner.invoke(
            cli_app, ["diff", str(baseline), str(current), "--fail-on", "off"],
        )
        assert result.exit_code == 0, result.output
        assert "[quality]" in result.output

    def test_persisting_axis_shift_renders_arrow(
        self,
        runner: CliRunner,
        cli_app: typer.Typer,
        tmp_path: Path,
    ) -> None:
        baseline = _write_envelope(
            tmp_path / "baseline.json",
            _data(aggregated_recs=[_rec_with_axis(primary_axis="cost")]),
        )
        current = _write_envelope(
            tmp_path / "current.json",
            _data(aggregated_recs=[_rec_with_axis(primary_axis="quality")]),
        )
        result = runner.invoke(
            cli_app, ["diff", str(baseline), str(current), "--fail-on", "off"],
        )
        assert result.exit_code == 0, result.output
        # Same agent + target + signal_types → persisting; different
        # primary_axis → shift indicator with both axis names present.
        assert "Persisting Recommendations" in result.output
        # The shift indicator carries both axes joined by an arrow; we
        # check both pieces independently to stay resilient to whitespace.
        assert "cost" in result.output
        assert "quality" in result.output
        assert "→" in result.output

    def test_persisting_no_shift_renders_single_axis(
        self,
        runner: CliRunner,
        cli_app: typer.Typer,
        tmp_path: Path,
    ) -> None:
        baseline = _write_envelope(
            tmp_path / "baseline.json",
            _data(aggregated_recs=[_rec_with_axis(primary_axis="speed")]),
        )
        current = _write_envelope(
            tmp_path / "current.json",
            _data(aggregated_recs=[_rec_with_axis(primary_axis="speed")]),
        )
        result = runner.invoke(
            cli_app, ["diff", str(baseline), str(current), "--fail-on", "off"],
        )
        assert result.exit_code == 0, result.output
        assert "[speed]" in result.output
        # No shift → no arrow indicator inside the recommendations
        # block. (The summary line has its own ``Sessions: N → N``
        # arrow we explicitly skip past.)
        rec_section_start = result.output.index("Persisting Recommendations")
        rec_section_end = (
            result.output.index("Token Metrics")
            if "Token Metrics" in result.output
            else len(result.output)
        )
        rec_section = result.output[rec_section_start:rec_section_end]
        assert "→" not in rec_section
