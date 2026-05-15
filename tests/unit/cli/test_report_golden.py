"""Golden Markdown coverage for ``agentfluent report`` (#355)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agentfluent.cli.commands.report import _render_analyze_report
from agentfluent.cli.formatters.json_output import format_json_output, parse_json_output

FIXED_NOW = datetime(2026, 5, 15, 12, 30, tzinfo=UTC)


def _analysis_data() -> dict[str, Any]:
    return {
        "project_name": "golden demo",
        "session_count": 2,
        "diagnostics_version": "0.7.0",
        "window": {
            "since": "2026-05-01T00:00:00Z",
            "until": "2026-05-08T00:00:00Z",
            "session_count_before_filter": 5,
            "session_count_after_filter": 2,
        },
        "token_metrics": {
            "input_tokens": 1200,
            "output_tokens": 450,
            "cache_creation_input_tokens": 300,
            "cache_read_input_tokens": 600,
            "total_cost": 1.25,
            "cache_efficiency": 66.7,
            "by_model": [
                {
                    "model": "claude-sonnet-4-6",
                    "origin": "parent",
                    "input_tokens": 800,
                    "output_tokens": 300,
                    "cache_creation_input_tokens": 200,
                    "cache_read_input_tokens": 400,
                    "cost": 0.90,
                },
                {
                    "model": "claude-sonnet-4-6",
                    "origin": "subagent",
                    "input_tokens": 400,
                    "output_tokens": 150,
                    "cache_creation_input_tokens": 100,
                    "cache_read_input_tokens": 200,
                    "cost": 0.35,
                },
            ],
        },
        "agent_metrics": {
            "by_agent_type": {
                "reviewer": {
                    "agent_type": "reviewer",
                    "is_builtin": False,
                    "invocation_count": 2,
                    "total_tokens": 4000,
                    "total_tool_uses": 6,
                    "total_duration_ms": 25_000,
                }
            },
            "total_invocations": 2,
            "agent_token_percentage": 35.5,
        },
        "diagnostics": {
            "aggregated_recommendations": [
                {
                    "agent_type": "reviewer",
                    "target": "allowed_tools",
                    "severity": "warning",
                    "signal_types": ["tool_error"],
                    "count": 2,
                    "representative_message": "Allow the reviewer to read changed files before commenting.",
                    "primary_axis": "quality",
                    "priority_score": 8.5,
                    "axis_scores": {"cost": 0.0, "speed": 0.0, "quality": 8.5},
                    "contributing_recommendations": [],
                }
            ],
            "offload_candidates": [
                {
                    "name": "review-sweeps",
                    "description": "Repeated review passes",
                    "confidence": "high",
                    "cluster_size": 3,
                    "cohesion_score": 0.9,
                    "top_terms": ["review"],
                    "tool_sequence_summary": [],
                    "tools": ["Read", "Grep"],
                    "tools_note": "",
                    "estimated_parent_tokens": 20_000,
                    "estimated_parent_cost_usd": 2.00,
                    "estimated_savings_usd": 0.75,
                    "parent_model": "claude-sonnet-4-6",
                    "alternative_model": "claude-haiku-4-5",
                    "cost_note": "",
                    "target_kind": "subagent",
                    "subagent_draft": None,
                    "skill_draft": None,
                    "matched_agent": "",
                    "dedup_note": "",
                    "yaml_draft": "",
                }
            ],
        },
    }


def _render_golden(data: dict[str, Any]) -> str:
    report = _render_analyze_report(data, now=FIXED_NOW)
    return report if report.endswith("\n") else report + "\n"


def test_report_matches_golden_markdown_fixture() -> None:
    envelope = format_json_output("analyze", _analysis_data())
    data = parse_json_output(envelope, expected_command="analyze")

    fixture = Path(__file__).parents[2] / "fixtures" / "report_analyze_golden.md"
    assert _render_golden(data) == fixture.read_text(encoding="utf-8")


def test_report_handles_v06_analyze_json_without_v07_fields() -> None:
    legacy = _analysis_data()
    legacy.pop("window")
    legacy.pop("diagnostics_version")
    legacy["token_metrics"]["by_model"] = [
        {k: v for k, v in row.items() if k != "origin"}
        for row in legacy["token_metrics"]["by_model"]
    ]

    out = _render_golden(legacy)

    assert "Window:** all sessions" in out
    assert "AgentFluent version" not in out
    assert "## Token Metrics" in out
    assert "## Reproduction" in out


if __name__ == "__main__":
    fixture = Path(__file__).parents[2] / "fixtures" / "report_analyze_golden.md"
    fixture.parent.mkdir(parents=True, exist_ok=True)
    fixture.write_text(_render_golden(_analysis_data()), encoding="utf-8")
