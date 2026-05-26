"""End-to-end CLI checks for the ``--github`` flag plumbing.

Verifies the flag's *contract* in ``analyze``: it requires
``--diagnostics``, surfaces friendly errors when ``gh`` is missing
or unauthenticated, produces a clear hint when repo inference fails,
runs Tier 3 setup regardless of invocation count, propagates
``--github-no-cache`` through ``run_diagnostics``, and validates
``--repo`` before persisting any consent state. The actual API call
path is not exercised here — that lands once the signal extractors
(#400, #401) ship.
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
def _clear_detection_memos() -> None:
    detection.gh_auth_login.cache_clear()
    detection.detect_gh.cache_clear()


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
    def test_malformed_override_emits_friendly_error_before_detection(
        self,
        runner: CliRunner,
        cli_app: typer.Typer,
        populated_home_with_traces: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # --repo is now validated BEFORE detect_gh and the consent
        # prompt fire, so a malformed override never persists a consent
        # record on disk for a run that exits with USER_ERROR. We don't
        # monkeypatch detect_gh / prompt — if the order regressed, the
        # test would either fail with a different error or auto-record
        # consent (which the XDG-isolated tmp_path fixture would catch).
        detect_called = {"count": 0}

        def fake_detect() -> None:
            detect_called["count"] += 1

        prompt_called = {"count": 0}

        def fake_prompt(**_kwargs: Any) -> bool:
            prompt_called["count"] += 1
            return True

        monkeypatch.setattr(
            "agentfluent.cli.commands.analyze.detect_gh", fake_detect,
        )
        monkeypatch.setattr(
            "agentfluent.cli.commands.analyze.prompt_and_record_if_needed",
            fake_prompt,
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
        # Neither detection nor consent should have run — the order
        # check is what this test exists for.
        assert detect_called["count"] == 0
        assert prompt_called["count"] == 0


class TestNoCacheFlag:
    def test_no_cache_threads_through_run_diagnostics(
        self,
        runner: CliRunner,
        cli_app: typer.Typer,
        populated_home_with_traces: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # The --github-no-cache flag must reach run_diagnostics so that
        # signal extractors (#400/#401) can forward it to gh_api. The
        # Pylance "unused variable" warning that originally exposed
        # this regression now has a regression test.
        from agentfluent.github.models import GitHubRepo

        captured: dict[str, Any] = {}

        def fake_run_diagnostics(*_args: Any, **kwargs: Any) -> Any:
            captured.update(kwargs)
            from agentfluent.diagnostics.models import DiagnosticsResult
            return DiagnosticsResult()

        monkeypatch.setattr(
            "agentfluent.cli.commands.analyze.run_diagnostics",
            fake_run_diagnostics,
        )
        monkeypatch.setattr(
            "agentfluent.cli.commands.analyze.detect_gh", lambda: None,
        )
        monkeypatch.setattr(
            "agentfluent.cli.commands.analyze.prompt_and_record_if_needed",
            lambda **_kw: True,
        )
        monkeypatch.setattr(
            "agentfluent.cli.commands.analyze.infer_repo",
            lambda _p: GitHubRepo(owner="o", repo="r"),
        )

        result = runner.invoke(
            cli_app,
            [
                "analyze", "--project", "project",
                "--github", "--github-no-cache",
            ],
        )
        assert result.exit_code == 0, result.stdout + (result.stderr or "")
        assert captured.get("github_no_cache") is True
        assert captured.get("github_repo") == GitHubRepo(owner="o", repo="r")

    def test_default_is_false(
        self,
        runner: CliRunner,
        cli_app: typer.Typer,
        populated_home_with_traces: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from agentfluent.github.models import GitHubRepo

        captured: dict[str, Any] = {}

        def fake_run_diagnostics(*_args: Any, **kwargs: Any) -> Any:
            captured.update(kwargs)
            from agentfluent.diagnostics.models import DiagnosticsResult
            return DiagnosticsResult()

        monkeypatch.setattr(
            "agentfluent.cli.commands.analyze.run_diagnostics",
            fake_run_diagnostics,
        )
        monkeypatch.setattr(
            "agentfluent.cli.commands.analyze.detect_gh", lambda: None,
        )
        monkeypatch.setattr(
            "agentfluent.cli.commands.analyze.prompt_and_record_if_needed",
            lambda **_kw: True,
        )
        monkeypatch.setattr(
            "agentfluent.cli.commands.analyze.infer_repo",
            lambda _p: GitHubRepo(owner="o", repo="r"),
        )

        result = runner.invoke(
            cli_app,
            ["analyze", "--project", "project", "--github"],
        )
        assert result.exit_code == 0
        assert captured.get("github_no_cache") is False


class TestTier3SetupNotGatedByInvocations:
    def test_zero_invocations_still_runs_detection(
        self,
        runner: CliRunner,
        cli_app: typer.Typer,
        isolated_home: Path,
        fixtures_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # A project with zero agent invocations + --github used to
        # silently skip detection/consent and exit 0. Now Tier 3 setup
        # runs unconditionally when --github is passed, so the user
        # sees the gh-not-installed (or detection) error and can
        # correct it instead of believing Tier 3 ran successfully.
        import shutil
        project_dir = isolated_home / "projects" / "-home-user-test-project"
        project_dir.mkdir()
        # Use a fixture with no Agent invocations so all_invocations=[].
        shutil.copy(
            fixtures_dir / "session_basic.jsonl",
            project_dir / "session-1.jsonl",
        )

        monkeypatch.setattr(
            "agentfluent.github.detection.shutil.which", lambda _: None,
        )

        result = runner.invoke(
            cli_app,
            ["analyze", "--project", "project", "--github"],
        )
        # Detection ran (and failed), so the user gets the install hint
        # instead of a silent success that they'd discover later.
        assert result.exit_code != 0
        combined = _strip_ansi(result.stdout + (result.stderr or ""))
        assert "GitHub CLI" in combined
