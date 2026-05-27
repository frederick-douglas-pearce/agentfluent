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
    """Prefix-match endpoint stub with paging support.

    - ``responses[prefix]``: single canned response returned for every
      call matching ``prefix``.
    - ``paged_responses[prefix]``: list of per-page payloads consumed
      in order; the stub inspects the ``query_params['page']`` kwarg
      to disambiguate. After all pages are returned, subsequent
      calls return ``[]`` (signals end-of-pagination).
    - ``rate_limit_after[prefix]``: raise ``RateLimitedError`` after
      this many calls have matched ``prefix``.
    - ``calls`` / ``call_kwargs``: per-call audit log so tests can
      assert pagination/query params reached ``gh_api``.

    Longest-prefix wins for both response tables (a more specific
    endpoint shadows a less specific one).
    """

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.call_kwargs: list[dict[str, Any]] = []
        self.responses: dict[str, Any] = {}
        self.paged_responses: dict[str, list[Any]] = {}
        self.rate_limit_after: dict[str, int] = {}
        self._counts: dict[str, int] = {}
        self._page_indices: dict[str, int] = {}

    def __call__(self, endpoint: str, **kwargs: Any) -> Any:
        self.calls.append(endpoint)
        self.call_kwargs.append(kwargs)
        # Longest-prefix match across both response tables, so a
        # paged_responses key that exactly matches an endpoint
        # shadows a more general responses entry.
        all_prefixes = set(self.responses) | set(self.paged_responses)
        matches = sorted(
            (p for p in all_prefixes if endpoint.startswith(p)),
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
        if prefix in self.paged_responses:
            idx = self._page_indices.get(prefix, 0)
            pages = self.paged_responses[prefix]
            self._page_indices[prefix] = idx + 1
            if idx >= len(pages):
                # Pagination exhausted — return empty list so the
                # caller's loop terminates.
                return []
            return pages[idx]
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


class TestPagination:
    """The /pulls/{N}/comments endpoint must be paginated; pre-fix
    the extractor used gh's default 30-per-page cap and silently
    truncated PRs with >30 inline review comments — exactly the
    PRs the signal is designed to surface."""

    def test_pagination_loops_until_partial_page(
        self,
        monkeypatch: pytest.MonkeyPatch,
        repo_dir: Path,
        github_repo: GitHubRepo,
    ) -> None:
        # 250 inline comments across 100 lines: pre-fix capped at
        # 30 (density 0.30 → INFO). Post-fix counts all 250 across
        # 3 pages (density 2.50 → WARNING).
        sha = "shaPAG1"
        session = _wire_session_with_commit(monkeypatch, sha=sha)
        stub = _GhApiStub()
        stub.responses = {
            f"repos/o/r/commits/{sha}/pulls":
                [{"number": 1, "title": "x", "html_url": "url"}],
            "repos/o/r/pulls/1": {
                "additions": 60, "deletions": 40,
                "user": {"login": "bob"},
                "title": "x", "html_url": "url",
            },
        }
        stub.paged_responses = {
            "repos/o/r/pulls/1/comments": [
                # Page 1: 100 comments (max page size).
                [{"user": {"login": "alice"}}] * 100,
                # Page 2: 100 comments (still max).
                [{"user": {"login": "alice"}}] * 100,
                # Page 3: 50 comments (partial → break).
                [{"user": {"login": "alice"}}] * 50,
            ],
        }
        monkeypatch.setattr(
            "agentfluent.diagnostics.github_signals.gh_api", stub,
        )
        signals, degraded = extract_pr_review_comment_density_signals(
            [session], github_repo=github_repo, repo_dir=repo_dir,
        )
        assert degraded is False
        assert len(signals) == 1
        assert signals[0].detail["external_comment_count"] == 250
        assert signals[0].detail["density"] == pytest.approx(2.5)
        assert signals[0].severity == Severity.WARNING

        # Verify gh_api received per_page=100 + sequential page=
        # values, not just one call with defaults.
        comment_kwargs = [
            kw for c, kw in zip(stub.calls, stub.call_kwargs)
            if c == "repos/o/r/pulls/1/comments"
        ]
        assert len(comment_kwargs) == 3
        pages_requested = [
            kw.get("query_params", {}).get("page") for kw in comment_kwargs
        ]
        assert pages_requested == ["1", "2", "3"]
        per_page_values = {
            kw.get("query_params", {}).get("per_page") for kw in comment_kwargs
        }
        assert per_page_values == {"100"}

    def test_pagination_stops_at_partial_page(
        self,
        monkeypatch: pytest.MonkeyPatch,
        repo_dir: Path,
        github_repo: GitHubRepo,
    ) -> None:
        # A 50-comment PR fits in one partial page (< per_page=100),
        # so the helper stops after the first call.
        sha = "shaPAG2"
        session = _wire_session_with_commit(monkeypatch, sha=sha)
        stub = _GhApiStub()
        stub.responses = {
            f"repos/o/r/commits/{sha}/pulls":
                [{"number": 1, "title": "x", "html_url": "url"}],
            "repos/o/r/pulls/1": {
                "additions": 60, "deletions": 40,
                "user": {"login": "bob"},
                "title": "x", "html_url": "url",
            },
        }
        stub.paged_responses = {
            "repos/o/r/pulls/1/comments": [
                [{"user": {"login": "alice"}}] * 50,
            ],
        }
        monkeypatch.setattr(
            "agentfluent.diagnostics.github_signals.gh_api", stub,
        )
        signals, degraded = extract_pr_review_comment_density_signals(
            [session], github_repo=github_repo, repo_dir=repo_dir,
        )
        assert degraded is False
        assert len(signals) == 1
        assert signals[0].detail["external_comment_count"] == 50
        # Only one /comments call — the partial-page early-exit
        # avoids the wasted second-page roundtrip.
        comment_calls = [c for c in stub.calls if "/comments" in c]
        assert len(comment_calls) == 1


class TestMalformedPayloads:
    """Wrapper helpers must flip degraded (not silently return)
    when gh succeeds but the payload shape is unexpected."""

    def test_non_dict_user_in_comments_does_not_crash(
        self,
        monkeypatch: pytest.MonkeyPatch,
        repo_dir: Path,
        github_repo: GitHubRepo,
    ) -> None:
        # `(item.get('user') or {}).get('login')` pre-fix raised
        # AttributeError when user was a non-dict value (e.g.,
        # string). The exception was NOT in _GH_RECOVERABLE, so it
        # crashed the extractor mid-loop. Post-fix: isinstance
        # guards, malformed entries counted as external (the
        # liberal-count design choice).
        sha = "shaMAL"
        session = _wire_session_with_commit(monkeypatch, sha=sha)
        stub = _GhApiStub()
        stub.responses = {
            f"repos/o/r/commits/{sha}/pulls":
                [{"number": 1, "title": "x", "html_url": "url"}],
            "repos/o/r/pulls/1": {
                "additions": 60, "deletions": 40,
                "user": {"login": "bob"},
                "title": "x", "html_url": "url",
            },
            # Mixed shapes: dict, string (the trap), null, missing
            # user, valid external dict.
            "repos/o/r/pulls/1/comments": [
                {"user": {"login": "bob"}},      # self-review → skip
                {"user": "stringly-typed"},      # would crash pre-fix
                {"user": None},                  # null user
                {},                              # missing user key
                {"user": {"login": "alice"}},    # external
                {"user": {"login": "carol"}},    # external
            ],
        }
        monkeypatch.setattr(
            "agentfluent.diagnostics.github_signals.gh_api", stub,
        )
        signals, degraded = extract_pr_review_comment_density_signals(
            [session], github_repo=github_repo, repo_dir=repo_dir,
        )
        assert degraded is False
        # Self-review (bob): 1 skip. Stringly-typed, None-user,
        # missing-user: 3 counted as external (liberal count to
        # avoid systematic under-count when GitHub anonymizes
        # deleted users). Plus alice and carol: 2 real external.
        # Total external = 5; density = 5/100 = 0.05 < 0.1 → no signal.
        assert signals == []

    def test_non_dict_user_in_pr_detail_does_not_crash(
        self,
        monkeypatch: pytest.MonkeyPatch,
        repo_dir: Path,
        github_repo: GitHubRepo,
    ) -> None:
        # Same trap on the PR-detail call site. detail.get("user")
        # is a non-dict scalar — pre-fix: AttributeError, crash;
        # post-fix: author resolves to empty string → skip the PR
        # with NO_AUTHOR (no signal emitted for that PR).
        sha = "shaMAL2"
        session = _wire_session_with_commit(monkeypatch, sha=sha)
        stub = _GhApiStub()
        stub.responses = {
            f"repos/o/r/commits/{sha}/pulls":
                [{"number": 1, "title": "x", "html_url": "url"}],
            "repos/o/r/pulls/1": {
                "additions": 60, "deletions": 40,
                "user": "ghost-string",  # the trap
                "title": "x", "html_url": "url",
            },
        }
        # No /comments stub — the PR should be skipped at the
        # no-author gate before the comments fetch.
        monkeypatch.setattr(
            "agentfluent.diagnostics.github_signals.gh_api", stub,
        )
        signals, degraded = extract_pr_review_comment_density_signals(
            [session], github_repo=github_repo, repo_dir=repo_dir,
        )
        assert signals == []
        assert degraded is False
        # The comments endpoint must NOT have been called — the
        # no-author skip short-circuits before it.
        assert not any("/comments" in c for c in stub.calls)

    def test_non_dict_pr_detail_marks_degraded(
        self,
        monkeypatch: pytest.MonkeyPatch,
        repo_dir: Path,
        github_repo: GitHubRepo,
    ) -> None:
        # gh_api succeeded but returned a non-dict (jq quirk, API
        # surface change). Pre-fix returned (None, False) — silent
        # drop with no degraded flag. Post-fix: degraded=True so
        # the user sees data was lost.
        sha = "shaMAL3"
        session = _wire_session_with_commit(monkeypatch, sha=sha)
        stub = _GhApiStub()
        stub.responses = {
            f"repos/o/r/commits/{sha}/pulls":
                [{"number": 1, "title": "x", "html_url": "url"}],
            "repos/o/r/pulls/1": "not-a-dict",  # malformed response
        }
        monkeypatch.setattr(
            "agentfluent.diagnostics.github_signals.gh_api", stub,
        )
        signals, degraded = extract_pr_review_comment_density_signals(
            [session], github_repo=github_repo, repo_dir=repo_dir,
        )
        assert signals == []
        assert degraded is True


class TestCaseInsensitiveSelfReview:
    """GitHub logins are logically case-insensitive; mixed-case
    self-reviews must NOT escape the filter."""

    def test_mixed_case_author_and_commenter_treated_as_self(
        self,
        monkeypatch: pytest.MonkeyPatch,
        repo_dir: Path,
        github_repo: GitHubRepo,
    ) -> None:
        # PR author 'Alice' (canonical), 12 comments from 'alice'
        # (lowercased). Pre-fix: case-sensitive == treats them as
        # different users → all 12 count as external → density 0.12
        # INFO. Post-fix: casefold compare → all 12 self-reviews
        # filtered → no signal.
        sha = "shaCAS"
        session = _wire_session_with_commit(monkeypatch, sha=sha)
        stub = _GhApiStub()
        stub.responses = {
            f"repos/o/r/commits/{sha}/pulls":
                [{"number": 1, "title": "x", "html_url": "url"}],
            "repos/o/r/pulls/1": {
                "additions": 60, "deletions": 40,
                "user": {"login": "Alice"},  # canonical case
                "title": "x", "html_url": "url",
            },
            "repos/o/r/pulls/1/comments":
                [{"user": {"login": "alice"}}] * 12,  # lowercased
        }
        monkeypatch.setattr(
            "agentfluent.diagnostics.github_signals.gh_api", stub,
        )
        signals, degraded = extract_pr_review_comment_density_signals(
            [session], github_repo=github_repo, repo_dir=repo_dir,
        )
        assert signals == []
        assert degraded is False


class TestNoAuthorSkip:
    """When PR author can't be resolved, the signal must skip the
    PR (not silently disable the filter and count author comments
    as external)."""

    def test_null_user_skips_pr(
        self,
        monkeypatch: pytest.MonkeyPatch,
        repo_dir: Path,
        github_repo: GitHubRepo,
    ) -> None:
        sha = "shaAUTH"
        session = _wire_session_with_commit(monkeypatch, sha=sha)
        stub = _GhApiStub()
        stub.responses = {
            f"repos/o/r/commits/{sha}/pulls":
                [{"number": 1, "title": "x", "html_url": "url"}],
            "repos/o/r/pulls/1": {
                "additions": 60, "deletions": 40,
                "user": None,  # deleted account / ghost
                "title": "x", "html_url": "url",
            },
        }
        # No /comments stub — the no-author skip is before the
        # comments fetch; if the skip is broken the stub's
        # AssertionError safety net would trigger.
        monkeypatch.setattr(
            "agentfluent.diagnostics.github_signals.gh_api", stub,
        )
        signals, degraded = extract_pr_review_comment_density_signals(
            [session], github_repo=github_repo, repo_dir=repo_dir,
        )
        assert signals == []
        assert degraded is False
        assert not any("/comments" in c for c in stub.calls)


class TestGitFailureDegrades:
    """When git itself fails (binary missing, timeout, non-repo
    dir), the helper must flip degraded so the user sees that
    Tier 3 was incomplete, not a clean zero-signal run."""

    def test_git_log_failure_marks_degraded(
        self,
        monkeypatch: pytest.MonkeyPatch,
        repo_dir: Path,
        github_repo: GitHubRepo,
    ) -> None:
        session_end = datetime.now(UTC) - timedelta(hours=1)
        session = _session(end=session_end)

        # Simulate git not on PATH: subprocess.run raises FileNotFoundError.
        def fake_run(*_a: Any, **_kw: Any) -> Any:
            raise FileNotFoundError("no git")

        monkeypatch.setattr(
            "agentfluent.diagnostics._git_helpers.subprocess.run", fake_run,
        )
        stub = _GhApiStub()
        monkeypatch.setattr(
            "agentfluent.diagnostics.github_signals.gh_api", stub,
        )
        signals, degraded = extract_pr_review_comment_density_signals(
            [session], github_repo=github_repo, repo_dir=repo_dir,
        )
        assert signals == []
        assert degraded is True
        # No API calls — git failure short-circuits before any
        # commits-to-PRs fetch.
        assert stub.calls == []


class TestThresholdValidation:
    """Non-positive density_threshold disables severity tiering;
    the extractor must reject it loudly rather than silently
    flooding the user with WARNINGs."""

    def test_zero_threshold_raises(
        self,
        repo_dir: Path,
        github_repo: GitHubRepo,
    ) -> None:
        with pytest.raises(ValueError, match="must be > 0"):
            extract_pr_review_comment_density_signals(
                [], github_repo=github_repo, repo_dir=repo_dir,
                density_threshold=0.0,
            )

    def test_negative_threshold_raises(
        self,
        repo_dir: Path,
        github_repo: GitHubRepo,
    ) -> None:
        with pytest.raises(ValueError, match="must be > 0"):
            extract_pr_review_comment_density_signals(
                [], github_repo=github_repo, repo_dir=repo_dir,
                density_threshold=-0.1,
            )


class TestZeroLinesGate:
    """An explicit ``lines_changed == 0`` gate must short-circuit
    even when the user-configurable ``min_lines_changed`` is 0 —
    otherwise the divisor's ``max(0, 1) = 1`` defensive guard
    produces a nonsense density of N comments per 0 lines."""

    def test_pure_merge_pr_with_zero_lines_is_gated_even_with_min_zero(
        self,
        monkeypatch: pytest.MonkeyPatch,
        repo_dir: Path,
        github_repo: GitHubRepo,
    ) -> None:
        sha = "shaZ"
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
        # No /comments stub: the zero-lines gate must short-circuit
        # before fetching comments. If broken, the AssertionError
        # safety net catches the unexpected call.
        monkeypatch.setattr(
            "agentfluent.diagnostics.github_signals.gh_api", stub,
        )
        signals, degraded = extract_pr_review_comment_density_signals(
            [session], github_repo=github_repo, repo_dir=repo_dir,
            min_lines_changed=0,  # user lowers the gate
        )
        assert signals == []
        assert degraded is False


class TestPerPRErrorIsolation:
    """A malformed payload on one PR must not destroy signals
    computed for OTHER PRs in the same run."""

    def test_one_pr_raises_others_still_emit(
        self,
        monkeypatch: pytest.MonkeyPatch,
        repo_dir: Path,
        github_repo: GitHubRepo,
    ) -> None:
        session_end = datetime.now(UTC) - timedelta(hours=1)
        session = _session(end=session_end)
        sha = "shaERR"
        commit_time = session_end - timedelta(minutes=5)
        monkeypatch.setattr(
            "agentfluent.diagnostics._git_helpers.subprocess.run",
            _completed(_git_log_stdout([(sha, commit_time, "feat")])),
        )
        # Two PRs touched by the same commit.
        stub = _GhApiStub()
        stub.responses = {
            f"repos/o/r/commits/{sha}/pulls": [
                {"number": 1, "title": "bad", "html_url": "url1"},
                {"number": 2, "title": "good", "html_url": "url2"},
            ],
            # PR #1: detail.additions is a list (jq quirk).
            # `_safe_int` returns 0 for non-coercible values, so
            # lines_changed = 0 + 0 = 0 → gated, NO signal. Per-PR
            # try/except guards the iteration regardless.
            "repos/o/r/pulls/1": {
                "additions": ["broken"],  # genuinely bad shape
                "deletions": ["broken"],
                "user": {"login": "bob"},
                "title": "bad", "html_url": "url1",
            },
            # PR #2: well-formed.
            "repos/o/r/pulls/2": {
                "additions": 60, "deletions": 40,
                "user": {"login": "bob"},
                "title": "good", "html_url": "url2",
            },
            "repos/o/r/pulls/2/comments":
                [{"user": {"login": "alice"}}] * 15,  # 15/100 = 0.15
        }
        monkeypatch.setattr(
            "agentfluent.diagnostics.github_signals.gh_api", stub,
        )
        signals, degraded = extract_pr_review_comment_density_signals(
            [session], github_repo=github_repo, repo_dir=repo_dir,
        )
        # PR #2 still emits its signal (15 comments / 100 lines = 0.15).
        assert len(signals) == 1
        assert signals[0].detail["pr_number"] == 2


class TestTitleRefreshFix:
    """Title refresh uses explicit `isinstance + truthiness` check
    rather than `or`, so a PR with genuinely empty title doesn't
    silently fall through to the stale _PRRef.title."""

    def test_empty_detail_title_falls_back_to_ref_title(
        self,
        monkeypatch: pytest.MonkeyPatch,
        repo_dir: Path,
        github_repo: GitHubRepo,
    ) -> None:
        # Detail returns empty title; ref.title was set from the
        # earlier commits/{sha}/pulls call. Behavior: fall back to
        # ref.title since empty is unhelpful.
        sha = "shaT"
        session = _wire_session_with_commit(monkeypatch, sha=sha)
        stub = _GhApiStub()
        stub.responses = {
            f"repos/o/r/commits/{sha}/pulls":
                [{"number": 1, "title": "Cached title",
                  "html_url": "https://github.com/o/r/pull/1"}],
            "repos/o/r/pulls/1": {
                "additions": 60, "deletions": 40,
                "user": {"login": "bob"},
                "title": "",  # detail title is empty
                "html_url": "https://github.com/o/r/pull/1",
            },
            "repos/o/r/pulls/1/comments":
                [{"user": {"login": "alice"}}] * 15,
        }
        monkeypatch.setattr(
            "agentfluent.diagnostics.github_signals.gh_api", stub,
        )
        signals, degraded = extract_pr_review_comment_density_signals(
            [session], github_repo=github_repo, repo_dir=repo_dir,
        )
        assert len(signals) == 1
        # Falls back to ref.title since detail title was empty.
        assert signals[0].detail["pr_title"] == "Cached title"


class TestObservabilityCounters:
    """Density extractor emits an INFO log summarizing PR skip
    reasons. Pre-fix, all skips were silent — operators tuning
    thresholds had no visibility into why PRs weren't firing."""

    def test_skip_reasons_logged_at_end(
        self,
        monkeypatch: pytest.MonkeyPatch,
        repo_dir: Path,
        github_repo: GitHubRepo,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        import logging

        # Two PRs both below the lines gate.
        session_end = datetime.now(UTC) - timedelta(hours=1)
        session = _session(end=session_end)
        sha = "shaOBS"
        commit_time = session_end - timedelta(minutes=5)
        monkeypatch.setattr(
            "agentfluent.diagnostics._git_helpers.subprocess.run",
            _completed(_git_log_stdout([(sha, commit_time, "feat")])),
        )
        stub = _GhApiStub()
        stub.responses = {
            f"repos/o/r/commits/{sha}/pulls": [
                {"number": 1, "title": "x", "html_url": "url"},
                {"number": 2, "title": "y", "html_url": "url"},
            ],
            "repos/o/r/pulls/1": {
                "additions": 5, "deletions": 3,  # below 20-line gate
                "user": {"login": "bob"},
                "title": "x", "html_url": "url",
            },
            "repos/o/r/pulls/2": {
                "additions": 3, "deletions": 4,  # below 20-line gate
                "user": {"login": "bob"},
                "title": "y", "html_url": "url",
            },
        }
        monkeypatch.setattr(
            "agentfluent.diagnostics.github_signals.gh_api", stub,
        )
        with caplog.at_level(
            logging.INFO,
            logger="agentfluent.diagnostics.github_signals",
        ):
            signals, degraded = extract_pr_review_comment_density_signals(
                [session], github_repo=github_repo, repo_dir=repo_dir,
            )
        assert signals == []
        assert degraded is False
        # The summary INFO line must name the below-gate count.
        log_text = "\n".join(r.message for r in caplog.records)
        assert "below_lines_gate=2" in log_text


class TestSafeInt:
    """Verify the integer coercion helper doesn't raise on
    unexpected types (the int(str_or_int or 0) pattern would
    propagate ValueError to the pipeline's outer except)."""

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (0, 0),
            (42, 42),
            (None, 0),
            (True, 1),
            (False, 0),
            ("0", 0),
            ("42", 42),
            ("42.7", 42),
            ("not-a-number", 0),
            ([], 0),
            ({}, 0),
            (42.7, 42),
        ],
    )
    def test_safe_int(self, value: Any, expected: int) -> None:
        from agentfluent.diagnostics.github_signals import _safe_int
        assert _safe_int(value) == expected
