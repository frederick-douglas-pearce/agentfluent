"""Tests for Tier 3 detection helpers.

Subprocess calls are mocked end-to-end. The integration tests
(``tests/integration/``) — outside CI — cover the actual ``gh`` and
``git`` invocations against the user's real environment.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from agentfluent.github import detection
from agentfluent.github.models import (
    GhNotAuthenticatedError,
    GhNotInstalledError,
    RepoInferenceError,
)


def _fake_run(*, stdout: str = "", stderr: str = "", returncode: int = 0) -> Any:
    def runner(*_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=_args, returncode=returncode, stdout=stdout, stderr=stderr,
        )
    return runner


class TestDetectGh:
    def test_missing_binary_raises(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("agentfluent.github.detection.shutil.which", lambda _: None)
        with pytest.raises(GhNotInstalledError, match="GitHub CLI"):
            detection.detect_gh()

    def test_unauthenticated_raises(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            "agentfluent.github.detection.shutil.which", lambda _: "/usr/bin/gh",
        )
        monkeypatch.setattr(
            "agentfluent.github.detection.subprocess.run",
            _fake_run(returncode=1, stderr="not logged in"),
        )
        with pytest.raises(GhNotAuthenticatedError, match="gh auth login"):
            detection.detect_gh()

    def test_authenticated_does_not_raise(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            "agentfluent.github.detection.shutil.which", lambda _: "/usr/bin/gh",
        )
        monkeypatch.setattr(
            "agentfluent.github.detection.subprocess.run",
            _fake_run(stdout="Logged in to github.com", returncode=0),
        )
        detection.detect_gh()


class TestGhAuthLogin:
    def teardown_method(self) -> None:
        detection.gh_auth_login.cache_clear()

    def test_returns_login_on_success(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        detection.gh_auth_login.cache_clear()
        monkeypatch.setattr(
            "agentfluent.github.detection.subprocess.run",
            _fake_run(stdout="alice\n", returncode=0),
        )
        assert detection.gh_auth_login() == "alice"

    def test_empty_login_raises(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        detection.gh_auth_login.cache_clear()
        monkeypatch.setattr(
            "agentfluent.github.detection.subprocess.run",
            _fake_run(stdout="", returncode=0),
        )
        with pytest.raises(GhNotAuthenticatedError, match="empty login"):
            detection.gh_auth_login()

    def test_failure_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        detection.gh_auth_login.cache_clear()
        monkeypatch.setattr(
            "agentfluent.github.detection.subprocess.run",
            _fake_run(returncode=1, stderr="oops"),
        )
        with pytest.raises(GhNotAuthenticatedError, match="oops"):
            detection.gh_auth_login()

    def test_result_is_memoized(self, monkeypatch: pytest.MonkeyPatch) -> None:
        detection.gh_auth_login.cache_clear()
        calls = {"count": 0}

        def runner(*_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[str]:
            calls["count"] += 1
            return subprocess.CompletedProcess(
                args=_args, returncode=0, stdout="alice\n", stderr="",
            )

        monkeypatch.setattr(
            "agentfluent.github.detection.subprocess.run", runner,
        )
        detection.gh_auth_login()
        detection.gh_auth_login()
        assert calls["count"] == 1


class TestInferRepo:
    def test_https_remote_parses(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        monkeypatch.setattr(
            "agentfluent.github.detection.subprocess.run",
            _fake_run(stdout="https://github.com/owner/some-repo.git\n"),
        )
        repo = detection.infer_repo(tmp_path)
        assert (repo.owner, repo.repo) == ("owner", "some-repo")

    def test_ssh_remote_parses(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        monkeypatch.setattr(
            "agentfluent.github.detection.subprocess.run",
            _fake_run(stdout="git@github.com:owner/some-repo.git\n"),
        )
        repo = detection.infer_repo(tmp_path)
        assert (repo.owner, repo.repo) == ("owner", "some-repo")

    def test_non_github_remote_raises(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        monkeypatch.setattr(
            "agentfluent.github.detection.subprocess.run",
            _fake_run(stdout="git@gitlab.com:owner/repo.git\n"),
        )
        with pytest.raises(RepoInferenceError, match="not on github"):
            detection.infer_repo(tmp_path)

    def test_no_origin_raises(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        monkeypatch.setattr(
            "agentfluent.github.detection.subprocess.run",
            _fake_run(returncode=128, stderr="error: No such remote 'origin'"),
        )
        with pytest.raises(RepoInferenceError, match="No `origin` remote"):
            detection.infer_repo(tmp_path)

    def test_none_path_raises(self) -> None:
        with pytest.raises(RepoInferenceError, match="on-disk path"):
            detection.infer_repo(None)

    def test_missing_path_raises(self, tmp_path: Path) -> None:
        with pytest.raises(RepoInferenceError, match="does not exist"):
            detection.infer_repo(tmp_path / "ghost")


class TestParseRepoOverride:
    def test_valid_input(self) -> None:
        r = detection.parse_repo_override("owner/repo-name")
        assert (r.owner, r.repo) == ("owner", "repo-name")

    def test_strips_whitespace(self) -> None:
        r = detection.parse_repo_override("  owner/repo  ")
        assert (r.owner, r.repo) == ("owner", "repo")

    @pytest.mark.parametrize("bad", ["owner", "owner/", "/repo", "owner/repo/extra", ""])
    def test_invalid_raises(self, bad: str) -> None:
        with pytest.raises(ValueError, match="OWNER/NAME"):
            detection.parse_repo_override(bad)
