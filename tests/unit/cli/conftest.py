"""Fixtures for CLI tests: isolated home directory + CliRunner."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from agentfluent.cli.main import app


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
