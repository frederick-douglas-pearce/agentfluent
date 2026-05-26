"""End-to-end CLI checks for the ``--github`` flag plumbing.

Verifies the flag's *contract* in ``analyze``: it requires
``--diagnostics``, surfaces friendly errors when ``gh`` is missing
or unauthenticated, and produces a clear hint when repo inference
fails. The actual API call path is not exercised here — that lands
once the signal extractors (#400, #401) ship.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
import typer
from typer.testing import CliRunner

from agentfluent.github import detection

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


@pytest.fixture(autouse=True)
def _clear_auth_memo() -> None:
    detection.gh_auth_login.cache_clear()


class TestRequiresDiagnostics:
    def test_github_without_diagnostics_errors(
        self,
        runner: CliRunner,
        cli_app: typer.Typer,
        populated_home_with_traces: Path,
    ) -> None:
        result = runner.invoke(
            cli_app,
            ["analyze", "--project", "project", "--no-diagnostics", "--github"],
        )
        assert result.exit_code != 0
        combined = _strip_ansi(result.stdout + (result.stderr or ""))
        assert "--github requires --diagnostics" in combined


class TestDetectionFailures:
    def test_gh_missing_emits_install_hint(
        self,
        runner: CliRunner,
        cli_app: typer.Typer,
        populated_home_with_traces: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            "agentfluent.github.detection.shutil.which", lambda _: None,
        )
        result = runner.invoke(
            cli_app, ["analyze", "--project", "project", "--github"],
        )
        assert result.exit_code != 0
        combined = _strip_ansi(result.stdout + (result.stderr or ""))
        assert "GitHub CLI" in combined

    def test_gh_unauthenticated_emits_login_hint(
        self,
        runner: CliRunner,
        cli_app: typer.Typer,
        populated_home_with_traces: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            "agentfluent.github.detection.shutil.which", lambda _: "/usr/bin/gh",
        )

        def fake_run(*_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(
                args=_args, returncode=1, stdout="", stderr="not logged in",
            )

        monkeypatch.setattr(
            "agentfluent.github.detection.subprocess.run", fake_run,
        )
        result = runner.invoke(
            cli_app, ["analyze", "--project", "project", "--github"],
        )
        assert result.exit_code != 0
        combined = _strip_ansi(result.stdout + (result.stderr or ""))
        assert "gh auth login" in combined


class TestRepoOverrideValidation:
    def test_malformed_override_emits_friendly_error(
        self,
        runner: CliRunner,
        cli_app: typer.Typer,
        populated_home_with_traces: Path,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        # Bypass real detection and consent so the test focuses on the
        # --repo parse failure path.
        monkeypatch.setattr(
            "agentfluent.cli.commands.analyze.detect_gh", lambda: None,
        )
        monkeypatch.setattr(
            "agentfluent.cli.commands.analyze.prompt_and_record_if_needed",
            lambda **_kwargs: True,
        )
        result = runner.invoke(
            cli_app,
            [
                "analyze", "--project", "project",
                "--github", "--repo", "not-a-valid-repo",
            ],
        )
        assert result.exit_code != 0
        combined = _strip_ansi(result.stdout + (result.stderr or ""))
        assert "OWNER/NAME" in combined
