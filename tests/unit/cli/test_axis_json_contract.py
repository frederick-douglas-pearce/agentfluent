"""JSON envelope axis attribution contract (#273).

Locks ``primary_axis`` and ``axis_scores`` on every aggregated
recommendation in ``analyze --json`` output. JSON consumers
(CI gates, dashboards, future v0.6 markdown report) depend on these
fields being present and shaped correctly.
"""

from __future__ import annotations

import json
from pathlib import Path

import typer
from typer.testing import CliRunner


class TestAnalyzeAxisJsonContract:
    """Each aggregated recommendation in the analyze envelope MUST
    expose ``primary_axis`` (str) and ``axis_scores`` (dict[str, float]
    keyed by ``cost``/``speed``/``quality``)."""

    def test_aggregated_recs_carry_primary_axis_and_axis_scores(
        self,
        runner: CliRunner,
        cli_app: typer.Typer,
        populated_home_with_traces: Path,
    ) -> None:
        result = runner.invoke(
            cli_app, ["analyze", "--project", "project", "--format", "json"],
        )
        assert result.exit_code == 0, result.stdout
        payload = json.loads(result.stdout)
        diagnostics = payload["data"].get("diagnostics")
        assert diagnostics is not None, "diagnostics block should be present"
        aggs = diagnostics.get("aggregated_recommendations") or []
        assert aggs, "expected at least one aggregated recommendation"

        for agg in aggs:
            assert "primary_axis" in agg, (
                f"primary_axis missing from aggregated row: {agg}"
            )
            assert isinstance(agg["primary_axis"], str)
            assert agg["primary_axis"] in {"cost", "speed", "quality"}

            assert "axis_scores" in agg, (
                f"axis_scores missing from aggregated row: {agg}"
            )
            scores = agg["axis_scores"]
            assert isinstance(scores, dict)
            assert set(scores.keys()) == {"cost", "speed", "quality"}
            for value in scores.values():
                # JSON numerics arrive as int OR float; both are valid.
                assert isinstance(value, (int, float))
