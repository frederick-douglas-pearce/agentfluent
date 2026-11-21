"""Cost-coverage disclosure in ``diff`` output (#713): table caveat, quiet line, JSON."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest
import typer
from rich.console import Console
from typer.testing import CliRunner

from agentfluent.analytics.cost_coverage import (
    CostCoverage,
    DiagnosticsCoverage,
    InvocationCoverage,
    PricingCoverage,
    has_cost_gap,
)
from agentfluent.analytics.cost_model import CostModelStamp
from agentfluent.cli.formatters.coverage import cost_gap_clauses
from agentfluent.cli.formatters.diff_cost_caveat import cost_caveat_reasons
from agentfluent.cli.formatters.diff_table import _render_token_metrics
from agentfluent.cli.formatters.json_output import format_json_output
from agentfluent.diff.models import DiffResult

_STAMP = CostModelStamp(genai_prices_version="0.2.3", cost_method_version=1)


def _coverage(
    *, unpriced: dict[str, int] | None = None, linked: int = 4, priced: int = 4,
) -> CostCoverage:
    unpriced = unpriced or {}
    spend = linked
    return CostCoverage(
        pricing=PricingCoverage(
            tokens=1_000_000,
            unpriced_tokens=sum(unpriced.values()),
            unpriced_models=unpriced,
        ),
        invocations=InvocationCoverage(total=4, linked=linked, spend=spend, priced=priced),
    )


def _result(
    baseline: CostCoverage | None,
    current: CostCoverage | None,
    *,
    baseline_unparseable: int | None = 0,
    current_unparseable: int | None = 0,
) -> DiffResult:
    """Matching stamps so only #713's coverage reasons speak."""
    return DiffResult(
        baseline_cost_model=_STAMP,
        current_cost_model=_STAMP,
        baseline_cost_coverage=baseline,
        current_cost_coverage=current,
        baseline_unparseable_trace_files=baseline_unparseable if baseline else None,
        current_unparseable_trace_files=current_unparseable if current else None,
    )


def _render(result: DiffResult) -> str:
    buf = io.StringIO()
    console = Console(file=buf, width=400, force_terminal=False, no_color=True)
    _render_token_metrics(console, result)
    return buf.getvalue()


class TestReasons:
    """AC1 + AC3: a note whenever either side is not full; none when both are."""

    def test_both_full_is_silent(self) -> None:
        assert cost_caveat_reasons(_result(_coverage(), _coverage())) == []
        assert "Cost deltas below" not in _render(_result(_coverage(), _coverage()))

    def test_one_side_unpriced_names_the_side_and_model(self) -> None:
        reasons = cost_caveat_reasons(
            _result(_coverage(), _coverage(unpriced={"claude-new-1": 40_000})),
        )
        assert len(reasons) == 1
        assert reasons[0].startswith("current cost coverage is incomplete")
        assert "not agent behavior" in reasons[0]
        assert "claude-new-1" in reasons[0]

    def test_one_side_legacy_reads_unknown(self) -> None:
        reasons = cost_caveat_reasons(_result(None, _coverage()))
        assert len(reasons) == 1
        assert reasons[0].startswith("cost coverage unknown: the baseline snapshot")
        assert "before v0.12.0" in reasons[0]

    def test_full_block_without_trace_count_reads_unknown(self) -> None:
        result = DiffResult(
            baseline_cost_model=_STAMP,
            current_cost_model=_STAMP,
            baseline_cost_coverage=_coverage(),
            current_cost_coverage=_coverage(),
            baseline_unparseable_trace_files=None,
            current_unparseable_trace_files=0,
        )
        reasons = cost_caveat_reasons(result)
        assert reasons == [reasons[0]]
        assert "the baseline snapshot has no readable cost-coverage record" in reasons[0]

    def test_both_partial_gives_one_reason_per_side(self) -> None:
        reasons = cost_caveat_reasons(
            _result(
                _coverage(linked=3, priced=3),
                _coverage(priced=2),
                current_unparseable=1,
            ),
        )
        assert len(reasons) == 2
        assert reasons[0].startswith("baseline cost coverage is incomplete")
        assert "1 with no linked trace" in reasons[0]
        assert reasons[1].startswith("current cost coverage is incomplete")
        assert "2 on an unpriced model" in reasons[1]
        assert "1 trace file could not be parsed" in reasons[1]

    def test_reasons_follow_the_cost_model_reason(self) -> None:
        result = _result(None, _coverage())
        result.baseline_cost_model = None
        reasons = cost_caveat_reasons(result)
        assert reasons[0].startswith("cost model unknown")
        assert reasons[1].startswith("cost coverage unknown")

    def test_rendered_above_the_cost_tables(self) -> None:
        out = _render(_result(None, _coverage()))
        assert out.index("Cost deltas below may not reflect agent behavior") < out.index(
            "Token Metrics",
        )
        assert "cost coverage unknown: the baseline snapshot" in out

    def test_markup_in_a_model_id_renders_literally(self) -> None:
        """Model ids come from user-supplied envelope files (C2)."""
        out = _render(_result(_coverage(), _coverage(unpriced={"[red]x[/red]": 5})))
        assert "[red]x[/red]" in out


class TestSharedClauses:
    """The diff clauses and the shared gap check cannot disagree (C3)."""

    @pytest.mark.parametrize(
        ("coverage", "unparseable"),
        [
            (_coverage(), 0),
            (_coverage(unpriced={"m": 1}), 0),
            (_coverage(linked=3, priced=3), 0),
            (_coverage(priced=3), 0),
            (_coverage(), 2),
            (
                CostCoverage(
                    pricing=PricingCoverage(tokens=10),
                    invocations=InvocationCoverage(total=2, linked=2, spend=1, priced=1),
                    diagnostics=DiagnosticsCoverage(partial_figures=1, unpriced_figures=1),
                ),
                0,
            ),
        ],
    )
    def test_clauses_non_empty_iff_gap(self, coverage: CostCoverage, unparseable: int) -> None:
        clauses = cost_gap_clauses(coverage.model_dump(), unparseable)
        assert bool(clauses) is has_cost_gap(coverage, unparseable)
        assert not any(clause.startswith("Diagnostics:") for clause in clauses)


def _envelope_data(*, total_cost: float, coverage: dict[str, Any] | None) -> dict[str, Any]:
    data: dict[str, Any] = {
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
        "diagnostics": {"aggregated_recommendations": []},
        "cost_model": _STAMP.model_dump(mode="json"),
    }
    if coverage is not None:
        data["cost_coverage"] = coverage
        data["trace_coverage"] = {"unparseable": {"files": 0}}
    return data


def _paths(
    tmp_path: Path, baseline: dict[str, Any] | None, current: dict[str, Any] | None,
) -> tuple[str, str]:
    out = []
    for name, cost, coverage in (
        ("baseline", 40.0, baseline), ("current", 5.0, current),
    ):
        path = tmp_path / f"{name}.json"
        path.write_text(
            format_json_output("analyze", _envelope_data(total_cost=cost, coverage=coverage)),
        )
        out.append(str(path))
    return out[0], out[1]


_FULL = _coverage().model_dump(mode="json")
_UNPRICED = _coverage(unpriced={"claude-new-1": 900_000}).model_dump(mode="json")


class TestCli:
    """AC2: outside the table, the cost delta is never bare when coverage is not full."""

    @pytest.mark.parametrize(
        ("baseline", "current", "note"),
        [
            (_FULL, _FULL, None),
            (_FULL, _UNPRICED, " (cost coverage incomplete)"),
            (None, _FULL, " (cost coverage unknown)"),
        ],
    )
    def test_quiet_line(
        self, runner: CliRunner, cli_app: typer.Typer, tmp_path: Path,
        baseline: Any, current: Any, note: str | None,
    ) -> None:
        a, b = _paths(tmp_path, baseline, current)
        result = runner.invoke(cli_app, ["diff", a, b, "--quiet", "--fail-on", "off"])
        assert result.exit_code == 0, result.output
        lines = result.output.strip().splitlines()
        assert len(lines) == 1  # one line, never wrapped
        if note is None:
            assert "(" not in lines[0]
        else:
            assert lines[0].endswith("cost -$35.0000" + note)

    def test_quiet_line_combines_both_notes(
        self, runner: CliRunner, cli_app: typer.Typer, tmp_path: Path,
    ) -> None:
        a, b = _paths(tmp_path, None, _FULL)
        data = json.loads(Path(a).read_text())
        del data["data"]["cost_model"]
        Path(a).write_text(json.dumps(data))
        result = runner.invoke(cli_app, ["diff", a, b, "--quiet", "--fail-on", "off"])
        assert "(cost model unknown; cost coverage unknown)" in result.output

    @pytest.mark.parametrize(
        ("baseline", "current", "expected"),
        [(_FULL, _FULL, "full"), (_FULL, _UNPRICED, "incomplete"), (None, _FULL, "unknown")],
    )
    def test_quiet_json_status(
        self, runner: CliRunner, cli_app: typer.Typer, tmp_path: Path,
        baseline: Any, current: Any, expected: str,
    ) -> None:
        a, b = _paths(tmp_path, baseline, current)
        result = runner.invoke(
            cli_app, ["diff", a, b, "--quiet", "--json", "--fail-on", "off"],
        )
        assert json.loads(result.output)["data"]["cost_coverage_status"] == expected

    @pytest.mark.parametrize("baseline", [None, _UNPRICED])
    def test_default_fail_on_exit_code_ignores_coverage(
        self, runner: CliRunner, cli_app: typer.Typer, tmp_path: Path, baseline: Any,
    ) -> None:
        a, b = _paths(tmp_path, baseline, _FULL)
        result = runner.invoke(cli_app, ["diff", a, b, "--json"])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)["data"]
        assert payload["cost_coverage_status"] != "full"
        assert payload["regression_detected"] is False

    def test_table_shows_coverage_caveat(
        self, runner: CliRunner, cli_app: typer.Typer, tmp_path: Path,
    ) -> None:
        a, b = _paths(tmp_path, _FULL, _UNPRICED)
        result = runner.invoke(cli_app, ["diff", a, b, "--fail-on", "off"])
        assert result.exit_code == 0, result.output
        assert "Cost deltas below may not reflect agent behavior" in result.output
        assert "current cost coverage is incomplete" in result.output
