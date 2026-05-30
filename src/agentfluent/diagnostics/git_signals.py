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

**Calibration (v0.8, #402).** On the agentfluent dogfood corpus
(34 pairs, 90-day window) the v0.7 default thresholds yielded
**58.8% precision** (20 TP / 14 FP). The dominant false-positive
mode was single-file coincidental overlap with broad-impact fix
commits (a schema or UX fix touching widely-used files getting
paired with unrelated feats). Raising the per-fix overlap threshold
from 1 to 2 *code* files (excluding ``.md`` / ``.yaml`` / ``.yml``
paths) raises precision to **76.2%** (16 TP / 5 FP / 21 kept) with
~20% recall loss. See ``.claude/specs/analysis/402-calibration/``
for the per-pair classification rubric and methodology.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from agentfluent.config.models import Severity
from agentfluent.diagnostics._git_helpers import (
    _GIT_LOG_COMMIT_SEPARATOR,
    _GIT_LOG_FIELD_SEPARATOR,
    _GIT_LOG_FORMAT,
    _GIT_TIMEOUT_SEC,
    DEFAULT_LOOKBACK_DAYS,
    _GitCommit,
    _parse_commits,
    _run_git_log,
)
from agentfluent.diagnostics.models import DiagnosticSignal, SignalType
from agentfluent.diagnostics.quality_signals import REVIEW_AGENT_TYPES

if TYPE_CHECKING:
    from pathlib import Path

    from agentfluent.analytics.pipeline import SessionAnalysis

logger = logging.getLogger(__name__)

# Re-exports for back-compat — :mod:`agentfluent.diagnostics._git_helpers`
# is the canonical home for the moved git-log primitives. Tests that
# imported these private symbols from this module continue to work;
# new callers should import them from ``_git_helpers`` directly. Also
# includes locally-defined private symbols (``_FeatFixPair``,
# ``_find_feat_fix_pairs``, ``_FEAT_PATTERN``, ``_FIX_PATTERN``) that
# tests import directly from this module.
__all__ = [
    "DEFAULT_LOOKBACK_DAYS",
    "DEFAULT_PROXIMITY_DAYS",
    "_FEAT_PATTERN",
    "_FIX_PATTERN",
    "_GIT_LOG_COMMIT_SEPARATOR",
    "_GIT_LOG_FIELD_SEPARATOR",
    "_GIT_LOG_FORMAT",
    "_GIT_TIMEOUT_SEC",
    "_FeatFixPair",
    "_GitCommit",
    "_find_feat_fix_pairs",
    "_parse_commits",
    "_run_git_log",
    "extract_git_quality_signals",
]

# Default proximity window for feat-fix pairing. A pair only counts
# when feat and fix are within this many days of each other. The
# lookback window (how far back the initial scan reaches) is shared
# with Tier 3 via :data:`agentfluent.diagnostics._git_helpers.DEFAULT_LOOKBACK_DAYS`.
DEFAULT_PROXIMITY_DAYS = 7

# Match the ``feat:`` / ``fix:`` Conventional Commits prefix on the
# commit subject. ``feat(scope):`` and ``feat!:`` both count. Other
# prefixes (``docs:``, ``chore:``, ``test:``) are deliberately ignored
# — they are not "shipped features" and don't tell us anything about
# quality misses.
_FEAT_PATTERN = re.compile(r"^feat(\([^)]*\))?!?:", re.IGNORECASE)
_FIX_PATTERN = re.compile(r"^fix(\([^)]*\))?!?:", re.IGNORECASE)

# Files that do NOT count toward the per-fix code-overlap threshold.
# Documentation paths (Markdown, YAML) are co-edited frequently with
# unrelated commits — GLOSSARY.md grows whenever a new signal lands,
# terms.yaml whenever a term is renamed — so counting them toward the
# overlap inflated the signal's false-positive rate (#402 calibration).
_OVERLAP_EXCLUDED_EXTENSIONS: frozenset[str] = frozenset({".md", ".yaml", ".yml"})

# Minimum number of code files a single fix must share with a feat for
# the pair to count. Raised from 1 to 2 in v0.8 after the #402
# calibration showed single-file overlap was the dominant FP mode.
# Applied per-fix inside the inner loop of :func:`_find_feat_fix_pairs`
# — NOT on the accumulated ``shared`` set — so two single-file-overlap
# fixes can't combine to clear the bar even though neither individually
# has strong evidence (architect review on #402, 2026-05-29).
_MIN_CODE_FILE_OVERLAP = 2


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
    # _run_git_log now returns (commits, ok); Tier 2 silently skips
    # on either git failure or empty window — the original behavior
    # is preserved by collapsing both cases to an empty return.
    commits, _ok = _run_git_log(repo_dir, since=since)
    if not commits:
        return []

    pairs = _find_feat_fix_pairs(commits, proximity_days=proximity_days)
    if not pairs:
        return []

    return [_signal_for_pair(pair, sessions) for pair in pairs]


def _find_feat_fix_pairs(
    commits: list[_GitCommit], *, proximity_days: int,
) -> list[_FeatFixPair]:
    """Pair each ``feat:`` commit with subsequent in-window ``fix:`` commits
    that share at least :data:`_MIN_CODE_FILE_OVERLAP` code files (per
    fix). Commits are sorted chronologically. The threshold check runs
    inside the inner loop so each fix is evaluated on its own merits —
    two single-file-overlap fixes can't combine to clear the bar.
    See module docstring for the calibration result behind these
    thresholds (#402, v0.8)."""
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
            code_overlap_count = sum(1 for f in overlap if _is_code_file(f))
            if code_overlap_count < _MIN_CODE_FILE_OVERLAP:
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


def _is_code_file(path: str) -> bool:
    """Whether ``path`` counts toward the code-overlap threshold for
    FEAT_FIX_PROXIMITY pairing. Files matching
    :data:`_OVERLAP_EXCLUDED_EXTENSIONS` (Markdown / YAML) are filtered
    out — they co-edit frequently with unrelated commits and previously
    drove the signal's FP rate (#402 calibration)."""
    return not any(path.endswith(ext) for ext in _OVERLAP_EXCLUDED_EXTENSIONS)


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
