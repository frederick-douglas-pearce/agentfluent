"""Tests for the ``PR_REVIEW_COMMENT_DENSITY`` Tier 3 signal.

All ``gh_api`` calls are mocked end-to-end so the tests run without
network access or `gh` on PATH. ``git log`` invocations are mocked
via the shared ``_git_helpers`` subprocess hook the other tests use.

Reuses the ``_session`` fixture shape and ``_GhApiStub`` pattern
from ``test_github_signals_ci_failure.py``; if those test
infrastructure pieces drift, this file's setup will need the same
updates.
"""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from agentfluent.agents.models import AgentInvocation
from agentfluent.analytics.agent_metrics import AgentMetrics
from agentfluent.analytics.pipeline import SessionAnalysis
from agentfluent.analytics.tokens import TokenMetrics
from agentfluent.analytics.tools import ToolMetrics
from agentfluent.config.models import Severity
from agentfluent.core.session import SessionMessage
from agentfluent.diagnostics._git_helpers import (
    _GIT_LOG_COMMIT_SEPARATOR,
    _GIT_LOG_FIELD_SEPARATOR,
)
from agentfluent.diagnostics.github_signals import (
    extract_pr_review_comment_density_signals,
)
from agentfluent.diagnostics.models import SignalType
from agentfluent.github.models import GitHubRepo, RateLimitedError


def _session(
    *,
    end: datetime,
    duration: timedelta = timedelta(minutes=30),
) -> SessionAnalysis:
    msgs = [
        SessionMessage(type="user", timestamp=end - duration),
        SessionMessage(type="user", timestamp=end),
    ]
    inv = AgentInvocation(
        invocation_id="inv-0",
        agent_type="general-purpose",
        description="x",
        prompt="y",
        tool_use_id="toolu_0",
        session_id="s",
        session_path=Path("/x"),
    )
    return SessionAnalysis(
        session_path=Path("/tmp/session.jsonl"),
        token_metrics=TokenMetrics(),
        tool_metrics=ToolMetrics(),
        agent_metrics=AgentMetrics(),
        invocations=[inv],
        mcp_tool_calls=[],
        messages=msgs,
    )


def _git_log_stdout(commits: list[tuple[str, datetime, str]]) -> str:
    parts: list[str] = []
    for sha, ts, subject in commits:
        header = _GIT_LOG_FIELD_SEPARATOR.join([sha, ts.isoformat(), subject])
        parts.append(f"{_GIT_LOG_COMMIT_SEPARATOR}{header}\n")
    return "\n".join(parts) + "\n"


def _completed(stdout: str = "", returncode: int = 0) -> Any:
    def runner(*args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=args, returncode=returncode, stdout=stdout, stderr="",
        )
    return runner


class _GhApiStub:
    """Same prefix-match-with-AssertionError-on-unknown stub as #400's
    test file. Longest-prefix wins so a specific PR endpoint shadows
    a shorter catch-all (intentionally we use no catch-alls)."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.responses: dict[str, Any] = {}
        self.rate_limit_after: dict[str, int] = {}
        self._counts: dict[str, int] = {}

    def __call__(self, endpoint: str, **_kwargs: Any) -> Any:
        self.calls.append(endpoint)
        matches = sorted(
            (p for p in self.responses if endpoint.startswith(p)),
            key=len,
            reverse=True,
        )
        if not matches:
            raise AssertionError(f"unexpected gh_api call: {endpoint}")
        prefix = matches[0]
        count = self._counts.get(prefix, 0) + 1
        self._counts[prefix] = count
        limit = self.rate_limit_after.get(prefix)
        if limit is not None and count > limit:
            raise RateLimitedError(
                reset_at=datetime.now(UTC) + timedelta(seconds=60),
                endpoint=endpoint,
            )
        return self.responses[prefix]


@pytest.fixture
def repo_dir(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def github_repo() -> GitHubRepo:
    return GitHubRepo(owner="o", repo="r")


def _wire_session_with_commit(
    monkeypatch: pytest.MonkeyPatch, sha: str = "abc12345",
) -> SessionAnalysis:
    """Plant a single session + one commit in its window; return the
    session for use as an `extract_*([session], ...)` arg."""
    session_end = datetime.now(UTC) - timedelta(hours=1)
    session = _session(end=session_end)
    monkeypatch.setattr(
        "agentfluent.diagnostics._git_helpers.subprocess.run",
        _completed(_git_log_stdout([
            (sha, session_end - timedelta(minutes=5), "feat: x"),
        ])),
    )
    return session


class TestThresholdBands:
    def test_density_above_warning_threshold_emits_warning(
        self,
        monkeypatch: pytest.MonkeyPatch,
        repo_dir: Path,
        github_repo: GitHubRepo,
    ) -> None:
        # 50 comments / 100 lines = 0.5 = 5x threshold → WARNING.
        sha = "shaA"
        session = _wire_session_with_commit(monkeypatch, sha=sha)
        stub = _GhApiStub()
        stub.responses = {
            f"repos/o/r/commits/{sha}/pulls":
                [{"number": 42, "title": "Add window filter",
                  "html_url": "https://github.com/o/r/pull/42"}],
            "repos/o/r/pulls/42/comments":
                [{"user": {"login": "alice"}} for _ in range(50)],
            "repos/o/r/pulls/42": {
                "additions": 60, "deletions": 40,
                "user": {"login": "bob"},  # author != commenter
                "title": "Add window filter",
                "html_url": "https://github.com/o/r/pull/42",
            },
        }
        monkeypatch.setattr(
            "agentfluent.diagnostics.github_signals.gh_api", stub,
        )
        signals, degraded = extract_pr_review_comment_density_signals(
            [session], github_repo=github_repo, repo_dir=repo_dir,
        )
        assert degraded is False
        assert len(signals) == 1
        sig = signals[0]
        assert sig.signal_type == SignalType.PR_REVIEW_COMMENT_DENSITY
        assert sig.severity == Severity.WARNING
        assert sig.agent_type is None
        assert sig.detail["pr_number"] == 42
        assert sig.detail["density"] == pytest.approx(0.5)
        assert sig.detail["external_comment_count"] == 50
        assert sig.detail["lines_changed"] == 100

    def test_density_at_threshold_emits_info(
        self,
        monkeypatch: pytest.MonkeyPatch,
        repo_dir: Path,
        github_repo: GitHubRepo,
    ) -> None:
        # 10 comments / 100 lines = 0.1 = exactly threshold → INFO.
        # Confirms `>=` semantics at the boundary.
        sha = "shaB"
        session = _wire_session_with_commit(monkeypatch, sha=sha)
        stub = _GhApiStub()
        stub.responses = {
            f"repos/o/r/commits/{sha}/pulls":
                [{"number": 1, "title": "x", "html_url": "url"}],
            "repos/o/r/pulls/1/comments":
                [{"user": {"login": "alice"}} for _ in range(10)],
            "repos/o/r/pulls/1": {
                "additions": 50, "deletions": 50,
                "user": {"login": "bob"},
                "title": "x", "html_url": "url",
            },
        }
        monkeypatch.setattr(
            "agentfluent.diagnostics.github_signals.gh_api", stub,
        )
        signals, degraded = extract_pr_review_comment_density_signals(
            [session], github_repo=github_repo, repo_dir=repo_dir,
        )
        assert degraded is False
        assert len(signals) == 1
        assert signals[0].severity == Severity.INFO
        assert signals[0].detail["density"] == pytest.approx(0.1)

    def test_density_below_threshold_emits_nothing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        repo_dir: Path,
        github_repo: GitHubRepo,
    ) -> None:
        # 2 comments / 500 lines = 0.004 << threshold → no signal.
        sha = "shaC"
        session = _wire_session_with_commit(monkeypatch, sha=sha)
        stub = _GhApiStub()
        stub.responses = {
            f"repos/o/r/commits/{sha}/pulls":
                [{"number": 1, "title": "x", "html_url": "url"}],
            "repos/o/r/pulls/1/comments":
                [{"user": {"login": "alice"}}, {"user": {"login": "alice"}}],
            "repos/o/r/pulls/1": {
                "additions": 250, "deletions": 250,
                "user": {"login": "bob"},
                "title": "x", "html_url": "url",
            },
        }
        monkeypatch.setattr(
            "agentfluent.diagnostics.github_signals.gh_api", stub,
        )
        signals, degraded = extract_pr_review_comment_density_signals(
            [session], github_repo=github_repo, repo_dir=repo_dir,
        )
        assert signals == []
        assert degraded is False


class TestGates:
    def test_min_lines_changed_gate_suppresses_small_prs(
        self,
        monkeypatch: pytest.MonkeyPatch,
        repo_dir: Path,
        github_repo: GitHubRepo,
    ) -> None:
        # 10 comments / 5 lines would be density 2.0 but the PR is
        # below the min_lines_changed gate (20) — no signal.
        sha = "shaD"
        session = _wire_session_with_commit(monkeypatch, sha=sha)
        stub = _GhApiStub()
        stub.responses = {
            f"repos/o/r/commits/{sha}/pulls":
                [{"number": 1, "title": "x", "html_url": "url"}],
            "repos/o/r/pulls/1": {
                "additions": 3, "deletions": 2,
                "user": {"login": "bob"},
                "title": "x", "html_url": "url",
            },
            # Note: no /comments stub. The gate hits BEFORE the
            # comments fetch, so the AssertionError safety net
            # would fire if the gate were broken.
        }
        monkeypatch.setattr(
            "agentfluent.diagnostics.github_signals.gh_api", stub,
        )
        signals, degraded = extract_pr_review_comment_density_signals(
            [session], github_repo=github_repo, repo_dir=repo_dir,
        )
        assert signals == []
        assert degraded is False
        # Verify the /comments endpoint was NOT called — the gate
        # short-circuits before the second API call to save budget.
        assert not any("/comments" in c for c in stub.calls)

    def test_zero_lines_changed_no_divide_by_zero(
        self,
        monkeypatch: pytest.MonkeyPatch,
        repo_dir: Path,
        github_repo: GitHubRepo,
    ) -> None:
        # Pure-merge / pure-revert PRs report additions=0, deletions=0.
        # No signal (gate catches it), no division-by-zero crash.
        sha = "shaE"
        session = _wire_session_with_commit(monkeypatch, sha=sha)
        stub = _GhApiStub()
        stub.responses = {
            f"repos/o/r/commits/{sha}/pulls":
                [{"number": 1, "title": "x", "html_url": "url"}],
            "repos/o/r/pulls/1": {
                "additions": 0, "deletions": 0,
                "user": {"login": "bob"},
                "title": "x", "html_url": "url",
            },
        }
        monkeypatch.setattr(
            "agentfluent.diagnostics.github_signals.gh_api", stub,
        )
        signals, degraded = extract_pr_review_comment_density_signals(
            [session], github_repo=github_repo, repo_dir=repo_dir,
        )
        assert signals == []
        assert degraded is False

    def test_zero_external_comments_no_signal(
        self,
        monkeypatch: pytest.MonkeyPatch,
        repo_dir: Path,
        github_repo: GitHubRepo,
    ) -> None:
        sha = "shaF"
        session = _wire_session_with_commit(monkeypatch, sha=sha)
        stub = _GhApiStub()
        stub.responses = {
            f"repos/o/r/commits/{sha}/pulls":
                [{"number": 1, "title": "x", "html_url": "url"}],
            "repos/o/r/pulls/1": {
                "additions": 50, "deletions": 50,
                "user": {"login": "bob"},
                "title": "x", "html_url": "url",
            },
            "repos/o/r/pulls/1/comments": [],
        }
        monkeypatch.setattr(
            "agentfluent.diagnostics.github_signals.gh_api", stub,
        )
        signals, degraded = extract_pr_review_comment_density_signals(
            [session], github_repo=github_repo, repo_dir=repo_dir,
        )
        assert signals == []
        assert degraded is False


class TestSelfReviewExclusion:
    def test_self_review_comments_filtered_out(
        self,
        monkeypatch: pytest.MonkeyPatch,
        repo_dir: Path,
        github_repo: GitHubRepo,
    ) -> None:
        # PR author == 'bob'; 10 comments all by bob → 0 external →
        # no signal. Without the filter this would fire at density
        # 0.1 (INFO) — the test guards against accidentally treating
        # self-reviews as external feedback.
        sha = "shaG"
        session = _wire_session_with_commit(monkeypatch, sha=sha)
        stub = _GhApiStub()
        stub.responses = {
            f"repos/o/r/commits/{sha}/pulls":
                [{"number": 1, "title": "x", "html_url": "url"}],
            "repos/o/r/pulls/1": {
                "additions": 50, "deletions": 50,
                "user": {"login": "bob"},
                "title": "x", "html_url": "url",
            },
            "repos/o/r/pulls/1/comments":
                [{"user": {"login": "bob"}} for _ in range(10)],
        }
        monkeypatch.setattr(
            "agentfluent.diagnostics.github_signals.gh_api", stub,
        )
        signals, degraded = extract_pr_review_comment_density_signals(
            [session], github_repo=github_repo, repo_dir=repo_dir,
        )
        assert signals == []
        assert degraded is False

    def test_mixed_author_and_external_only_counts_external(
        self,
        monkeypatch: pytest.MonkeyPatch,
        repo_dir: Path,
        github_repo: GitHubRepo,
    ) -> None:
        # 6 by author + 12 by external reviewers / 100 lines = 0.12
        # (external-only) → above 0.1 threshold → INFO.
        sha = "shaH"
        session = _wire_session_with_commit(monkeypatch, sha=sha)
        comments = [{"user": {"login": "bob"}} for _ in range(6)]
        comments.extend([{"user": {"login": "alice"}} for _ in range(12)])
        stub = _GhApiStub()
        stub.responses = {
            f"repos/o/r/commits/{sha}/pulls":
                [{"number": 1, "title": "x", "html_url": "url"}],
            "repos/o/r/pulls/1": {
                "additions": 60, "deletions": 40,
                "user": {"login": "bob"},
                "title": "x", "html_url": "url",
            },
            "repos/o/r/pulls/1/comments": comments,
        }
        monkeypatch.setattr(
            "agentfluent.diagnostics.github_signals.gh_api", stub,
        )
        signals, degraded = extract_pr_review_comment_density_signals(
            [session], github_repo=github_repo, repo_dir=repo_dir,
        )
        assert degraded is False
        assert len(signals) == 1
        assert signals[0].severity == Severity.INFO
        assert signals[0].detail["external_comment_count"] == 12


class TestDegradation:
    def test_rate_limit_on_comments_marks_degraded(
        self,
        monkeypatch: pytest.MonkeyPatch,
        repo_dir: Path,
        github_repo: GitHubRepo,
    ) -> None:
        sha = "shaI"
        session = _wire_session_with_commit(monkeypatch, sha=sha)
        stub = _GhApiStub()
        stub.responses = {
            f"repos/o/r/commits/{sha}/pulls":
                [{"number": 1, "title": "x", "html_url": "url"}],
            "repos/o/r/pulls/1": {
                "additions": 50, "deletions": 50,
                "user": {"login": "bob"},
                "title": "x", "html_url": "url",
            },
            "repos/o/r/pulls/1/comments": [],
        }
        stub.rate_limit_after["repos/o/r/pulls/1/comments"] = 0
        monkeypatch.setattr(
            "agentfluent.diagnostics.github_signals.gh_api", stub,
        )
        signals, degraded = extract_pr_review_comment_density_signals(
            [session], github_repo=github_repo, repo_dir=repo_dir,
        )
        assert signals == []
        assert degraded is True

    def test_value_error_on_pr_detail_marks_degraded(
        self,
        monkeypatch: pytest.MonkeyPatch,
        repo_dir: Path,
        github_repo: GitHubRepo,
    ) -> None:
        # ValueError (non-JSON gh output) on the PR-detail call
        # must NOT crash the extractor — it should flip degraded
        # and let other PRs proceed.
        from agentfluent.diagnostics import github_signals

        sha = "shaJ"
        session = _wire_session_with_commit(monkeypatch, sha=sha)
        # The /pulls helper succeeds and returns a PR ref; then the
        # detail call raises ValueError.
        call_log: list[str] = []

        def fake_gh_api(endpoint: str, **_kwargs: Any) -> Any:
            call_log.append(endpoint)
            if endpoint.endswith("/pulls") and "/commits/" in endpoint:
                return [{"number": 1, "title": "x", "html_url": "url"}]
            if endpoint.endswith("/pulls/1"):
                raise ValueError("non-JSON")
            raise AssertionError(f"unexpected: {endpoint}")

        monkeypatch.setattr(github_signals, "gh_api", fake_gh_api)
        signals, degraded = github_signals.extract_pr_review_comment_density_signals(
            [session], github_repo=github_repo, repo_dir=repo_dir,
        )
        assert signals == []
        assert degraded is True


class TestEarlyExits:
    def test_no_commits_no_api_calls(
        self,
        monkeypatch: pytest.MonkeyPatch,
        repo_dir: Path,
        github_repo: GitHubRepo,
    ) -> None:
        session_end = datetime.now(UTC) - timedelta(hours=1)
        session = _session(end=session_end)
        monkeypatch.setattr(
            "agentfluent.diagnostics._git_helpers.subprocess.run",
            _completed(""),
        )
        stub = _GhApiStub()
        monkeypatch.setattr(
            "agentfluent.diagnostics.github_signals.gh_api", stub,
        )
        signals, degraded = extract_pr_review_comment_density_signals(
            [session], github_repo=github_repo, repo_dir=repo_dir,
        )
        assert signals == []
        assert degraded is False
        assert stub.calls == []
