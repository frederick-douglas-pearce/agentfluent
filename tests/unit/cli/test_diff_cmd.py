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


def _agent_data(
    by_agent: dict[str, dict[str, Any]], *, with_spend: bool,
) -> dict[str, Any]:
    data = _data()
    data["agent_metrics"]["by_agent_type"] = by_agent
    if with_spend:
        data["agent_metrics"]["total_agent_spend_tokens"] = sum(
            e.get("spend_tokens", 0) for e in by_agent.values()
        )
    return data


def _gp_entry(*, rollup: int, cost: float, spend: int | None) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "invocation_count": 2,
        "total_tokens": rollup,
        "estimated_total_cost_usd": cost,
    }
    if spend is not None:
        entry["spend_tokens"] = spend
        entry["spend_invocation_count"] = 2
    return entry


def _gp_row(section: str) -> str:
    rows = [line for line in section.splitlines() if " gp " in line]
    assert len(rows) == 1, section
    return rows[0]


def _cells(row: str) -> list[str]:
    return [cell.strip() for cell in row.split("│")[1:-1]]


def _agent_section(output: str) -> str:
    assert "Per-Agent Deltas" in output, output
    return output[output.index("Per-Agent Deltas"):]


class TestPerAgentTokensDeltaFromSpend:
    """#693 -- the Per-Agent Deltas table's Tokens Δ renders trace spend,
    never the ``toolUseResult.totalTokens`` rollup proxy."""

    def _output(
        self,
        runner: CliRunner,
        cli_app: typer.Typer,
        tmp_path: Path,
        baseline: dict[str, Any],
        current: dict[str, Any],
        *extra_args: str,
    ) -> str:
        b = _write_envelope(tmp_path / "baseline.json", baseline)
        c = _write_envelope(tmp_path / "current.json", current)
        result = runner.invoke(
            cli_app, ["diff", str(b), str(c), "--fail-on", "off", *extra_args],
        )
        assert result.exit_code == 0, result.output
        return result.output

    def _run(
        self,
        runner: CliRunner,
        cli_app: typer.Typer,
        tmp_path: Path,
        baseline: dict[str, Any],
        current: dict[str, Any],
    ) -> str:
        return _agent_section(self._output(runner, cli_app, tmp_path, baseline, current))

    def test_renders_spend_delta_not_rollup_delta(
        self, runner: CliRunner, cli_app: typer.Typer, tmp_path: Path,
    ) -> None:
        section = self._run(
            runner, cli_app, tmp_path,
            _agent_data({"gp": _gp_entry(rollup=1_000, cost=1.0, spend=2_000)}, with_spend=True),
            _agent_data(
                {"gp": _gp_entry(rollup=124_456, cost=13.0, spend=9_502_000)}, with_spend=True,
            ),
        )
        assert "+9,500,000" in section
        assert "123,456" not in section

    def test_rollup_zero_still_shows_spend_movement(
        self, runner: CliRunner, cli_app: typer.Typer, tmp_path: Path,
    ) -> None:
        # The bug shape: rollup absent (0) on both sides beside a real cost Δ.
        section = self._run(
            runner, cli_app, tmp_path,
            _agent_data({"gp": _gp_entry(rollup=0, cost=1.0, spend=2_000)}, with_spend=True),
            _agent_data({"gp": _gp_entry(rollup=0, cost=13.0, spend=9_502_000)}, with_spend=True),
        )
        row = _gp_row(section)
        assert "+9,500,000" in row
        assert "—" not in row

    def test_one_side_legacy_renders_dash(
        self, runner: CliRunner, cli_app: typer.Typer, tmp_path: Path,
    ) -> None:
        section = self._run(
            runner, cli_app, tmp_path,
            _agent_data({"gp": _gp_entry(rollup=1_000, cost=1.0, spend=None)}, with_spend=False),
            _agent_data(
                {"gp": _gp_entry(rollup=124_456, cost=13.0, spend=9_502_000)}, with_spend=True,
            ),
        )
        assert "—" in section
        assert "123,456" not in section
        assert "9,502,000" not in section

    def test_both_sides_legacy_render_dash_in_every_row(
        self, runner: CliRunner, cli_app: typer.Typer, tmp_path: Path,
    ) -> None:
        # Pre-#693 this row showed the rollup delta (+123,456); there is no
        # rollup fallback by design.
        section = self._run(
            runner, cli_app, tmp_path,
            _agent_data(
                {
                    "gp": _gp_entry(rollup=1_000, cost=1.0, spend=None),
                    "pm": _gp_entry(rollup=500, cost=0.5, spend=None),
                },
                with_spend=False,
            ),
            _agent_data(
                {
                    "gp": _gp_entry(rollup=124_456, cost=13.0, spend=None),
                    "pm": _gp_entry(rollup=900, cost=0.9, spend=None),
                },
                with_spend=False,
            ),
        )
        assert "123,456" not in section
        assert "+400" not in section
        rows = [line for line in section.splitlines() if " gp " in line or " pm " in line]
        assert len(rows) == 2, section
        assert all("—" in row for row in rows), section

    def test_measured_zero_spend_delta_renders_zero_not_dash(
        self, runner: CliRunner, cli_app: typer.Typer, tmp_path: Path,
    ) -> None:
        # Spend unchanged, cost moved (keeps the row): a measured 0, not unknown.
        section = self._run(
            runner, cli_app, tmp_path,
            _agent_data({"gp": _gp_entry(rollup=0, cost=1.0, spend=5_000)}, with_spend=True),
            _agent_data({"gp": _gp_entry(rollup=0, cost=3.0, spend=5_000)}, with_spend=True),
        )
        row = _gp_row(section)
        assert "—" not in row
        assert _cells(row)[2] == "0", row

    def test_rollup_only_change_row_hidden_unless_verbose(
        self, runner: CliRunner, cli_app: typer.Typer, tmp_path: Path,
    ) -> None:
        baseline = _agent_data(
            {"gp": _gp_entry(rollup=300, cost=1.0, spend=5_000)}, with_spend=True,
        )
        current = _agent_data(
            {"gp": _gp_entry(rollup=700, cost=1.0, spend=5_000)}, with_spend=True,
        )
        quiet = self._output(runner, cli_app, tmp_path, baseline, current)
        assert "Per-Agent Deltas" not in quiet
        verbose = self._output(runner, cli_app, tmp_path, baseline, current, "--verbose")
        assert _cells(_gp_row(_agent_section(verbose)))[2] == "0"


class TestCostModelStampOutput:
    """#543: quiet outputs carry the cost-model status, not a bare cost delta."""

    _STAMP = {"genai_prices_version": "0.2.3", "cost_method_version": 1}

    def _paths(
        self, tmp_path: Path, baseline_stamp: Any, current_stamp: Any,
    ) -> tuple[Path, Path]:
        baseline = _data(total_cost=1.0)
        current = _data(total_cost=2.0)
        # Full cost coverage on both sides (#713), so these cases isolate the stamp.
        for side in (baseline, current):
            side["cost_coverage"] = {
                "pricing": {"tokens": 150, "unpriced_tokens": 0},
                "invocations": {"total": 0, "linked": 0, "spend": 0, "priced": 0},
            }
            side["trace_coverage"] = {"unparseable": {"files": 0}}
        if baseline_stamp is not None:
            baseline["cost_model"] = baseline_stamp
        if current_stamp is not None:
            current["cost_model"] = current_stamp
        return (
            _write_envelope(tmp_path / "baseline.json", baseline),
            _write_envelope(tmp_path / "current.json", current),
        )

    def _quiet(
        self, runner: CliRunner, cli_app: typer.Typer, paths: tuple[Path, Path],
        *extra: str,
    ) -> str:
        result = runner.invoke(
            cli_app,
            ["diff", str(paths[0]), str(paths[1]), "--quiet", "--fail-on", "off", *extra],
        )
        assert result.exit_code == 0, result.output
        return result.output

    @pytest.mark.parametrize(
        ("baseline_stamp", "expected"),
        [
            ({"genai_prices_version": "0.2.3", "cost_method_version": 1}, "match"),
            ({"genai_prices_version": "0.1.4", "cost_method_version": 1}, "mismatch"),
            (None, "unknown"),
        ],
    )
    def test_quiet_json_carries_status(
        self, runner: CliRunner, cli_app: typer.Typer, tmp_path: Path,
        baseline_stamp: Any, expected: str,
    ) -> None:
        paths = self._paths(tmp_path, baseline_stamp, self._STAMP)
        payload = json.loads(self._quiet(runner, cli_app, paths, "--json"))
        assert payload["data"]["cost_model_status"] == expected

    def test_quiet_text_marks_mismatch(
        self, runner: CliRunner, cli_app: typer.Typer, tmp_path: Path,
    ) -> None:
        paths = self._paths(
            tmp_path, {"genai_prices_version": "0.1.4", "cost_method_version": 1},
            self._STAMP,
        )
        assert "(cost model differs)" in self._quiet(runner, cli_app, paths)

    def test_quiet_text_marks_unknown(
        self, runner: CliRunner, cli_app: typer.Typer, tmp_path: Path,
    ) -> None:
        paths = self._paths(tmp_path, None, self._STAMP)
        assert "(cost model unknown)" in self._quiet(runner, cli_app, paths)

    def test_quiet_text_unmarked_on_match(
        self, runner: CliRunner, cli_app: typer.Typer, tmp_path: Path,
    ) -> None:
        paths = self._paths(tmp_path, self._STAMP, self._STAMP)
        output = self._quiet(runner, cli_app, paths)
        assert "cost model" not in output
        assert "(" not in output

    @pytest.mark.parametrize(
        "baseline_stamp",
        [{"genai_prices_version": "0.2.3", "cost_method_version": 2}, None],
    )
    def test_default_fail_on_exit_code_ignores_cost_model(
        self, runner: CliRunner, cli_app: typer.Typer, tmp_path: Path,
        baseline_stamp: Any,
    ) -> None:
        """Under the default ``--fail-on warning``, a cost-model mismatch or an unknown
        cost model with no new recommendation exits 0."""
        paths = self._paths(tmp_path, baseline_stamp, self._STAMP)
        result = runner.invoke(cli_app, ["diff", str(paths[0]), str(paths[1]), "--json"])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["data"]["cost_model_status"] != "match"
        assert payload["data"]["regression_detected"] is False

    def test_table_output_shows_caveat(
        self, runner: CliRunner, cli_app: typer.Typer, tmp_path: Path,
    ) -> None:
        paths = self._paths(tmp_path, None, self._STAMP)
        result = runner.invoke(
            cli_app, ["diff", str(paths[0]), str(paths[1]), "--fail-on", "off"],
        )
        assert result.exit_code == 0, result.output
        assert "Cost deltas below may not reflect agent behavior" in result.output
