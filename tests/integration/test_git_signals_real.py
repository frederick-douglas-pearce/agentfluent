"""End-to-end smoke test for ``extract_git_quality_signals`` against
this repo's real git history.

Skipped in CI (no git repo in the actions-checkout that runs unit
tests; also marked ``integration``). Locally, this catches issues
where the synthetic stdout the unit tests mock diverges from real
``git log`` output shape — e.g., commit subjects with embedded
newlines, file paths with unusual characters, timezone handling.

The test runs against the agentfluent repo itself, so it requires a
clone with at least one ``feat:`` commit in the last 90 days. The
repo's CHANGELOG demonstrates ample such commits exist.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agentfluent.diagnostics.git_signals import extract_git_quality_signals
from agentfluent.diagnostics.models import SignalType

REPO_ROOT = Path(__file__).resolve().parents[2]

# Skip if git isn't installed or the path isn't a repo. The
# ``check=True`` would raise on a non-repo dir, so we explicitly
# inspect returncode.
_probe = subprocess.run(  # noqa: S603,S607 — fixed args, repo root only
    ["git", "-C", str(REPO_ROOT), "rev-parse", "--git-dir"],
    capture_output=True, text=True, check=False,
)
has_git_repo = _probe.returncode == 0

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not has_git_repo,
        reason="No git repo at repo root (or git binary missing)",
    ),
]


class TestGitSignalsAgainstRealRepo:
    def test_runs_without_error_against_repo(self) -> None:
        signals = extract_git_quality_signals(
            sessions=[],
            repo_dir=REPO_ROOT,
            proximity_days=7,
            lookback_days=90,
        )
        # Don't assert signal count — depends on real commit timing.
        # Just assert the list is well-formed.
        for sig in signals:
            assert sig.signal_type == SignalType.FEAT_FIX_PROXIMITY
            assert sig.agent_type is None
            assert "feat_commit" in sig.detail
            assert "fix_commits" in sig.detail
            assert isinstance(sig.detail["days_between"], int)
            assert sig.detail["days_between"] >= 0
            assert sig.detail["session_used_reviewer"] is None  # sessions=[]
