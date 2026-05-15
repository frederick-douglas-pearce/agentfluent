"""Golden-fixture snapshot test for ``agentfluent report``.

The fixture pair lives in ``tests/fixtures/report/``:

* ``analyze_snapshot.json`` -- a representative ``analyze --json`` envelope
  built from :func:`_analyze_envelope_data`.
* ``expected_report.md`` -- the rendered Markdown produced by
  :func:`agentfluent.cli.commands.report._render_analyze_report` against
  that data with ``now`` pinned to :data:`_FIXED_NOW`.

The snapshot test loads both files and asserts byte-for-byte equality.
The fixture pair doubles as a documentation example -- the rendered
Markdown is exactly what a developer sees when running ``report`` on a
real project.

To regenerate after an intentional format change:

    python -m tests.unit.cli.test_report_golden

The same data builder produces both files, so they cannot drift apart.

A separate test (:class:`TestV06EnvelopeBackwardCompat`) constructs an
analyze envelope shaped like a v0.6 snapshot -- no ``window``, no
``diagnostics_version``, no per-row ``origin`` on ``by_model`` -- to
prove ``report`` still produces a sensible document when fed an older
snapshot a user kept around.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from agentfluent.cli.commands.report import _render_analyze_report
from agentfluent.cli.formatters.json_output import format_json_output

FIXTURE_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "report"
ANALYZE_SNAPSHOT_PATH = FIXTURE_DIR / "analyze_snapshot.json"
EXPECTED_REPORT_PATH = FIXTURE_DIR / "expected_report.md"

_FIXED_NOW = datetime(2026, 5, 15, 14, 30, 0, tzinfo=UTC)


def _analyze_envelope_data() -> dict[str, Any]:
    """Representative analyze ``data`` payload exercising every renderer.

    Hand-built rather than captured from a real run so every renderer
    branch has coverage: parent + subagent rows on the same model, a
    builtin and a custom agent, one finding per severity, and a
    positive-savings offload candidate.
    """
    return {
        "project_name": "demo-agent-shop",
        "session_count": 5,
        "diagnostics_version": "0.7.0",
        "window": {
            "since": "2026-05-01T00:00:00+00:00",
            "until": "2026-05-08T00:00:00+00:00",
            "session_count_before_filter": 12,
            "session_count_after_filter": 5,
        },
        "token_metrics": {
            "input_tokens": 24_500,
            "output_tokens": 8_200,
            "cache_creation_input_tokens": 18_300,
            "cache_read_input_tokens": 412_000,
            "total_cost": 7.84,
            "cache_efficiency": 0.94,
            "by_model": [
                {
                    "model": "claude-opus-4-7",
                    "origin": "parent",
                    "input_tokens": 14_200,
                    "output_tokens": 5_100,
                    "cache_creation_input_tokens": 12_500,
                    "cache_read_input_tokens": 280_000,
                    "cost": 5.40,
                },
                {
                    "model": "claude-opus-4-7",
                    "origin": "subagent",
                    "input_tokens": 6_300,
                    "output_tokens": 2_100,
                    "cache_creation_input_tokens": 4_100,
                    "cache_read_input_tokens": 92_000,
                    "cost": 1.62,
                },
                {
                    "model": "claude-haiku-4-5",
                    "origin": "subagent",
                    "input_tokens": 4_000,
                    "output_tokens": 1_000,
                    "cache_creation_input_tokens": 1_700,
                    "cache_read_input_tokens": 40_000,
                    "cost": 0.82,
                },
            ],
        },
        "agent_metrics": {
            "total_invocations": 17,
            "agent_token_percentage": 38.5,
            "by_agent_type": {
                "Explore": {
                    "agent_type": "Explore",
                    "is_builtin": True,
                    "invocation_count": 8,
                    "total_tokens": 42_000,
                    "total_tool_uses": 56,
                    "total_duration_ms": 320_000,
                },
                "pm": {
                    "agent_type": "pm",
                    "is_builtin": False,
                    "invocation_count": 6,
                    "total_tokens": 78_000,
                    "total_tool_uses": 42,
                    "total_duration_ms": 510_000,
                },
                "tester": {
                    "agent_type": "tester",
                    "is_builtin": False,
                    "invocation_count": 3,
                    "total_tokens": 24_000,
                    "total_tool_uses": 18,
                    "total_duration_ms": 145_000,
                },
            },
        },
        "diagnostics": {
            "aggregated_recommendations": [
                {
                    "agent_type": "pm",
                    "target": "model",
                    "severity": "critical",
                    "signal_types": ["model_mismatch"],
                    "count": 4,
                    "representative_message": (
                        "Agent 'pm' runs Opus on tasks Sonnet handles cleanly; "
                        "swap the model field to claude-sonnet-4-6."
                    ),
                    "primary_axis": "cost",
                    "priority_score": 87.0,
                    "axis_scores": {"cost": 85.0, "speed": 0.0, "quality": 12.0},
                    "contributing_recommendations": [],
                },
                {
                    "agent_type": "tester",
                    "target": "tools",
                    "severity": "warning",
                    "signal_types": ["tool_error_sequence"],
                    "count": 3,
                    "representative_message": (
                        "Tester retries Bash 4-5 times before giving up; "
                        "tighten the description so it routes failing builds "
                        "back to the parent."
                    ),
                    "primary_axis": "speed",
                    "priority_score": 52.5,
                    "axis_scores": {"cost": 8.0, "speed": 50.0, "quality": 4.0},
                    "contributing_recommendations": [],
                },
                {
                    "agent_type": None,
                    "target": "mcp_servers",
                    "severity": "info",
                    "signal_types": ["mcp_unused"],
                    "count": 1,
                    "representative_message": (
                        "MCP server 'github' is configured but no agent "
                        "invoked it during the analyzed window."
                    ),
                    "primary_axis": "cost",
                    "priority_score": 12.0,
                    "axis_scores": {"cost": 10.0, "speed": 0.0, "quality": 0.0},
                    "contributing_recommendations": [],
                },
            ],
            "offload_candidates": [
                {
                    "name": "ts-bulk-edits",
                    "description": "Bulk TypeScript file edits with Read+Edit pattern.",
                    "confidence": "high",
                    "cluster_size": 14,
                    "cohesion_score": 0.88,
                    "top_terms": ["edit", "typescript", "rename"],
                    "tool_sequence_summary": [],
                    "tools": ["Read", "Edit", "Grep"],
                    "tools_note": "",
                    "estimated_parent_tokens": 95_000,
                    "estimated_parent_cost_usd": 4.20,
                    "estimated_savings_usd": 2.85,
                    "parent_model": "claude-opus-4-7",
                    "alternative_model": "claude-sonnet-4-6",
                    "cost_note": "",
                    "target_kind": "subagent",
                    "subagent_draft": None,
                    "skill_draft": None,
                    "matched_agent": "",
                    "dedup_note": "",
                    "yaml_draft": "",
                },
            ],
        },
    }


class TestSnapshot:
    def test_rendered_report_matches_golden_fixture(self) -> None:
        envelope_text = ANALYZE_SNAPSHOT_PATH.read_text(encoding="utf-8")
        envelope = json.loads(envelope_text)
        rendered = _render_analyze_report(envelope["data"], now=_FIXED_NOW)

        expected = EXPECTED_REPORT_PATH.read_text(encoding="utf-8")
        assert rendered == expected, (
            "Rendered report drifted from the golden fixture. If this is "
            "intentional, regenerate via: "
            "python -m tests.unit.cli.test_report_golden"
        )

    def test_committed_envelope_matches_data_builder(self) -> None:
        """Guard against the JSON fixture diverging from the builder."""
        on_disk = json.loads(ANALYZE_SNAPSHOT_PATH.read_text(encoding="utf-8"))
        rebuilt = json.loads(format_json_output("analyze", _analyze_envelope_data()))
        assert on_disk == rebuilt, (
            "analyze_snapshot.json drifted from _analyze_envelope_data(); "
            "regenerate via: python -m tests.unit.cli.test_report_golden"
        )


class TestV06EnvelopeBackwardCompat:
    """Older snapshots predate v0.7's ``window``, ``diagnostics_version``,
    and per-row ``origin`` columns. ``report`` must degrade gracefully
    rather than KeyError on missing fields."""

    @pytest.fixture()
    def v06_data(self) -> dict[str, Any]:
        return {
            "project_name": "legacy-project",
            "session_count": 3,
            "token_metrics": {
                "input_tokens": 5000,
                "output_tokens": 1500,
                "cache_creation_input_tokens": 2000,
                "cache_read_input_tokens": 18000,
                "total_cost": 0.42,
                "cache_efficiency": 0.78,
                "by_model": [
                    {
                        "model": "claude-opus-4-6",
                        "input_tokens": 5000,
                        "output_tokens": 1500,
                        "cache_creation_input_tokens": 2000,
                        "cache_read_input_tokens": 18000,
                        "cost": 0.42,
                    },
                ],
            },
            "agent_metrics": {
                "by_agent_type": {},
                "total_invocations": 0,
                "agent_token_percentage": 0.0,
            },
        }

    def test_renders_without_window_metadata(self, v06_data: dict[str, Any]) -> None:
        out = _render_analyze_report(v06_data, now=_FIXED_NOW)
        assert "## Summary" in out
        assert "Window:** all sessions" in out

    def test_omits_version_line_when_unstamped(self, v06_data: dict[str, Any]) -> None:
        out = _render_analyze_report(v06_data, now=_FIXED_NOW)
        assert "AgentFluent version" not in out

    def test_token_table_renders_with_blank_origin(
        self, v06_data: dict[str, Any],
    ) -> None:
        out = _render_analyze_report(v06_data, now=_FIXED_NOW)
        assert "## Token Metrics" in out
        assert "claude-opus-4-6" in out

    def test_no_agent_invocations_emits_fallback(
        self, v06_data: dict[str, Any],
    ) -> None:
        out = _render_analyze_report(v06_data, now=_FIXED_NOW)
        assert "No agent invocations." in out

    def test_no_diagnostics_emits_no_findings(
        self, v06_data: dict[str, Any],
    ) -> None:
        out = _render_analyze_report(v06_data, now=_FIXED_NOW)
        assert "No findings." in out


def _regenerate_golden() -> None:
    """Write both fixture files from the canonical data builder.

    Invoke directly when the report format changes intentionally:

        python -m tests.unit.cli.test_report_golden
    """
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    data = _analyze_envelope_data()
    envelope_text = format_json_output("analyze", data)
    if not envelope_text.endswith("\n"):
        envelope_text += "\n"
    ANALYZE_SNAPSHOT_PATH.write_text(envelope_text, encoding="utf-8")

    rendered = _render_analyze_report(data, now=_FIXED_NOW)
    EXPECTED_REPORT_PATH.write_text(rendered, encoding="utf-8")
    print(f"Wrote {ANALYZE_SNAPSHOT_PATH}")
    print(f"Wrote {EXPECTED_REPORT_PATH}")


if __name__ == "__main__":
    _regenerate_golden()
