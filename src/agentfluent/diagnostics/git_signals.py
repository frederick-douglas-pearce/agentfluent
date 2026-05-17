"""Local-git quality signal extraction (Tier 2 / opt-in).

Sibling to ``quality_signals.py`` (parent-message-driven) and
``trace_signals.py`` (subagent-trace-driven). This module mines the
local git history for ``feat:`` commits followed within a window by
``fix:`` commits touching the same files, then correlates each pair
back to the session that produced the ``feat:`` commit to check
whether a review-style subagent was used.

The signal is "this feature shipped with quality issues that a
reviewer might have caught" — strong evidence when the session did
*not* use a review subagent.

**Off by default.** Runs only when the CLI passes ``--git`` (which
in turn passes ``git_repo`` to :func:`run_diagnostics`). AgentFluent
should not silently shell out to git on every analyze run.

All git invocations use stdlib :mod:`subprocess` with bounded timeout
and graceful degradation: a missing git binary, a non-repo dir, an
empty window, or a timeout all return ``[]`` rather than raising.
The diagnostics pipeline never crashes because the user opted into
``--git`` from a non-repo working dir.
"""

from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from agentfluent.config.models import Severity
from agentfluent.diagnostics.models import DiagnosticSignal, SignalType
from agentfluent.diagnostics.quality_signals import REVIEW_AGENT_TYPES

if TYPE_CHECKING:
    from agentfluent.analytics.pipeline import SessionAnalysis

logger = logging.getLogger(__name__)

# Default lookback window for `git log --since`. A pair only counts as
# "proximity" when feat and fix are within this many days of each other,
# but we also need to cap how far back the initial scan reaches — pulling
# the entire repo history on every run is wasteful and the signal value
# decays fast with age.
DEFAULT_PROXIMITY_DAYS = 7
DEFAULT_LOOKBACK_DAYS = 90

# Match the ``feat:`` / ``fix:`` Conventional Commits prefix on the
# commit subject. ``feat(scope):`` and ``feat!:`` both count. Other
# prefixes (``docs:``, ``chore:``, ``test:``) are deliberately ignored
# — they are not "shipped features" and don't tell us anything about
# quality misses.
_FEAT_PATTERN = re.compile(r"^feat(\([^)]*\))?!?:", re.IGNORECASE)
_FIX_PATTERN = re.compile(r"^fix(\([^)]*\))?!?:", re.IGNORECASE)

# ASCII record separators chosen because they don't appear in commit
# subjects, paths, or ISO timestamps. The same format is parsed inline
# by :func:`_parse_commits`. ``%x1e`` separates fields within a commit;
# ``%x1f`` separates commits.
_GIT_LOG_FORMAT = "%H%x1e%cI%x1e%s"
_GIT_LOG_COMMIT_SEPARATOR = "\x1f"
_GIT_LOG_FIELD_SEPARATOR = "\x1e"

# Subprocess timeout. A real `git log --name-only` over 90 days finishes
# in well under a second on healthy repos; 30s is generous headroom for
# slow filesystems / huge repos without making an honest hang invisible.
_GIT_TIMEOUT_SEC = 30


@dataclass(frozen=True)
class _GitCommit:
    """One parsed entry from ``git log --format=...``."""

    sha: str
    timestamp: datetime
    subject: str
    files: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class _FeatFixPair:
    """A `feat:` commit followed by one or more `fix:` commits within
    the proximity window that share at least one file with the feat."""

    feat: _GitCommit
    fixes: tuple[_GitCommit, ...]
    shared_files: frozenset[str]

    @property
    def days_between(self) -> int:
        latest_fix = max(f.timestamp for f in self.fixes)
        return (latest_fix - self.feat.timestamp).days


def extract_git_quality_signals(
    sessions: list[SessionAnalysis],
    *,
    repo_dir: Path,
    proximity_days: int = DEFAULT_PROXIMITY_DAYS,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> list[DiagnosticSignal]:
    """Find feat-then-fix proximity pairs in ``repo_dir`` and emit signals.

    Each ``feat:`` commit in the last ``lookback_days`` is paired with
    any ``fix:`` commit within ``proximity_days`` that shares at least
    one file. Each pair correlates back to the session whose last
    message precedes the feat commit, and the signal carries a
    ``session_used_reviewer`` boolean (or ``None`` if no session
    matches) so downstream consumers can distinguish "no reviewer was
    used" (strong signal, WARNING) from "reviewer was used but issue
    slipped" (weaker signal, INFO).

    Returns ``[]`` on any non-fatal git error (no binary, not a repo,
    empty window, timeout). The caller does not need a try/except.
    """
    since = datetime.now().astimezone() - timedelta(days=lookback_days)
    commits = _run_git_log(repo_dir, since=since)
    if not commits:
        return []

    pairs = _find_feat_fix_pairs(commits, proximity_days=proximity_days)
    if not pairs:
        return []

    return [_signal_for_pair(pair, sessions) for pair in pairs]


def _run_git_log(repo_dir: Path, *, since: datetime) -> list[_GitCommit]:
    """Run ``git log`` and parse the output. Returns ``[]`` on any error.

    The subprocess invocation is bounded by :data:`_GIT_TIMEOUT_SEC`
    and uses a fixed-shape ``--format`` that pairs cleanly with
    ``--name-only`` so file paths land below each commit header.
    """
    cmd = [
        "git", "-C", str(repo_dir),
        "log",
        f"--since={since.isoformat()}",
        f"--format={_GIT_LOG_COMMIT_SEPARATOR}{_GIT_LOG_FORMAT}",
        "--name-only",
    ]
    try:
        result = subprocess.run(  # noqa: S603 — args are constants, not user input
            cmd,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SEC,
            check=False,
        )
    except FileNotFoundError:
        logger.debug("git binary not found on PATH; skipping git signals")
        return []
    except subprocess.TimeoutExpired:
        logger.warning("git log timed out after %ds; skipping git signals", _GIT_TIMEOUT_SEC)
        return []

    if result.returncode != 0:
        # Not a repo, or another git error. Stderr is human-readable
        # but we don't want to surface it on every run — DEBUG only.
        logger.debug(
            "git log returned %d: %s",
            result.returncode, result.stderr.strip(),
        )
        return []

    return _parse_commits(result.stdout)


def _parse_commits(stdout: str) -> list[_GitCommit]:
    """Parse the structured ``git log`` output into ``_GitCommit`` records.

    Output shape (with our separators):

        \\x1f<sha>\\x1e<isoformat-cdate>\\x1e<subject>
        <file1>
        <file2>
        \\x1f<sha>\\x1e<isoformat-cdate>\\x1e<subject>
        <file1>
        ...

    The leading ``\\x1f`` makes ``split`` cleanly produce one entry per
    commit (the first entry is empty and gets filtered).
    """
    commits: list[_GitCommit] = []
    for entry in stdout.split(_GIT_LOG_COMMIT_SEPARATOR):
        entry = entry.strip()
        if not entry:
            continue
        # First line is the header; subsequent lines are file paths.
        header, _, file_block = entry.partition("\n")
        parts = header.split(_GIT_LOG_FIELD_SEPARATOR)
        if len(parts) != 3:
            continue
        sha, timestamp_str, subject = parts
        try:
            timestamp = datetime.fromisoformat(timestamp_str)
        except ValueError:
            continue
        files = frozenset(
            line.strip() for line in file_block.splitlines() if line.strip()
        )
        commits.append(_GitCommit(
            sha=sha, timestamp=timestamp, subject=subject, files=files,
        ))
    return commits


def _find_feat_fix_pairs(
    commits: list[_GitCommit], *, proximity_days: int,
) -> list[_FeatFixPair]:
    """Pair each ``feat:`` commit with subsequent in-window ``fix:`` commits
    that share at least one file. Commits are sorted chronologically."""
    sorted_commits = sorted(commits, key=lambda c: c.timestamp)
    feats = [c for c in sorted_commits if _FEAT_PATTERN.match(c.subject)]
    fixes = [c for c in sorted_commits if _FIX_PATTERN.match(c.subject)]

    pairs: list[_FeatFixPair] = []
    for feat in feats:
        window_end = feat.timestamp + timedelta(days=proximity_days)
        matching_fixes: list[_GitCommit] = []
        shared: set[str] = set()
        for fix in fixes:
            if fix.timestamp <= feat.timestamp:
                continue
            if fix.timestamp > window_end:
                continue
            overlap = feat.files & fix.files
            if not overlap:
                continue
            matching_fixes.append(fix)
            shared.update(overlap)
        if matching_fixes:
            pairs.append(_FeatFixPair(
                feat=feat,
                fixes=tuple(matching_fixes),
                shared_files=frozenset(shared),
            ))
    return pairs


def _signal_for_pair(
    pair: _FeatFixPair, sessions: list[SessionAnalysis],
) -> DiagnosticSignal:
    """Build the ``FEAT_FIX_PROXIMITY`` signal for one pair."""
    used_reviewer = _session_used_reviewer(pair.feat.timestamp, sessions)

    severity = (
        Severity.INFO if used_reviewer is True else Severity.WARNING
    )

    fix_count = len(pair.fixes)
    fix_word = "fix" if fix_count == 1 else "fixes"
    message = (
        f"feat {pair.feat.sha[:7]} followed by {fix_count} {fix_word} "
        f"on shared file(s) within {pair.days_between}d"
    )

    return DiagnosticSignal(
        signal_type=SignalType.FEAT_FIX_PROXIMITY,
        severity=severity,
        agent_type=None,
        invocation_id=None,
        message=message,
        detail={
            "feat_commit": {
                "sha": pair.feat.sha,
                "subject": pair.feat.subject,
                "timestamp": pair.feat.timestamp.isoformat(),
                "files": sorted(pair.feat.files),
            },
            "fix_commits": [
                {
                    "sha": fix.sha,
                    "subject": fix.subject,
                    "timestamp": fix.timestamp.isoformat(),
                    "files": sorted(fix.files),
                }
                for fix in pair.fixes
            ],
            "days_between": pair.days_between,
            "shared_files": sorted(pair.shared_files),
            "session_used_reviewer": used_reviewer,
        },
    )


def _session_used_reviewer(
    feat_timestamp: datetime, sessions: list[SessionAnalysis],
) -> bool | None:
    """Find the session whose last message precedes ``feat_timestamp``
    most closely; return whether any of its invocations used a
    review-style subagent.

    Returns ``None`` when no session matches — the typical case for
    commits made outside any AgentFluent-analyzed session window.
    """
    best_session: SessionAnalysis | None = None
    best_end: datetime | None = None
    for session in sessions:
        end = _session_end(session)
        if end is None or end > feat_timestamp:
            continue
        if best_end is None or end > best_end:
            best_end = end
            best_session = session
    if best_session is None:
        return None
    return any(
        inv.agent_type.lower() in REVIEW_AGENT_TYPES
        for inv in best_session.invocations
    )


def _session_end(session: SessionAnalysis) -> datetime | None:
    """Latest message timestamp in the session, or ``None`` if none has one."""
    timestamps = [m.timestamp for m in session.messages if m.timestamp is not None]
    return max(timestamps) if timestamps else None
