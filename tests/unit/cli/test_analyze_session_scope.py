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
    assistant_message,
    assistant_with_tool_use,
    user_message,
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


@pytest.fixture()
def quality_signals_project(isolated_home: Path) -> Path:
    """Project with three sessions covering empty-result and quality-signal scope.

    - ``clean.jsonl``: one ``pm`` Agent invocation, no correction
      patterns in user messages. Used as the scope-control for the
      USER_CORRECTION leak test.
    - ``corrections.jsonl``: one ``tester`` Agent invocation plus user
      messages containing strong-correction phrases (``revert``,
      ``undo``, ``that's wrong``) that trigger ``USER_CORRECTION``.
      Three strong matches clear the ``MIN_CORRECTIONS_PER_SESSION = 2``
      gate even after the wrapper-stripping in #330 / #321.
    - ``quiet.jsonl``: one plain user message, no Agent invocations.
      Drives the "diagnostics skipped on zero-invocation session"
      branch at ``commands/analyze.py:364`` without erroring.

    USER_CORRECTION exercises the same ``parent_messages`` pipeline that
    feeds FILE_REWORK and REVIEWER_CAUGHT — scope correctness here
    implies scope correctness for the whole quality-signals layer.
    """
    project_dir = isolated_home / "projects" / "-home-user-test-project"
    project_dir.mkdir()

    clean_messages = [
        assistant_with_tool_use(
            "toolu_clean1",
            name="Agent",
            inp={
                "subagent_type": "pm",
                "description": "Review backlog",
                "prompt": "Review the backlog cleanly.",
            },
            message_id="msg_clean",
            timestamp="2026-05-01T10:00:00.000Z",
        ),
        user_with_tool_result(
            "toolu_clean1",
            content="Reviewed backlog.",
            timestamp="2026-05-01T10:01:00.000Z",
            tool_use_result={
                "agentId": "pm-clean",
                "agentType": "pm",
                "totalDurationMs": 60_000,
                "totalTokens": 10_000,
                "totalToolUseCount": 3,
            },
        ),
    ]

    corrections_messages = [
        user_message("start the work", timestamp="2026-05-02T10:00:00.000Z"),
        assistant_message(
            [{"type": "text", "text": "Working on it."}],
            message_id="msg_corr_a1",
            timestamp="2026-05-02T10:00:30.000Z",
        ),
        user_message(
            "revert that change please",
            timestamp="2026-05-02T10:01:00.000Z",
        ),
        assistant_message(
            [{"type": "text", "text": "Reverted."}],
            message_id="msg_corr_a2",
            timestamp="2026-05-02T10:01:30.000Z",
        ),
        user_message(
            "undo it, that's wrong",
            timestamp="2026-05-02T10:02:00.000Z",
        ),
        assistant_with_tool_use(
            "toolu_corr1",
            name="Agent",
            inp={
                "subagent_type": "tester",
                "description": "Run tests",
                "prompt": "Run the test suite.",
            },
            message_id="msg_corr_agent",
            timestamp="2026-05-02T10:03:00.000Z",
        ),
        user_with_tool_result(
            "toolu_corr1",
            content="Tests run.",
            timestamp="2026-05-02T10:04:00.000Z",
            tool_use_result={
                "agentId": "tester-corr",
                "agentType": "tester",
                "totalDurationMs": 60_000,
                "totalTokens": 8_000,
                "totalToolUseCount": 4,
            },
        ),
    ]

    quiet_messages = [
        user_message("just checking in", timestamp="2026-05-03T10:00:00.000Z"),
    ]

    write_project_layout(project_dir, "clean", clean_messages)
    write_project_layout(project_dir, "corrections", corrections_messages)
    write_project_layout(project_dir, "quiet", quiet_messages)
    return isolated_home


class TestEmptyResultDoesNotError:
    """A scoped session that produces no agent invocations must exit
    cleanly with no diagnostics block, not raise. Covers the
    ``commands/analyze.py:364`` branch (``total_invocations == 0``)."""

    @pytest.mark.usefixtures("quality_signals_project")
    def test_quiet_session_exits_zero_with_no_diagnostics(
        self, runner: CliRunner, cli_app: typer.Typer,
    ) -> None:
        result = runner.invoke(
            cli_app,
            ["analyze", "--project", "project", "--session", "quiet.jsonl",
             "--diagnostics", "--json"],
        )
        assert result.exit_code == 0, result.output
        envelope = json.loads(result.stdout)
        data = envelope["data"]
        assert data["scope_session"] == "quiet.jsonl"
        assert data["session_count"] == 1
        assert data["diagnostics"] is None
        assert "Traceback" not in result.output


class TestQualitySignalsScope:
    """Quality signals are extracted from ``parent_messages``, which the
    CLI's session-path filter constrains to one session. Asserting
    USER_CORRECTION scope is a proxy for FILE_REWORK and REVIEWER_CAUGHT
    scope: they all read the same constrained list."""

    @pytest.mark.usefixtures("quality_signals_project")
    def test_corrections_session_emits_user_correction(
        self, runner: CliRunner, cli_app: typer.Typer,
    ) -> None:
        data = _run_analyze_json(
            runner, cli_app, "--session", "corrections.jsonl",
        )
        signal_types = {s["signal_type"] for s in data["diagnostics"]["signals"]}
        assert "user_correction" in signal_types, (
            f"expected user_correction in {signal_types}"
        )

    @pytest.mark.usefixtures("quality_signals_project")
    def test_clean_session_does_not_inherit_corrections(
        self, runner: CliRunner, cli_app: typer.Typer,
    ) -> None:
        data = _run_analyze_json(
            runner, cli_app, "--session", "clean.jsonl",
        )
        signal_types = {s["signal_type"] for s in data["diagnostics"]["signals"]}
        assert "user_correction" not in signal_types, (
            f"user_correction leaked into clean session scope: {signal_types}"
        )
