"""Shared low-level git-log helpers for diagnostics signal extractors.

Used by both Tier 2 (:mod:`agentfluent.diagnostics.git_signals` —
``FEAT_FIX_PROXIMITY``) and Tier 3
(:mod:`agentfluent.diagnostics.github_signals` —
``CI_FAILURE_FIRST_PUSH``, ``PR_REVIEW_COMMENT_DENSITY``). Both tiers
need to enumerate commits in a time window from the project's git
working tree; the parsing and subprocess shape lives here so the two
extractors don't import each other's privates.

This module is intentionally tier-agnostic: nothing in here knows
about Conventional Commits, PR numbers, or signal types. It's pure
"give me commits with their timestamps and touched files."

All git invocations use stdlib :mod:`subprocess` with a bounded
timeout and graceful degradation: a missing git binary, a non-repo
dir, an empty window, or a timeout all return ``[]`` rather than
raising. Callers do not need a try/except wrapper.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# ASCII record separators chosen because they don't appear in commit
# subjects, paths, or ISO timestamps. ``%x1e`` separates fields within
# a commit; ``%x1f`` separates commits.
_GIT_LOG_FORMAT = "%H%x1e%cI%x1e%s"
_GIT_LOG_COMMIT_SEPARATOR = "\x1f"
_GIT_LOG_FIELD_SEPARATOR = "\x1e"

# Subprocess timeout. A real `git log --name-only` over 90 days
# finishes in well under a second on healthy repos; 30s is generous
# headroom for slow filesystems / huge repos without making an honest
# hang invisible.
_GIT_TIMEOUT_SEC = 30

# Default ``git log --since`` lookback window in days. Shared by all
# diagnostics extractors that scan local git history (Tier 2
# ``git_signals.FEAT_FIX_PROXIMITY``, Tier 3
# ``github_signals.CI_FAILURE_FIRST_PUSH``). Defined here so a single
# tuning change applies to every tier; per-extractor overrides remain
# possible via the ``lookback_days`` parameter on each public entry
# point.
DEFAULT_LOOKBACK_DAYS = 90


@dataclass(frozen=True)
class _GitCommit:
    """One parsed entry from ``git log --format=...``.

    ``timestamp`` is the committer date (``%cI``) as an aware
    :class:`datetime`. ``files`` is a (possibly empty) set of paths
    touched by the commit, populated when ``--name-only`` was passed.
    """

    sha: str
    timestamp: datetime
    subject: str
    files: frozenset[str] = field(default_factory=frozenset)


def _run_git_log(
    repo_dir: Path, *, since: datetime,
) -> tuple[list[_GitCommit], bool]:
    """Run ``git log --since=<since>`` and parse the output.

    Returns ``(commits, ok)``:

    - ``commits`` is the parsed list (possibly empty when the window
      is genuinely empty).
    - ``ok`` is ``True`` when the git subprocess completed
      successfully (returncode 0), ``False`` on any failure: missing
      git binary, non-repo directory, timeout, or non-zero exit.

    Pre-fix this returned just the list, conflating "no commits in
    window" with "git failed entirely" — Tier 3 callers couldn't
    detect git misconfiguration and reported a clean run with zero
    signals. The tuple return lets callers (especially
    :func:`agentfluent.diagnostics.github_signals._enumerate_attributed_prs`)
    flip ``tier3_degraded`` when git itself is broken vs. when the
    repo simply has no recent commits.

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
        logger.debug("git binary not found on PATH; returning empty commit list")
        return [], False
    except subprocess.TimeoutExpired:
        logger.warning(
            "git log timed out after %ds; returning empty commit list",
            _GIT_TIMEOUT_SEC,
        )
        return [], False

    if result.returncode != 0:
        # Not a repo, or another git error. Stderr is human-readable
        # but we don't want to surface it on every run — DEBUG only.
        logger.debug(
            "git log returned %d: %s",
            result.returncode, result.stderr.strip(),
        )
        return [], False

    return _parse_commits(result.stdout), True


def _parse_commits(stdout: str) -> list[_GitCommit]:
    """Parse the structured ``git log`` output into :class:`_GitCommit` records.

    Output shape (with our separators):

        \\x1f<sha>\\x1e<isoformat-cdate>\\x1e<subject>
        <file1>
        <file2>
        \\x1f<sha>\\x1e<isoformat-cdate>\\x1e<subject>
        <file1>
        ...

    The leading ``\\x1f`` makes ``split`` cleanly produce one entry
    per commit (the first entry is empty and gets filtered).
    """
    commits: list[_GitCommit] = []
    for raw_entry in stdout.split(_GIT_LOG_COMMIT_SEPARATOR):
        entry = raw_entry.strip()
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
