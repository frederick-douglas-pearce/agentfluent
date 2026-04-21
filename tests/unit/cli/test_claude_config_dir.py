"""CLI tests for --claude-config-dir flag and CLAUDE_CONFIG_DIR env var.

Covers precedence (flag > env > default), validation errors, and the scope
boundary: project-scope paths (.claude/ under CWD) are not affected by the
override.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from agentfluent.cli.exit_codes import EXIT_OK, EXIT_USER_ERROR


def _make_config_with_project(
    root: Path, fixtures_dir: Path, slug: str = "-home-user-test-project",
) -> None:
    """Populate a config root with one project + one session fixture."""
    project_dir = root / "projects" / slug
    project_dir.mkdir(parents=True)
    shutil.copy(fixtures_dir / "session_basic.jsonl", project_dir / "s1.jsonl")


def _make_config_with_user_agent(root: Path, fixtures_dir: Path) -> None:
    agents = root / "agents"
    agents.mkdir(parents=True)
    shutil.copy(
        fixtures_dir / "agents" / "well_configured.md",
        agents / "well-configured.md",
    )


class TestFlagResolution:
    def test_flag_points_list_at_custom_projects(
        self,
        runner: CliRunner,
        cli_app: typer.Typer,
        tmp_path: Path,
        fixtures_dir: Path,
    ) -> None:
        custom = tmp_path / "custom-claude"
        _make_config_with_project(custom, fixtures_dir)

        result = runner.invoke(
            cli_app, ["--claude-config-dir", str(custom), "list"],
        )

        assert result.exit_code == EXIT_OK
        assert "project" in result.stdout

    def test_env_var_used_when_flag_absent(
        self,
        runner: CliRunner,
        cli_app: typer.Typer,
        tmp_path: Path,
        fixtures_dir: Path,
    ) -> None:
        custom = tmp_path / "env-claude"
        _make_config_with_project(custom, fixtures_dir)

        result = runner.invoke(
            cli_app, ["list"], env={"CLAUDE_CONFIG_DIR": str(custom)},
        )

        assert result.exit_code == EXIT_OK

    def test_flag_takes_precedence_over_env(
        self,
        runner: CliRunner,
        cli_app: typer.Typer,
        tmp_path: Path,
        fixtures_dir: Path,
    ) -> None:
        # Env points at an empty config; flag points at one with a project.
        env_dir = tmp_path / "env-dir"
        (env_dir / "projects").mkdir(parents=True)

        flag_dir = tmp_path / "flag-dir"
        _make_config_with_project(flag_dir, fixtures_dir, slug="-x-flag-wins")

        result = runner.invoke(
            cli_app,
            ["--claude-config-dir", str(flag_dir), "list", "--quiet"],
            env={"CLAUDE_CONFIG_DIR": str(env_dir)},
        )

        assert result.exit_code == EXIT_OK
        # --quiet prints "<n> projects, <m> total sessions"; flag dir has 1.
        assert "1 projects" in result.stdout


class TestValidation:
    def test_nonexistent_path_exits_user_error(
        self, runner: CliRunner, cli_app: typer.Typer, tmp_path: Path,
    ) -> None:
        missing = tmp_path / "does-not-exist"
        result = runner.invoke(
            cli_app, ["--claude-config-dir", str(missing), "list"],
        )
        assert result.exit_code == EXIT_USER_ERROR
        assert "not found" in result.stderr

    def test_file_instead_of_dir_exits_user_error(
        self, runner: CliRunner, cli_app: typer.Typer, tmp_path: Path,
    ) -> None:
        f = tmp_path / "notadir.txt"
        f.write_text("")
        result = runner.invoke(cli_app, ["--claude-config-dir", str(f), "list"])
        assert result.exit_code == EXIT_USER_ERROR
        assert "not a directory" in result.stderr


class TestAllCommandsHonorOverride:
    def test_config_check_user_scope_uses_override(
        self,
        runner: CliRunner,
        cli_app: typer.Typer,
        tmp_path: Path,
        fixtures_dir: Path,
    ) -> None:
        custom = tmp_path / "cc"
        _make_config_with_user_agent(custom, fixtures_dir)

        result = runner.invoke(
            cli_app,
            [
                "--claude-config-dir", str(custom),
                "config-check", "--scope", "user",
            ],
        )

        assert result.exit_code == EXIT_OK

    def test_analyze_uses_override(
        self,
        runner: CliRunner,
        cli_app: typer.Typer,
        tmp_path: Path,
        fixtures_dir: Path,
    ) -> None:
        custom = tmp_path / "an"
        _make_config_with_project(custom, fixtures_dir)

        result = runner.invoke(
            cli_app,
            [
                "--claude-config-dir", str(custom),
                "analyze", "--project", "project", "--quiet",
            ],
        )

        assert result.exit_code == EXIT_OK


class TestScopeBoundary:
    """Project-scope paths (.claude/ under CWD) are NOT affected by the override."""

    def test_project_scope_reads_cwd_not_override(
        self,
        runner: CliRunner,
        cli_app: typer.Typer,
        tmp_path: Path,
        fixtures_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Set up a CWD containing project-scope agents.
        cwd = tmp_path / "work"
        project_agents = cwd / ".claude" / "agents"
        project_agents.mkdir(parents=True)
        shutil.copy(
            fixtures_dir / "agents" / "well_configured.md",
            project_agents / "project-agent.md",
        )
        monkeypatch.chdir(cwd)

        # Override points at an unrelated (empty) config tree.
        override = tmp_path / "override"
        (override / "agents").mkdir(parents=True)

        result = runner.invoke(
            cli_app,
            [
                "--claude-config-dir", str(override),
                "config-check", "--scope", "project",
            ],
        )

        # The project-scope agent is still discovered despite the override.
        # (The override's agents/ dir is empty; scope=project reads CWD.)
        assert result.exit_code == EXIT_OK
        assert "Agents scanned: 1" in result.stdout
