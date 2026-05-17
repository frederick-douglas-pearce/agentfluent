"""Unit tests for ``diagnostics/git_signals.py``.

Subprocess invocations are mocked so the tests run without git on PATH
and without a real git repo. The integration test
(``tests/integration/test_git_signals_real.py``) covers the actual
``git log`` shape against this repo.
"""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from agentfluent.analytics.agent_metrics import AgentMetrics
from agentfluent.analytics.pipeline import SessionAnalysis
from agentfluent.analytics.tokens import TokenMetrics
from agentfluent.analytics.tools import ToolMetrics
from agentfluent.config.models import Severity
from agentfluent.core.session import SessionMessage
from agentfluent.diagnostics.git_signals import (
    _GIT_LOG_COMMIT_SEPARATOR,
    _GIT_LOG_FIELD_SEPARATOR,
    _find_feat_fix_pairs,
    _GitCommit,
    extract_git_quality_signals,
)
from agentfluent.diagnostics.models import SignalType


def _commit(
    sha: str, ts: datetime, subject: str, files: list[str],
) -> _GitCommit:
    return _GitCommit(
        sha=sha, timestamp=ts, subject=subject, files=frozenset(files),
    )


def _format_git_log(commits: list[_GitCommit]) -> str:
    """Build the exact stdout shape :func:`_parse_commits` consumes."""
    parts: list[str] = []
    for c in commits:
        header = _GIT_LOG_FIELD_SEPARATOR.join(
            [c.sha, c.timestamp.isoformat(), c.subject],
        )
        files = "\n".join(sorted(c.files))
        parts.append(f"{_GIT_LOG_COMMIT_SEPARATOR}{header}\n{files}")
    return "\n".join(parts) + "\n"


def _fake_run(stdout: str = "", returncode: int = 0):
    """Build a ``subprocess.run`` patch target returning the given output."""
    def _runner(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=args, returncode=returncode, stdout=stdout, stderr="",
        )
    return _runner


def _session(end: datetime, agent_types: list[str]) -> SessionAnalysis:
    """Minimal SessionAnalysis: one message with the end timestamp,
    plus one synthetic invocation per supplied agent_type."""
    from agentfluent.agents.models import AgentInvocation
    msg = SessionMessage(type="user", timestamp=end)
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
        session_path=Path("/tmp/x.jsonl"),
        token_metrics=TokenMetrics(),
        tool_metrics=ToolMetrics(),
        agent_metrics=AgentMetrics(),
        messages=[msg],
        invocations=invs,
    )


class TestFeatFixPairing:
    def test_pair_within_window_with_shared_files_emitted(self) -> None:
        t0 = datetime(2026, 5, 1, tzinfo=UTC)
        commits = [
            _commit("a", t0, "feat: add foo", ["src/foo.py"]),
            _commit("b", t0 + timedelta(days=2), "fix: foo edge case", ["src/foo.py"]),
        ]
        pairs = _find_feat_fix_pairs(commits, proximity_days=7)
        assert len(pairs) == 1
        assert pairs[0].days_between == 2
        assert pairs[0].shared_files == frozenset({"src/foo.py"})

    def test_pair_outside_window_skipped(self) -> None:
        t0 = datetime(2026, 5, 1, tzinfo=UTC)
        commits = [
            _commit("a", t0, "feat: add foo", ["src/foo.py"]),
            _commit("b", t0 + timedelta(days=30), "fix: foo regression", ["src/foo.py"]),
        ]
        assert _find_feat_fix_pairs(commits, proximity_days=7) == []

    def test_pair_with_no_file_overlap_skipped(self) -> None:
        t0 = datetime(2026, 5, 1, tzinfo=UTC)
        commits = [
            _commit("a", t0, "feat: add foo", ["src/foo.py"]),
            _commit("b", t0 + timedelta(days=1), "fix: bar bug", ["src/bar.py"]),
        ]
        assert _find_feat_fix_pairs(commits, proximity_days=7) == []

    def test_feat_with_scope_and_breaking_marker_matches(self) -> None:
        t0 = datetime(2026, 5, 1, tzinfo=UTC)
        commits = [
            _commit("a", t0, "feat(cli)!: redo flag parsing", ["cli.py"]),
            _commit("b", t0 + timedelta(days=1), "fix(cli): off-by-one", ["cli.py"]),
        ]
        pairs = _find_feat_fix_pairs(commits, proximity_days=7)
        assert len(pairs) == 1

    def test_non_conventional_commits_ignored(self) -> None:
        t0 = datetime(2026, 5, 1, tzinfo=UTC)
        commits = [
            _commit("a", t0, "added foo", ["src/foo.py"]),
            _commit("b", t0 + timedelta(days=1), "fixed foo", ["src/foo.py"]),
        ]
        assert _find_feat_fix_pairs(commits, proximity_days=7) == []

    def test_multiple_fixes_collapsed_into_one_pair(self) -> None:
        t0 = datetime(2026, 5, 1, tzinfo=UTC)
        commits = [
            _commit("a", t0, "feat: add foo", ["src/foo.py", "src/bar.py"]),
            _commit("b", t0 + timedelta(days=1), "fix: foo regression", ["src/foo.py"]),
            _commit("c", t0 + timedelta(days=3), "fix: bar regression", ["src/bar.py"]),
        ]
        pairs = _find_feat_fix_pairs(commits, proximity_days=7)
        assert len(pairs) == 1
        assert len(pairs[0].fixes) == 2
        assert pairs[0].shared_files == frozenset({"src/foo.py", "src/bar.py"})


class TestSubprocessErrorHandling:
    def test_missing_git_binary_returns_empty(self, tmp_path: Path) -> None:
        with patch(
            "agentfluent.diagnostics.git_signals.subprocess.run",
            side_effect=FileNotFoundError("git not found"),
        ):
            assert extract_git_quality_signals([], repo_dir=tmp_path) == []

    def test_non_zero_exit_returns_empty(self, tmp_path: Path) -> None:
        with patch(
            "agentfluent.diagnostics.git_signals.subprocess.run",
            new=_fake_run(stdout="", returncode=128),
        ):
            assert extract_git_quality_signals([], repo_dir=tmp_path) == []

    def test_timeout_returns_empty(self, tmp_path: Path) -> None:
        with patch(
            "agentfluent.diagnostics.git_signals.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=["git"], timeout=30),
        ):
            assert extract_git_quality_signals([], repo_dir=tmp_path) == []

    def test_empty_log_returns_empty(self, tmp_path: Path) -> None:
        with patch(
            "agentfluent.diagnostics.git_signals.subprocess.run",
            new=_fake_run(stdout="", returncode=0),
        ):
            assert extract_git_quality_signals([], repo_dir=tmp_path) == []


class TestSessionCorrelation:
    """When a session's last-message timestamp precedes the feat commit
    most closely, the signal reports whether that session used a
    review-style subagent."""

    @pytest.fixture()
    def feat_fix_stdout(self) -> str:
        t0 = datetime(2026, 5, 1, tzinfo=UTC)
        commits = [
            _commit("a1b2c3d", t0, "feat: add widget", ["src/widget.py"]),
            _commit(
                "deadbee", t0 + timedelta(days=2),
                "fix: widget off-by-one", ["src/widget.py"],
            ),
        ]
        return _format_git_log(commits)

    def test_session_with_reviewer_yields_info_severity(
        self, tmp_path: Path, feat_fix_stdout: str,
    ) -> None:
        feat_time = datetime(2026, 5, 1, tzinfo=UTC)
        # Session ends 1 minute before the feat commit -> matched.
        sessions = [
            _session(feat_time - timedelta(minutes=1), ["architect", "general-purpose"]),
        ]
        with patch(
            "agentfluent.diagnostics.git_signals.subprocess.run",
            new=_fake_run(stdout=feat_fix_stdout),
        ):
            signals = extract_git_quality_signals(sessions, repo_dir=tmp_path)
        assert len(signals) == 1
        assert signals[0].signal_type == SignalType.FEAT_FIX_PROXIMITY
        assert signals[0].severity == Severity.INFO
        assert signals[0].detail["session_used_reviewer"] is True

    def test_session_without_reviewer_yields_warning(
        self, tmp_path: Path, feat_fix_stdout: str,
    ) -> None:
        feat_time = datetime(2026, 5, 1, tzinfo=UTC)
        sessions = [
            _session(feat_time - timedelta(minutes=1), ["pm", "general-purpose"]),
        ]
        with patch(
            "agentfluent.diagnostics.git_signals.subprocess.run",
            new=_fake_run(stdout=feat_fix_stdout),
        ):
            signals = extract_git_quality_signals(sessions, repo_dir=tmp_path)
        assert len(signals) == 1
        assert signals[0].severity == Severity.WARNING
        assert signals[0].detail["session_used_reviewer"] is False

    def test_no_matching_session_yields_none_reviewer(
        self, tmp_path: Path, feat_fix_stdout: str,
    ) -> None:
        feat_time = datetime(2026, 5, 1, tzinfo=UTC)
        # All sessions end AFTER the feat commit -> no match.
        sessions = [_session(feat_time + timedelta(days=1), ["pm"])]
        with patch(
            "agentfluent.diagnostics.git_signals.subprocess.run",
            new=_fake_run(stdout=feat_fix_stdout),
        ):
            signals = extract_git_quality_signals(sessions, repo_dir=tmp_path)
        assert len(signals) == 1
        # No session match -> WARNING (no proof a reviewer was used).
        assert signals[0].severity == Severity.WARNING
        assert signals[0].detail["session_used_reviewer"] is None

    def test_signal_detail_carries_full_pair_metadata(
        self, tmp_path: Path, feat_fix_stdout: str,
    ) -> None:
        sessions: list[SessionAnalysis] = []
        with patch(
            "agentfluent.diagnostics.git_signals.subprocess.run",
            new=_fake_run(stdout=feat_fix_stdout),
        ):
            signals = extract_git_quality_signals(sessions, repo_dir=tmp_path)
        detail = signals[0].detail
        assert detail["feat_commit"]["sha"] == "a1b2c3d"
        assert detail["fix_commits"][0]["sha"] == "deadbee"
        assert detail["days_between"] == 2
        assert detail["shared_files"] == ["src/widget.py"]
