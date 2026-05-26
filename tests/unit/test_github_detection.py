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


@pytest.fixture(autouse=True)
def _clear_detection_memos() -> None:
    """Clear lru_caches between tests so memoization doesn't leak."""
    detection.detect_gh.cache_clear()
    detection.gh_auth_login.cache_clear()


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

    def test_binary_disappeared_classifies_as_not_installed(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # shutil.which sees gh, but subprocess.run can't find it
        # (race: PATH change / uninstall between the two calls). The
        # taxonomy should give an install hint, not a login hint.
        monkeypatch.setattr(
            "agentfluent.github.detection.shutil.which", lambda _: "/usr/bin/gh",
        )

        def raises_fnf(*_args: Any, **_kwargs: Any) -> Any:
            raise FileNotFoundError

        monkeypatch.setattr(
            "agentfluent.github.detection.subprocess.run", raises_fnf,
        )
        with pytest.raises(GhNotInstalledError, match="Reinstall"):
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

    def test_success_is_memoized(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # detect_gh is wrapped in @lru_cache so callers (gh_api) can
        # invoke it as a per-request precondition without paying
        # subprocess cost on every call.
        monkeypatch.setattr(
            "agentfluent.github.detection.shutil.which", lambda _: "/usr/bin/gh",
        )
        calls = {"count": 0}

        def runner(*_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[str]:
            calls["count"] += 1
            return subprocess.CompletedProcess(
                args=_args, returncode=0, stdout="", stderr="",
            )

        monkeypatch.setattr(
            "agentfluent.github.detection.subprocess.run", runner,
        )
        detection.detect_gh()
        detection.detect_gh()
        assert calls["count"] == 1

    def test_failure_is_not_memoized(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Raised exceptions must NOT be cached — otherwise a failed
        # detection in a long-lived process would poison subsequent
        # retries after the user installs / authenticates gh.
        monkeypatch.setattr(
            "agentfluent.github.detection.shutil.which", lambda _: None,
        )
        with pytest.raises(GhNotInstalledError):
            detection.detect_gh()
        # Now flip the bypass so detection should succeed.
        monkeypatch.setattr(
            "agentfluent.github.detection.shutil.which", lambda _: "/usr/bin/gh",
        )
        monkeypatch.setattr(
            "agentfluent.github.detection.subprocess.run",
            _fake_run(returncode=0),
        )
        detection.detect_gh()


class TestGhAuthLogin:
    def test_returns_login_on_success(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            "agentfluent.github.detection.subprocess.run",
            _fake_run(stdout="alice\n", returncode=0),
        )
        assert detection.gh_auth_login() == "alice"

    def test_empty_login_raises(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            "agentfluent.github.detection.subprocess.run",
            _fake_run(stdout="", returncode=0),
        )
        with pytest.raises(GhNotAuthenticatedError, match="empty login"):
            detection.gh_auth_login()

    def test_failure_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "agentfluent.github.detection.subprocess.run",
            _fake_run(returncode=1, stderr="oops"),
        )
        with pytest.raises(GhNotAuthenticatedError, match="oops"):
            detection.gh_auth_login()

    def test_missing_binary_classifies_as_not_installed(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # If detect_gh was bypassed and gh isn't actually installed,
        # gh_auth_login should raise GhNotInstalledError, not the
        # generic GhNotAuthenticatedError — the taxonomy guides the
        # user's recovery action (install vs login).
        def raises_fnf(*_args: Any, **_kwargs: Any) -> Any:
            raise FileNotFoundError

        monkeypatch.setattr(
            "agentfluent.github.detection.subprocess.run", raises_fnf,
        )
        with pytest.raises(GhNotInstalledError):
            detection.gh_auth_login()

    def test_result_is_memoized(self, monkeypatch: pytest.MonkeyPatch) -> None:
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

    def test_https_remote_without_git_suffix(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        monkeypatch.setattr(
            "agentfluent.github.detection.subprocess.run",
            _fake_run(stdout="https://github.com/owner/some-repo\n"),
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

    def test_repo_name_with_dots_is_preserved(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        # GitHub allows dots in repo names (e.g., "claude.md", "foo.bar").
        # The non-greedy match + .git suffix consumption must leave
        # internal dots alone.
        monkeypatch.setattr(
            "agentfluent.github.detection.subprocess.run",
            _fake_run(stdout="git@github.com:owner/my.repo.name.git\n"),
        )
        repo = detection.infer_repo(tmp_path)
        assert (repo.owner, repo.repo) == ("owner", "my.repo.name")

    def test_non_github_remote_raises(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        monkeypatch.setattr(
            "agentfluent.github.detection.subprocess.run",
            _fake_run(stdout="git@gitlab.com:owner/repo.git\n"),
        )
        with pytest.raises(RepoInferenceError, match="not on github"):
            detection.infer_repo(tmp_path)

    def test_not_a_git_repo_emits_specific_error(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        # The common case for a project_disk_path that exists but
        # isn't a working tree must get a distinct, accurate error
        # message — not "No origin remote" (which is wrong) or the
        # generic git-failed shell output (which is confusing).
        monkeypatch.setattr(
            "agentfluent.github.detection.subprocess.run",
            _fake_run(
                returncode=128,
                stderr=(
                    "fatal: not a git repository "
                    "(or any of the parent directories): .git"
                ),
            ),
        )
        with pytest.raises(RepoInferenceError, match="not a git working tree"):
            detection.infer_repo(tmp_path)

    def test_no_origin_emits_specific_error(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        # Real git stderr for the missing-origin case starts with
        # "error: No such remote 'origin'". We branch on the lowered
        # substring so the user sees the intended hint.
        monkeypatch.setattr(
            "agentfluent.github.detection.subprocess.run",
            _fake_run(
                returncode=128,
                stderr="error: No such remote 'origin'",
            ),
        )
        with pytest.raises(RepoInferenceError, match="no `origin` remote"):
            detection.infer_repo(tmp_path)

    def test_other_git_failure_emits_generic_error(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        monkeypatch.setattr(
            "agentfluent.github.detection.subprocess.run",
            _fake_run(returncode=128, stderr="fatal: bad config file"),
        )
        with pytest.raises(RepoInferenceError, match="failed"):
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

    def test_strips_git_suffix(self) -> None:
        # parse_repo_override must produce the same normalized form
        # that infer_repo does; otherwise cache keys partition and
        # `repos/owner/repo.git/...` requests 404.
        r = detection.parse_repo_override("owner/repo.git")
        assert (r.owner, r.repo) == ("owner", "repo")

    def test_strips_trailing_slash(self) -> None:
        r = detection.parse_repo_override("owner/repo/")
        assert (r.owner, r.repo) == ("owner", "repo")

    @pytest.mark.parametrize("bad", [
        "owner", "owner/", "/repo", "owner/repo/extra", "",
        "owner/.",        # bare dot repo
        "owner/..",       # bare double-dot — would path-traverse
        "../foo/bar",     # leading .. — same risk
        ".owner/repo",    # leading dot in owner
        "-owner/repo",    # leading dash in owner (could be parsed as flag)
        "owner-/repo",    # trailing dash in owner
        "owner/-repo",    # leading dash in repo
        "owner/.repo",    # leading dot in repo
    ])
    def test_invalid_raises(self, bad: str) -> None:
        with pytest.raises(ValueError, match="OWNER/NAME"):
            detection.parse_repo_override(bad)
