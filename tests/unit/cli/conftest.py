"""Fixtures for CLI tests: isolated home directory + CliRunner."""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import typer
from rich.console import Console
from typer.testing import CliRunner

from agentfluent.cli.main import app
from tests._builders import (
    assistant_with_tool_use,
    user_with_tool_result,
    write_project_layout,
)


def render_section(
    formatter: Callable[..., Any],
    diag: Any,
    *,
    verbose: bool = False,
    width: int = 120,
    **formatter_kwargs: Any,
) -> str:
    """Run a CLI formatter against a recording ``Console`` and return text.

    Shared by the formatter test files (``test_offload_candidates_formatting``,
    ``test_deep_diagnostics_formatting``) so the boilerplate
    ``Console(record=True, width=120, force_terminal=False)`` →
    formatter call → ``export_text()`` lives in one place. ``force_terminal``
    is fixed to ``False`` so the renderer doesn't pick up pytest's TTY
    state (#265). Extra keyword arguments are forwarded to ``formatter``.
    """
    console = Console(record=True, width=width, force_terminal=False)
    formatter(console, diag, verbose=verbose, **formatter_kwargs)
    return console.export_text()


@pytest.fixture()
def runner() -> CliRunner:
    """Click/Typer test runner. Click 8.2+ always separates stderr by default."""
    return CliRunner()


@pytest.fixture()
def cli_app() -> typer.Typer:
    """The top-level Typer app under test."""
    return app


@pytest.fixture()
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect ~/.claude/projects/ and ~/.claude/agents/ to a tmp dir.

    Returns the root tmp dir; caller can populate `projects/` and `agents/`
    subdirs as needed. The chdir is load-bearing: `scan_agents()` also
    resolves project-scope agents relative to cwd, so we block that path
    by pointing cwd at a tree with no `.claude/agents/` subdir.
    """
    projects_dir = tmp_path / "projects"
    user_agents_dir = tmp_path / "agents"
    projects_dir.mkdir()
    user_agents_dir.mkdir()

    monkeypatch.setattr(
        "agentfluent.core.discovery.DEFAULT_PROJECTS_DIR", projects_dir,
    )
    monkeypatch.setattr(
        "agentfluent.config.scanner.DEFAULT_USER_AGENTS_DIR", user_agents_dir,
    )
    monkeypatch.chdir(tmp_path)

    return tmp_path


@pytest.fixture()
def populated_home(isolated_home: Path, fixtures_dir: Path) -> Path:
    """Isolated home with one project (one session) and one user agent.

    The project directory is named `-home-user-test-project` so that
    `discovery.slug_to_display_name()` derives the display name `"project"`
    (the last dash-separated segment). Tests use `--project project`.
    """
    project_dir = isolated_home / "projects" / "-home-user-test-project"
    project_dir.mkdir()
    shutil.copy(fixtures_dir / "session_basic.jsonl", project_dir / "session-1.jsonl")

    agent_file = isolated_home / "agents" / "well-configured.md"
    shutil.copy(fixtures_dir / "agents" / "well_configured.md", agent_file)

    return isolated_home


def _load_jsonl(path: Path) -> list[dict[str, object]]:
    """Load a JSONL file as a list of dict records, skipping blanks."""
    return [
        json.loads(line) for line in path.read_text().splitlines() if line.strip()
    ]


@pytest.fixture()
def populated_home_with_traces(
    isolated_home: Path, fixtures_dir: Path,
) -> Path:
    """Isolated home with a session that carries linked subagent traces
    exhibiting errors and retries.

    Built programmatically via ``tests._builders.write_project_layout``
    so the session-to-trace agentId links are explicit. The trace
    content is loaded from the checked-in retry/stuck fixtures under
    ``tests/fixtures/subagents/`` — those already exhibit the error
    and retry shapes we need to exercise the full diagnostic pipeline.
    """
    project_dir = isolated_home / "projects" / "-home-user-test-project"
    project_dir.mkdir()

    # Two Agent invocations: pm (with a retry-pattern trace) and
    # Explore (with a stuck-pattern trace). agentIds deliberately
    # avoid an "agent-" prefix so the on-disk filenames stay clean.
    session_messages = [
        assistant_with_tool_use(
            "toolu_pm1",
            name="Agent",
            inp={
                "subagent_type": "pm",
                "description": "Review backlog",
                "prompt": "Review the backlog and create issues.",
            },
            message_id="msg_01pm",
            timestamp="2026-04-21T10:00:00.000Z",
        ),
        user_with_tool_result(
            "toolu_pm1",
            content="Reviewed backlog.",
            timestamp="2026-04-21T10:02:00.000Z",
            tool_use_result={
                "agentId": "pm-run-1",
                "agentType": "pm",
                "totalDurationMs": 120000,
                "totalTokens": 30000,
                "totalToolUseCount": 10,
            },
        ),
        assistant_with_tool_use(
            "toolu_ex1",
            name="Agent",
            inp={
                "subagent_type": "Explore",
                "description": "Map package structure",
                "prompt": "Find all Python files and summarize.",
            },
            message_id="msg_02ex",
            timestamp="2026-04-21T10:03:00.000Z",
        ),
        user_with_tool_result(
            "toolu_ex1",
            content="Mapped package structure.",
            timestamp="2026-04-21T10:05:00.000Z",
            tool_use_result={
                "agentId": "explore-run-1",
                "agentType": "Explore",
                "totalDurationMs": 90000,
                "totalTokens": 15000,
                "totalToolUseCount": 5,
            },
        ),
    ]

    subagent_traces = {
        "pm-run-1": _load_jsonl(fixtures_dir / "subagents" / "agent-retry.jsonl"),
        "explore-run-1": _load_jsonl(
            fixtures_dir / "subagents" / "agent-stuck.jsonl",
        ),
    }

    write_project_layout(
        project_dir, "session-1", session_messages,
        subagent_traces=subagent_traces,
    )

    return isolated_home
