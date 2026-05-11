"""Unit tests for the ``report`` Markdown section renderers (#354).

Each renderer is a pure function from a JSON-deserialized analyze
``data`` dict to a Markdown string. Tests pass dicts directly — no
Typer CliRunner needed; dispatch coverage lives in ``test_report_cmd``.
"""

from __future__ import annotations

from typing import Any

from agentfluent.cli.commands.report_renderers import (
    UNKNOWN_PROJECT,
    render_agent_metrics,
    render_diagnostics,
    render_footer,
    render_offload,
    render_summary,
    render_token_metrics,
)


def _data(**overrides: Any) -> dict[str, Any]:
    """Build a baseline ``data`` payload; ``overrides`` replaces top-level keys."""
    base: dict[str, Any] = {
        "project_name": "demo-project",
        "session_count": 3,
        "diagnostics_version": "0.7.0",
        "token_metrics": {
            "input_tokens": 1000,
            "output_tokens": 500,
            "cache_creation_input_tokens": 200,
            "cache_read_input_tokens": 300,
            "total_cost": 1.2345,
            "cache_efficiency": 60.0,
            "by_model": [
                {
                    "model": "claude-opus-4-7",
                    "input_tokens": 800,
                    "output_tokens": 400,
                    "cache_creation_input_tokens": 100,
                    "cache_read_input_tokens": 200,
                    "cost": 1.0,
                    "origin": "parent",
                },
                {
                    "model": "claude-opus-4-7",
                    "input_tokens": 200,
                    "output_tokens": 100,
                    "cache_creation_input_tokens": 100,
                    "cache_read_input_tokens": 100,
                    "cost": 0.2345,
                    "origin": "subagent",
                },
            ],
        },
        "agent_metrics": {
            "by_agent_type": {
                "pm": {
                    "agent_type": "pm",
                    "is_builtin": False,
                    "invocation_count": 4,
                    "total_tokens": 12000,
                    "total_tool_uses": 20,
                    "total_duration_ms": 60_000,
                },
                "explore": {
                    "agent_type": "Explore",
                    "is_builtin": True,
                    "invocation_count": 2,
                    "total_tokens": 5000,
                    "total_tool_uses": 8,
                    "total_duration_ms": 15_000,
                },
            },
            "total_invocations": 6,
            "agent_token_percentage": 42.5,
        },
        "window": None,
        "diagnostics": None,
    }
    base.update(overrides)
    return base


# ---- render_summary -------------------------------------------------------


class TestRenderSummary:
    def test_includes_project_session_cost_tokens_version(self) -> None:
        out = render_summary(_data())
        assert "## Summary" in out
        assert "demo-project" in out
        assert "Sessions analyzed:** 3" in out
        assert "$1.23" in out
        assert "2,000" in out  # total tokens = 1000+500+200+300
        assert "0.7.0" in out

    def test_window_null_renders_all_sessions(self) -> None:
        out = render_summary(_data(window=None))
        assert "Window:** all sessions" in out

    def test_window_populated_renders_range_and_counts(self) -> None:
        out = render_summary(
            _data(
                window={
                    "since": "2026-05-01T00:00:00Z",
                    "until": "2026-05-08T00:00:00Z",
                    "session_count_before_filter": 10,
                    "session_count_after_filter": 3,
                },
            ),
        )
        assert "2026-05-01T00:00:00Z" in out
        assert "2026-05-08T00:00:00Z" in out
        assert "3 of 10 sessions" in out

    def test_missing_project_name_falls_back_to_unknown(self) -> None:
        # Legacy envelope (pre-#354) won't carry ``project_name``.
        d = _data()
        d.pop("project_name")
        out = render_summary(d)
        assert UNKNOWN_PROJECT in out

    def test_omits_version_line_when_unstamped(self) -> None:
        out = render_summary(_data(diagnostics_version=None))
        assert "AgentFluent version" not in out


# ---- render_token_metrics -------------------------------------------------


class TestRenderTokenMetrics:
    def test_renders_table_with_totals_row(self) -> None:
        out = render_token_metrics(_data())
        assert "## Token Metrics" in out
        # GitHub-flavored Markdown table separator.
        assert "| Model" in out
        assert "---:" in out
        # Both (model, origin) rows present.
        assert "parent" in out
        assert "subagent" in out
        # Totals row uses bolded fields.
        assert "**Total**" in out
        assert "**$1.23**" in out

    def test_parent_origin_sorts_first(self) -> None:
        out = render_token_metrics(_data())
        parent_idx = out.index("parent")
        subagent_idx = out.index("subagent")
        assert parent_idx < subagent_idx

    def test_empty_by_model_emits_no_usage_note(self) -> None:
        d = _data()
        d["token_metrics"]["by_model"] = []
        out = render_token_metrics(d)
        assert "No token usage recorded." in out
        assert "| Model" not in out


# ---- render_agent_metrics -------------------------------------------------


class TestRenderAgentMetrics:
    def test_renders_per_type_rows_with_builtin_annotation(self) -> None:
        out = render_agent_metrics(_data())
        assert "## Agent Metrics" in out
        assert "pm" in out
        assert "Explore (builtin)" in out
        assert "12,000" in out
        assert "60.0s" in out
        # Footer line with agent token share.
        assert "42.5%" in out

    def test_zero_invocations_emits_fallback(self) -> None:
        out = render_agent_metrics(
            _data(agent_metrics={"by_agent_type": {}, "total_invocations": 0}),
        )
        assert "No agent invocations." in out
        assert "| Agent Type" not in out


# ---- render_diagnostics ---------------------------------------------------


def _agg(
    *,
    severity: str = "warning",
    target: str = "model",
    agent: str | None = "pm",
    count: int = 1,
    axis: str = "cost",
    msg: str = "Use a cheaper model for this pattern.",
    priority: float = 10.0,
) -> dict[str, Any]:
    return {
        "agent_type": agent,
        "target": target,
        "severity": severity,
        "signal_types": [],
        "count": count,
        "representative_message": msg,
        "primary_axis": axis,
        "priority_score": priority,
        "axis_scores": {"cost": 0.0, "speed": 0.0, "quality": 0.0},
        "contributing_recommendations": [],
    }


class TestRenderDiagnostics:
    def test_empty_recommendations_emits_no_findings(self) -> None:
        out = render_diagnostics(_data(diagnostics={"aggregated_recommendations": []}))
        assert "## Diagnostics" in out
        assert "No findings." in out

    def test_diagnostics_none_treated_as_no_findings(self) -> None:
        # ``analyze --no-diagnostics`` leaves ``diagnostics`` as ``None``;
        # the renderer must not crash on the missing key.
        out = render_diagnostics(_data(diagnostics=None))
        assert "No findings." in out

    def test_severity_groups_in_critical_warning_info_order(self) -> None:
        out = render_diagnostics(
            _data(
                diagnostics={
                    "aggregated_recommendations": [
                        _agg(severity="info", priority=1.0, target="prompt"),
                        _agg(severity="critical", priority=99.0, target="model"),
                        _agg(severity="warning", priority=50.0, target="tools"),
                    ],
                },
            ),
        )
        idx_crit = out.index("### Critical")
        idx_warn = out.index("### Warning")
        idx_info = out.index("### Info")
        assert idx_crit < idx_warn < idx_info

    def test_axis_labels_present_on_every_row(self) -> None:
        out = render_diagnostics(
            _data(
                diagnostics={
                    "aggregated_recommendations": [
                        _agg(axis="cost", target="t1"),
                        _agg(axis="speed", target="t2"),
                        _agg(axis="quality", target="t3"),
                    ],
                },
            ),
        )
        assert r"\[cost]" in out
        assert r"\[speed]" in out
        assert r"\[quality]" in out

    def test_top_n_summary_caps_at_five(self) -> None:
        recs = [
            _agg(target=f"t{i}", priority=100.0 - i, severity="warning")
            for i in range(7)
        ]
        out = render_diagnostics(
            _data(diagnostics={"aggregated_recommendations": recs}),
        )
        assert "Top 5 priority fixes" in out

    def test_global_agent_label_for_null_agent(self) -> None:
        out = render_diagnostics(
            _data(
                diagnostics={
                    "aggregated_recommendations": [
                        _agg(agent=None, target="mcp_servers"),
                    ],
                },
            ),
        )
        assert "(global)" in out


# ---- render_offload ------------------------------------------------------


def _offload(
    *,
    name: str = "ts-bulk-edits",
    savings: float = 1.50,
    confidence: str = "high",
    cluster_size: int = 12,
    tools: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "description": "...",
        "confidence": confidence,
        "cluster_size": cluster_size,
        "cohesion_score": 0.85,
        "top_terms": [],
        "tool_sequence_summary": [],
        "tools": tools or ["Edit", "Read"],
        "tools_note": "",
        "estimated_parent_tokens": 50_000,
        "estimated_parent_cost_usd": 5.0,
        "estimated_savings_usd": savings,
        "parent_model": "claude-opus-4-7",
        "alternative_model": "claude-sonnet-4-6",
        "cost_note": "",
        "target_kind": "subagent",
        "subagent_draft": None,
        "skill_draft": None,
        "matched_agent": "",
        "dedup_note": "",
        "yaml_draft": "",
    }


class TestRenderOffload:
    def test_renders_positive_savings_only(self) -> None:
        out = render_offload(
            _data(
                diagnostics={
                    "aggregated_recommendations": [],
                    "offload_candidates": [
                        _offload(name="keep", savings=2.0),
                        _offload(name="drop", savings=-1.5),
                    ],
                },
            ),
        )
        assert "## Offload Candidates" in out
        assert "keep" in out
        assert "drop" not in out

    def test_section_absent_when_all_savings_nonpositive(self) -> None:
        out = render_offload(
            _data(
                diagnostics={
                    "aggregated_recommendations": [],
                    "offload_candidates": [
                        _offload(name="zero", savings=0.0),
                        _offload(name="negative", savings=-1.0),
                    ],
                },
            ),
        )
        assert out == ""

    def test_no_offload_key_returns_empty(self) -> None:
        out = render_offload(_data(diagnostics={"aggregated_recommendations": []}))
        assert out == ""

    def test_diagnostics_none_returns_empty(self) -> None:
        assert render_offload(_data(diagnostics=None)) == ""

    def test_sorted_by_savings_descending(self) -> None:
        out = render_offload(
            _data(
                diagnostics={
                    "aggregated_recommendations": [],
                    "offload_candidates": [
                        _offload(name="small", savings=0.10),
                        _offload(name="big", savings=5.00),
                        _offload(name="mid", savings=1.00),
                    ],
                },
            ),
        )
        idx_big = out.index("big")
        idx_mid = out.index("mid")
        idx_small = out.index("small")
        assert idx_big < idx_mid < idx_small


# ---- render_footer -------------------------------------------------------


class TestRenderFooter:
    def test_reproduction_command_includes_project_and_json_flag(self) -> None:
        out = render_footer(_data())
        assert "## Reproduction" in out
        assert "agentfluent analyze --project demo-project --json" in out
        assert "```bash" in out
        assert "*Generated:" in out

    def test_window_flags_appear_when_present(self) -> None:
        out = render_footer(
            _data(
                window={
                    "since": "2026-05-01T00:00:00Z",
                    "until": "2026-05-08T00:00:00Z",
                    "session_count_before_filter": 10,
                    "session_count_after_filter": 3,
                },
            ),
        )
        assert "--since 2026-05-01T00:00:00Z" in out
        assert "--until 2026-05-08T00:00:00Z" in out

    def test_no_window_omits_since_until(self) -> None:
        out = render_footer(_data(window=None))
        assert "--since" not in out
        assert "--until" not in out

    def test_missing_project_name_uses_unknown_placeholder(self) -> None:
        d = _data()
        d.pop("project_name")
        out = render_footer(d)
        # The unknown-project sentinel contains parens/space, so it
        # should be quoted so the command is copy-paste-runnable.
        assert UNKNOWN_PROJECT in out
        assert '"(unknown project)"' in out

    def test_project_with_spaces_is_quoted(self) -> None:
        out = render_footer(_data(project_name="my project"))
        assert '"my project"' in out
