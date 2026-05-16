"""``--session`` scope: diagnostics + metrics constrain to one session.

Per D032, ``analyze --session SID --diagnostics`` reports only on the
named session — token/cost metrics, agent invocations, diagnostic
signals, and aggregated recommendations all reflect the single session,
not the whole project. This is a v0.6 → v0.7 semantics change (see
CHANGELOG entry tracked in #360).

The CLI's session-path filter at ``commands/analyze.py:318`` already
constrains the pipeline correctly, but no test pinned the behavior.
These tests lock it in via a multi-session fixture whose two sessions
delegate to distinct agent_types so cross-contamination would show up
as the wrong key appearing in the JSON envelope.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import typer
from typer.testing import CliRunner

from tests._builders import (
    assistant_with_tool_use,
    user_with_tool_result,
    write_project_layout,
)


def _agent_invocation_pair(
    tool_use_id: str,
    *,
    subagent_type: str,
    agent_id: str,
    timestamp_use: str,
    timestamp_result: str,
    message_id: str,
) -> list[dict[str, Any]]:
    """Two-message pair: assistant Agent tool_use + user tool_result.

    Returns the messages in order; concatenate pairs to build a session.
    """
    return [
        assistant_with_tool_use(
            tool_use_id,
            name="Agent",
            inp={
                "subagent_type": subagent_type,
                "description": f"{subagent_type} task",
                "prompt": f"do {subagent_type} work",
            },
            message_id=message_id,
            timestamp=timestamp_use,
        ),
        user_with_tool_result(
            tool_use_id,
            content=f"{subagent_type} done.",
            timestamp=timestamp_result,
            tool_use_result={
                "agentId": agent_id,
                "agentType": subagent_type,
                "totalDurationMs": 60_000,
                "totalTokens": 10_000,
                "totalToolUseCount": 3,
            },
        ),
    ]


@pytest.fixture()
def two_session_project(isolated_home: Path) -> Path:
    """Project with two sessions delegating to disjoint agent_types.

    Session ``alpha`` invokes only ``pm``; session ``beta`` invokes only
    ``tester``. With this layout, the JSON envelope's
    ``agent_metrics.by_agent_type`` keys are a clean witness for which
    sessions reached the diagnostics pipeline.
    """
    project_dir = isolated_home / "projects" / "-home-user-test-project"
    project_dir.mkdir()

    alpha_messages = _agent_invocation_pair(
        "toolu_alpha1",
        subagent_type="pm",
        agent_id="pm-alpha",
        timestamp_use="2026-05-01T10:00:00.000Z",
        timestamp_result="2026-05-01T10:01:00.000Z",
        message_id="msg_alpha",
    )
    beta_messages = _agent_invocation_pair(
        "toolu_beta1",
        subagent_type="tester",
        agent_id="tester-beta",
        timestamp_use="2026-05-02T10:00:00.000Z",
        timestamp_result="2026-05-02T10:01:00.000Z",
        message_id="msg_beta",
    )
    write_project_layout(project_dir, "alpha", alpha_messages)
    write_project_layout(project_dir, "beta", beta_messages)
    return isolated_home


def _run_analyze_json(
    runner: CliRunner,
    cli_app: typer.Typer,
    *extra: str,
) -> dict[str, Any]:
    """Invoke ``analyze --project project --diagnostics --json`` + extras.

    Returns the parsed envelope's ``data`` payload. Asserts a clean
    exit so the JSON parse below isn't masking a CLI error.
    """
    args = ["analyze", "--project", "project", "--diagnostics", "--json", *extra]
    result = runner.invoke(cli_app, args)
    assert result.exit_code == 0, result.output
    envelope = json.loads(result.stdout)
    assert envelope["command"] == "analyze"
    data = envelope["data"]
    assert isinstance(data, dict)
    return data


class TestSessionScopesEverything:
    @pytest.mark.usefixtures("two_session_project")
    def test_session_alpha_scopes_metrics_and_diagnostics(
        self, runner: CliRunner, cli_app: typer.Typer,
    ) -> None:
        data = _run_analyze_json(
            runner, cli_app, "--session", "alpha.jsonl",
        )

        assert data["session_count"] == 1
        assert data["scope_session"] == "alpha.jsonl"

        agent_keys = set(data["agent_metrics"]["by_agent_type"])
        assert agent_keys == {"pm"}, (
            f"beta session leaked into agent metrics: {agent_keys}"
        )

        signal_agent_types = {
            s["agent_type"] for s in data["diagnostics"]["signals"]
            if s.get("agent_type")
        }
        assert "tester" not in signal_agent_types, (
            f"diagnostics leaked from beta session: {signal_agent_types}"
        )

    @pytest.mark.usefixtures("two_session_project")
    def test_session_beta_scopes_to_beta(
        self, runner: CliRunner, cli_app: typer.Typer,
    ) -> None:
        data = _run_analyze_json(
            runner, cli_app, "--session", "beta.jsonl",
        )

        assert data["session_count"] == 1
        assert data["scope_session"] == "beta.jsonl"

        agent_keys = set(data["agent_metrics"]["by_agent_type"])
        assert agent_keys == {"tester"}

        signal_agent_types = {
            s["agent_type"] for s in data["diagnostics"]["signals"]
            if s.get("agent_type")
        }
        assert "pm" not in signal_agent_types

    @pytest.mark.usefixtures("two_session_project")
    def test_no_session_flag_aggregates_both(
        self, runner: CliRunner, cli_app: typer.Typer,
    ) -> None:
        data = _run_analyze_json(runner, cli_app)

        assert data["session_count"] == 2
        assert data["scope_session"] is None

        agent_keys = set(data["agent_metrics"]["by_agent_type"])
        assert agent_keys == {"pm", "tester"}, (
            "expected both sessions' agent types when --session is omitted"
        )


class TestSessionLatestMutualExclusion:
    """``--session`` + ``--latest`` is silently no-op'd in v0.6 — when
    ``--session`` is set, ``session_infos`` already has length 1, so
    ``[:latest]`` is a noop. Make the conflict explicit so users get a
    clear error instead of a misleading-but-accepted combination."""

    @pytest.mark.usefixtures("two_session_project")
    def test_session_with_latest_errors(
        self, runner: CliRunner, cli_app: typer.Typer,
    ) -> None:
        result = runner.invoke(
            cli_app,
            ["analyze", "--project", "project",
             "--session", "alpha.jsonl", "--latest", "3"],
        )
        assert result.exit_code != 0
        assert "--latest cannot be combined with --session" in result.stderr


class TestScopedFooterRendering:
    """Table footer must read ``Session: <filename>`` (not ``Sessions
    analyzed: 1``) when ``scope_session`` is set, so users can confirm
    scope from the terminal output without reading JSON."""

    @pytest.mark.usefixtures("two_session_project")
    def test_scoped_footer_shows_session_filename(
        self, runner: CliRunner, cli_app: typer.Typer,
    ) -> None:
        result = runner.invoke(
            cli_app,
            ["analyze", "--project", "project", "--session", "alpha.jsonl",
             "--no-diagnostics"],
        )
        assert result.exit_code == 0, result.output
        assert "Session: alpha.jsonl" in result.stdout
        assert "Sessions analyzed:" not in result.stdout

    @pytest.mark.usefixtures("two_session_project")
    def test_unscoped_footer_shows_session_count(
        self, runner: CliRunner, cli_app: typer.Typer,
    ) -> None:
        result = runner.invoke(
            cli_app,
            ["analyze", "--project", "project", "--no-diagnostics"],
        )
        assert result.exit_code == 0, result.output
        assert "Sessions analyzed: 2" in result.stdout
