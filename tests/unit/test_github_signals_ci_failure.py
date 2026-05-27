"""Tests for the ``CI_FAILURE_FIRST_PUSH`` Tier 3 signal extractor.

All ``gh_api`` calls are mocked end-to-end so the tests run without
network access or `gh` on PATH. ``git log`` invocations are mocked
via the shared ``_git_helpers`` subprocess hook the Tier 2 tests
already use.
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
    extract_ci_failure_first_push_signals,
)
from agentfluent.diagnostics.models import SignalType
from agentfluent.github.models import GitHubRepo, RateLimitedError


def _session(
    *,
    end: datetime,
    agent_types: list[str],
    duration: timedelta = timedelta(minutes=30),
) -> SessionAnalysis:
    """Minimal SessionAnalysis with two timestamped messages bracketing
    a 30-minute window, plus synthetic invocations.

    Two messages (not one) so the session's attribution window has
    real width — commits authored mid-session fall inside
    ``[end - duration, end + slack]``, matching real usage where an
    agent's ``git commit`` lands during a multi-message session.
    """
    msgs = [
        SessionMessage(type="user", timestamp=end - duration),
        SessionMessage(type="user", timestamp=end),
    ]
    invs = [
        AgentInvocation(
            invocation_id=f"inv-{i}",
            agent_type=at,
            description=f"{at} call",
            prompt="do work",
            tool_use_id=f"toolu_{i}",
            session_id="s",
            session_path=Path("/x"),
        )
        for i, at in enumerate(agent_types)
    ]
    return SessionAnalysis(
        session_path=Path("/tmp/session.jsonl"),
        token_metrics=TokenMetrics(),
        tool_metrics=ToolMetrics(),
        agent_metrics=AgentMetrics(),
        invocations=invs,
        mcp_tool_calls=[],
        messages=msgs,
    )


def _git_log_stdout(commits: list[tuple[str, datetime, str]]) -> str:
    """Build the structured ``git log`` stdout that ``_parse_commits``
    consumes. Each entry is (sha, timestamp, subject)."""
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


@pytest.fixture
def repo_dir(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def github_repo() -> GitHubRepo:
    return GitHubRepo(owner="o", repo="r")


class _GhApiStub:
    """Captures the call sequence and returns canned responses keyed
    on endpoint prefix. Per-call counters power the rate-limit tests."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.responses: dict[str, Any] = {}
        # Endpoints that should raise RateLimitedError on the Nth call.
        self.rate_limit_after: dict[str, int] = {}
        self._counts: dict[str, int] = {}

    def __call__(self, endpoint: str, **_kwargs: Any) -> Any:
        self.calls.append(endpoint)
        # Match by prefix, longest match wins so a fully-specified
        # endpoint shadows a shorter catch-all key. (Without this,
        # rate-limit setup for ``repos/o/r/commits/<sha>/pulls`` would
        # be hidden by a broader ``repos/o/r/commits/`` fallback.)
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


class TestSignalEmission:
    def test_first_push_failure_emits_signal(
        self,
        monkeypatch: pytest.MonkeyPatch,
        repo_dir: Path,
        github_repo: GitHubRepo,
    ) -> None:
        session_end = datetime.now(UTC) - timedelta(hours=1)
        session = _session(end=session_end, agent_types=["general-purpose"])
        commit_sha = "abc1234567890abc"
        # git log: the session's commit is the first commit on a PR.
        commit_time = session_end - timedelta(minutes=5)
        monkeypatch.setattr(
            "agentfluent.diagnostics._git_helpers.subprocess.run",
            _completed(_git_log_stdout([(commit_sha, commit_time, "feat: x")])),
        )

        stub = _GhApiStub()
        stub.responses = {
            f"repos/o/r/commits/{commit_sha}/pulls":
                [{"number": 42, "title": "Add window filter",
                  "html_url": "https://github.com/o/r/pull/42"}],
            "repos/o/r/pulls/42/commits": [{"sha": commit_sha}],
            f"repos/o/r/commits/{commit_sha}/status": {
                "state": "failure",
                "statuses": [
                    {"context": "ci/pytest", "state": "failure"},
                ],
            },
        }
        monkeypatch.setattr(
            "agentfluent.diagnostics.github_signals.gh_api", stub,
        )

        signals, degraded = extract_ci_failure_first_push_signals(
            [session], github_repo=github_repo, repo_dir=repo_dir,
        )
        assert degraded is False
        assert len(signals) == 1
        sig = signals[0]
        assert sig.signal_type == SignalType.CI_FAILURE_FIRST_PUSH
        assert sig.severity == Severity.WARNING
        assert sig.agent_type is None
        assert sig.detail["pr_number"] == 42
        assert sig.detail["first_commit_sha"] == commit_sha
        assert sig.detail["primary_context"] == "ci/pytest"
        assert "Add window filter" in sig.message

    def test_first_push_success_emits_nothing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        repo_dir: Path,
        github_repo: GitHubRepo,
    ) -> None:
        # state=success on legacy status + empty check-runs response =>
        # no failure, no signal. The check-runs fallback is always
        # polled when the legacy endpoint reports nothing actionable
        # (so we can detect Actions-only repos); the test must mock it.
        session_end = datetime.now(UTC) - timedelta(hours=1)
        session = _session(end=session_end, agent_types=["pm"])
        commit_sha = "abc"
        monkeypatch.setattr(
            "agentfluent.diagnostics._git_helpers.subprocess.run",
            _completed(_git_log_stdout([(commit_sha, session_end, "feat: x")])),
        )
        stub = _GhApiStub()
        stub.responses = {
            f"repos/o/r/commits/{commit_sha}/pulls":
                [{"number": 1, "title": "x", "html_url": "url"}],
            "repos/o/r/pulls/1/commits": [{"sha": commit_sha}],
            f"repos/o/r/commits/{commit_sha}/status": {
                "state": "success", "statuses": [],
            },
            f"repos/o/r/commits/{commit_sha}/check-runs": [],
        }
        monkeypatch.setattr(
            "agentfluent.diagnostics.github_signals.gh_api", stub,
        )
        signals, degraded = extract_ci_failure_first_push_signals(
            [session], github_repo=github_repo, repo_dir=repo_dir,
        )
        assert signals == []
        assert degraded is False

    def test_pending_no_contexts_emits_nothing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        repo_dir: Path,
        github_repo: GitHubRepo,
    ) -> None:
        session_end = datetime.now(UTC) - timedelta(hours=1)
        session = _session(end=session_end, agent_types=["pm"])
        sha = "ab"
        monkeypatch.setattr(
            "agentfluent.diagnostics._git_helpers.subprocess.run",
            _completed(_git_log_stdout([(sha, session_end, "feat")])),
        )
        stub = _GhApiStub()
        stub.responses = {
            f"repos/o/r/commits/{sha}/pulls":
                [{"number": 1, "title": "x", "html_url": "url"}],
            "repos/o/r/pulls/1/commits": [{"sha": sha}],
            f"repos/o/r/commits/{sha}/status": {
                "state": "pending", "statuses": [],
            },
            # Check-runs fallback also reports nothing.
            f"repos/o/r/commits/{sha}/check-runs": [],
        }
        monkeypatch.setattr(
            "agentfluent.diagnostics.github_signals.gh_api", stub,
        )
        signals, degraded = extract_ci_failure_first_push_signals(
            [session], github_repo=github_repo, repo_dir=repo_dir,
        )
        assert signals == []
        assert degraded is False

    def test_github_actions_check_run_failure_emits_signal(
        self,
        monkeypatch: pytest.MonkeyPatch,
        repo_dir: Path,
        github_repo: GitHubRepo,
    ) -> None:
        # Modern GitHub Actions case: legacy status returns
        # state=success with no statuses (because Actions report via
        # Check Runs, not Statuses). The check-runs fallback finds
        # the failing run and surfaces a signal. Pre-fix this signal
        # NEVER fired on Actions-only repos — that's the recall bug
        # we're closing.
        session_end = datetime.now(UTC) - timedelta(hours=1)
        session = _session(end=session_end, agent_types=["general-purpose"])
        commit_sha = "actions0"
        monkeypatch.setattr(
            "agentfluent.diagnostics._git_helpers.subprocess.run",
            _completed(_git_log_stdout([(commit_sha, session_end, "feat: x")])),
        )
        stub = _GhApiStub()
        stub.responses = {
            f"repos/o/r/commits/{commit_sha}/pulls":
                [{"number": 7, "title": "Actions only PR",
                  "html_url": "https://github.com/o/r/pull/7"}],
            "repos/o/r/pulls/7/commits": [{"sha": commit_sha}],
            f"repos/o/r/commits/{commit_sha}/status": {
                # The legacy endpoint sees no Statuses-API entries
                # for an Actions-only repo. state defaults to
                # "success" with empty statuses[].
                "state": "success", "statuses": [],
            },
            f"repos/o/r/commits/{commit_sha}/check-runs": [
                {"name": "build (3.12)", "conclusion": "failure"},
                {"name": "lint", "conclusion": "success"},
            ],
        }
        monkeypatch.setattr(
            "agentfluent.diagnostics.github_signals.gh_api", stub,
        )
        signals, degraded = extract_ci_failure_first_push_signals(
            [session], github_repo=github_repo, repo_dir=repo_dir,
        )
        assert degraded is False
        assert len(signals) == 1
        sig = signals[0]
        # Check Runs use the run's `name` field as the context.
        assert sig.detail["primary_context"] == "build (3.12)"
        # Conclusion is normalized to "failure" so downstream
        # consumers see a single vocabulary.
        assert sig.detail["primary_state"] == "failure"

    def test_check_runs_skipped_when_legacy_reports_failure(
        self,
        monkeypatch: pytest.MonkeyPatch,
        repo_dir: Path,
        github_repo: GitHubRepo,
    ) -> None:
        # Optimization: when the legacy status endpoint already
        # reports a failure, the check-runs fallback is NOT polled
        # (saves one API call per failing PR). Verified by asserting
        # no check-runs response is required from the stub.
        session_end = datetime.now(UTC) - timedelta(hours=1)
        session = _session(end=session_end, agent_types=["pm"])
        sha = "legacy00"
        monkeypatch.setattr(
            "agentfluent.diagnostics._git_helpers.subprocess.run",
            _completed(_git_log_stdout([(sha, session_end, "feat")])),
        )
        stub = _GhApiStub()
        # No check-runs entry — would AssertionError if the fallback
        # were polled.
        stub.responses = {
            f"repos/o/r/commits/{sha}/pulls":
                [{"number": 3, "title": "legacy", "html_url": "url"}],
            "repos/o/r/pulls/3/commits": [{"sha": sha}],
            f"repos/o/r/commits/{sha}/status": {
                "state": "failure",
                "statuses": [{"context": "circleci/build", "state": "failure"}],
            },
        }
        monkeypatch.setattr(
            "agentfluent.diagnostics.github_signals.gh_api", stub,
        )
        signals, degraded = extract_ci_failure_first_push_signals(
            [session], github_repo=github_repo, repo_dir=repo_dir,
        )
        assert degraded is False
        assert len(signals) == 1
        # The check-runs fallback was NOT called.
        assert not any("/check-runs" in c for c in stub.calls)

    def test_first_commit_outside_window_skipped(
        self,
        monkeypatch: pytest.MonkeyPatch,
        repo_dir: Path,
        github_repo: GitHubRepo,
    ) -> None:
        # Session authored commit B on a PR whose FIRST commit (A) was
        # made outside any analyzed session window. The signal must
        # NOT fire — attribution would be wrong (the failing first
        # commit is not "our" agent's miss).
        session_end = datetime.now(UTC) - timedelta(hours=1)
        session = _session(end=session_end, agent_types=["pm"])
        sha_b = "later000"
        sha_a = "earlier0"
        # Only sha_b lands in the session window; sha_a is older.
        monkeypatch.setattr(
            "agentfluent.diagnostics._git_helpers.subprocess.run",
            _completed(_git_log_stdout([
                (sha_b, session_end - timedelta(minutes=10), "fix: y"),
            ])),
        )
        stub = _GhApiStub()
        stub.responses = {
            f"repos/o/r/commits/{sha_b}/pulls":
                [{"number": 7, "title": "x", "html_url": "url"}],
            # PR's first commit is sha_a, NOT in our window.
            "repos/o/r/pulls/7/commits": [{"sha": sha_a}],
            f"repos/o/r/commits/{sha_a}/status": {
                "state": "failure",
                "statuses": [{"context": "ci", "state": "failure"}],
            },
        }
        monkeypatch.setattr(
            "agentfluent.diagnostics.github_signals.gh_api", stub,
        )
        signals, degraded = extract_ci_failure_first_push_signals(
            [session], github_repo=github_repo, repo_dir=repo_dir,
        )
        assert signals == []
        assert degraded is False


class TestEarlyExits:
    def test_no_commits_skips_all_api_calls(
        self,
        monkeypatch: pytest.MonkeyPatch,
        repo_dir: Path,
        github_repo: GitHubRepo,
    ) -> None:
        session_end = datetime.now(UTC) - timedelta(hours=1)
        session = _session(end=session_end, agent_types=["pm"])
        # git log returns empty.
        monkeypatch.setattr(
            "agentfluent.diagnostics._git_helpers.subprocess.run",
            _completed(""),
        )
        stub = _GhApiStub()
        monkeypatch.setattr(
            "agentfluent.diagnostics.github_signals.gh_api", stub,
        )
        signals, degraded = extract_ci_failure_first_push_signals(
            [session], github_repo=github_repo, repo_dir=repo_dir,
        )
        assert signals == []
        assert degraded is False
        assert stub.calls == []

    def test_session_without_messages_excluded(
        self,
        monkeypatch: pytest.MonkeyPatch,
        repo_dir: Path,
        github_repo: GitHubRepo,
    ) -> None:
        # Session with no timestamped messages -> no window -> no
        # attribution -> early return. Commits in git_log are present
        # but unattributable.
        session = SessionAnalysis(
            session_path=Path("/tmp/s.jsonl"),
            token_metrics=TokenMetrics(),
            tool_metrics=ToolMetrics(),
            agent_metrics=AgentMetrics(),
            invocations=[],
            mcp_tool_calls=[],
            messages=[],
        )
        monkeypatch.setattr(
            "agentfluent.diagnostics._git_helpers.subprocess.run",
            _completed(_git_log_stdout([
                ("abc", datetime.now(UTC) - timedelta(hours=1), "feat"),
            ])),
        )
        stub = _GhApiStub()
        monkeypatch.setattr(
            "agentfluent.diagnostics.github_signals.gh_api", stub,
        )
        signals, degraded = extract_ci_failure_first_push_signals(
            [session], github_repo=github_repo, repo_dir=repo_dir,
        )
        assert signals == []
        assert degraded is False
        assert stub.calls == []


class TestRateLimitDegradation:
    def test_rate_limit_on_pulls_lookup_marks_degraded(
        self,
        monkeypatch: pytest.MonkeyPatch,
        repo_dir: Path,
        github_repo: GitHubRepo,
    ) -> None:
        # Two sessions, two commits; one of the commits/{sha}/pulls
        # calls raises RateLimitedError. The other commit proceeds
        # normally; degraded is True; partial signal is returned.
        session_end = datetime.now(UTC) - timedelta(hours=1)
        session_1 = _session(
            end=session_end - timedelta(hours=2), agent_types=["pm"],
        )
        session_2 = _session(end=session_end, agent_types=["pm"])
        sha_1 = "limited0"
        sha_2 = "ok123456"
        monkeypatch.setattr(
            "agentfluent.diagnostics._git_helpers.subprocess.run",
            _completed(_git_log_stdout([
                (sha_1, session_end - timedelta(hours=2, minutes=5),
                 "feat: a"),
                (sha_2, session_end - timedelta(minutes=5), "feat: b"),
            ])),
        )
        stub = _GhApiStub()
        # All endpoints are explicit — no catch-all fallback, so the
        # stub's AssertionError safety net catches any unintended call.
        stub.responses = {
            f"repos/o/r/commits/{sha_1}/pulls": [],
            f"repos/o/r/commits/{sha_2}/pulls":
                [{"number": 9, "title": "ok", "html_url": "url"}],
            "repos/o/r/pulls/9/commits": [{"sha": sha_2}],
            f"repos/o/r/commits/{sha_2}/status": {
                "state": "failure",
                "statuses": [{"context": "ci", "state": "failure"}],
            },
        }
        # Configure sha_1's pulls endpoint to rate-limit on its first call.
        stub.rate_limit_after[f"repos/o/r/commits/{sha_1}/pulls"] = 0

        monkeypatch.setattr(
            "agentfluent.diagnostics.github_signals.gh_api", stub,
        )
        signals, degraded = extract_ci_failure_first_push_signals(
            [session_1, session_2],
            github_repo=github_repo, repo_dir=repo_dir,
        )
        assert degraded is True
        # Second session's PR still emits its signal.
        assert len(signals) == 1
        assert signals[0].detail["pr_number"] == 9


class TestDeduplication:
    def test_pr_touched_by_two_commits_emits_one_signal(
        self,
        monkeypatch: pytest.MonkeyPatch,
        repo_dir: Path,
        github_repo: GitHubRepo,
    ) -> None:
        # Same PR appears in commits/{sha1}/pulls and
        # commits/{sha2}/pulls. The extractor must dedup by PR number
        # so we only fetch first-commit + status once and emit one
        # signal.
        session_end = datetime.now(UTC) - timedelta(hours=1)
        session = _session(end=session_end, agent_types=["pm"])
        sha_1 = "first000"
        sha_2 = "second00"
        monkeypatch.setattr(
            "agentfluent.diagnostics._git_helpers.subprocess.run",
            _completed(_git_log_stdout([
                (sha_1, session_end - timedelta(minutes=10), "feat"),
                (sha_2, session_end - timedelta(minutes=5), "fix"),
            ])),
        )
        stub = _GhApiStub()
        pr_meta = [{"number": 5, "title": "x", "html_url": "url"}]
        stub.responses = {
            f"repos/o/r/commits/{sha_1}/pulls": pr_meta,
            f"repos/o/r/commits/{sha_2}/pulls": pr_meta,
            "repos/o/r/pulls/5/commits": [{"sha": sha_1}],
            f"repos/o/r/commits/{sha_1}/status": {
                "state": "failure",
                "statuses": [{"context": "ci", "state": "failure"}],
            },
        }
        monkeypatch.setattr(
            "agentfluent.diagnostics.github_signals.gh_api", stub,
        )
        signals, degraded = extract_ci_failure_first_push_signals(
            [session], github_repo=github_repo, repo_dir=repo_dir,
        )
        assert degraded is False
        assert len(signals) == 1
        # Dedup happened — pulls/5/commits and the status endpoint
        # are each called exactly once across two commits.
        status_calls = [c for c in stub.calls if "/status" in c]
        assert len(status_calls) == 1


class TestRecoverableErrors:
    """Recoverable gh errors (ValueError on malformed JSON,
    GhNotInstalledError/GhNotAuthenticatedError on mid-run state
    change, RuntimeError on transient 5xx) must NOT crash the
    extractor — they should flip tier3_degraded and let other PRs
    proceed. Pre-fix, only RateLimitedError and RuntimeError were
    caught; ValueError + the typed gh exceptions would bubble out
    and abort the entire diagnostics run."""

    def test_value_error_from_gh_api_is_caught_and_degrades(
        self,
        monkeypatch: pytest.MonkeyPatch,
        repo_dir: Path,
        github_repo: GitHubRepo,
    ) -> None:
        from agentfluent.diagnostics import github_signals

        session_end = datetime.now(UTC) - timedelta(hours=1)
        session = _session(end=session_end, agent_types=["pm"])
        commit_sha = "valueerr"
        monkeypatch.setattr(
            "agentfluent.diagnostics._git_helpers.subprocess.run",
            _completed(_git_log_stdout([
                (commit_sha, session_end - timedelta(minutes=5), "feat"),
            ])),
        )

        def gh_api_raises_value_error(*_args: Any, **_kwargs: Any) -> Any:
            raise ValueError("non-JSON response from gh api")

        monkeypatch.setattr(github_signals, "gh_api", gh_api_raises_value_error)
        signals, degraded = github_signals.extract_ci_failure_first_push_signals(
            [session], github_repo=github_repo, repo_dir=repo_dir,
        )
        # No crash. Empty signals, degraded flipped.
        assert signals == []
        assert degraded is True

    def test_gh_not_authenticated_mid_run_is_caught_and_degrades(
        self,
        monkeypatch: pytest.MonkeyPatch,
        repo_dir: Path,
        github_repo: GitHubRepo,
    ) -> None:
        from agentfluent.diagnostics import github_signals
        from agentfluent.github.models import GhNotAuthenticatedError

        session_end = datetime.now(UTC) - timedelta(hours=1)
        session = _session(end=session_end, agent_types=["pm"])
        commit_sha = "autherr0"
        monkeypatch.setattr(
            "agentfluent.diagnostics._git_helpers.subprocess.run",
            _completed(_git_log_stdout([
                (commit_sha, session_end - timedelta(minutes=5), "feat"),
            ])),
        )

        def gh_api_raises_auth(*_args: Any, **_kwargs: Any) -> Any:
            raise GhNotAuthenticatedError("auth lapsed")

        monkeypatch.setattr(github_signals, "gh_api", gh_api_raises_auth)
        signals, degraded = github_signals.extract_ci_failure_first_push_signals(
            [session], github_repo=github_repo, repo_dir=repo_dir,
        )
        assert signals == []
        assert degraded is True

    def test_runtime_error_now_flips_degraded(
        self,
        monkeypatch: pytest.MonkeyPatch,
        repo_dir: Path,
        github_repo: GitHubRepo,
    ) -> None:
        # Behavior change from pre-review: RuntimeError used to return
        # (empty, degraded=False), silently dropping data. It now
        # contributes to degraded=True so transient 5xxs are visible
        # to the user via the tier3_degraded flag.
        from agentfluent.diagnostics import github_signals

        session_end = datetime.now(UTC) - timedelta(hours=1)
        session = _session(end=session_end, agent_types=["pm"])
        commit_sha = "runtime0"
        monkeypatch.setattr(
            "agentfluent.diagnostics._git_helpers.subprocess.run",
            _completed(_git_log_stdout([
                (commit_sha, session_end - timedelta(minutes=5), "feat"),
            ])),
        )

        def gh_api_raises_runtime(*_args: Any, **_kwargs: Any) -> Any:
            raise RuntimeError("HTTP 502 Bad Gateway")

        monkeypatch.setattr(github_signals, "gh_api", gh_api_raises_runtime)
        signals, degraded = github_signals.extract_ci_failure_first_push_signals(
            [session], github_repo=github_repo, repo_dir=repo_dir,
        )
        assert signals == []
        assert degraded is True
