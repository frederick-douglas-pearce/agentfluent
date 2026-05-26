"""Tier 3 GitHub-API quality signals.

Per-PR quality signals computed from GitHub API responses, fetched
via the :mod:`agentfluent.github` infrastructure. Mirrors the Tier 2
:mod:`agentfluent.diagnostics.git_signals` pattern: one public
extractor per signal type, each returning a tuple of
``(signals, tier3_degraded)`` so the pipeline can flip
:attr:`DiagnosticsResult.tier3_degraded` when a ``gh api`` call hits
a rate limit.

**Off by default.** Runs only when the CLI passes ``--github``,
which in turn passes ``github_repo`` to :func:`run_diagnostics`.
AgentFluent should not silently call GitHub on every analyze run.

API budget per session (worst case, no cache hits): one
``commits/{sha}/pulls`` call per commit in the session window, plus
two calls (``pulls/{N}/commits`` and ``commits/{sha}/status``) per
unique PR. 50 sessions touching 5 PRs each at one commit per session
is comfortably under the 5000/hour authenticated rate limit; the
file-backed cache (:mod:`agentfluent.github.cache`) brings the
amortized cost much lower across repeat runs.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple

from agentfluent.config.models import Severity
from agentfluent.diagnostics._git_helpers import _GitCommit, _run_git_log
from agentfluent.diagnostics.models import DiagnosticSignal, SignalType
from agentfluent.github import (
    TTL_OPEN_PR_OR_CI,
    GitHubRepo,
    RateLimitedError,
    gh_api,
)

if TYPE_CHECKING:
    from agentfluent.analytics.pipeline import SessionAnalysis

logger = logging.getLogger(__name__)

# How far back to scan local git history when looking up commits to
# attribute to sessions. Same default as Tier 2 (#275).
DEFAULT_LOOKBACK_DAYS = 90

# Grace period appended to ``session.end_time`` when deciding whether a
# commit "belongs" to that session. Five minutes covers the common
# case of a deferred ``git commit`` after the session's last message;
# longer slack risks pulling in commits from a subsequent session.
DEFAULT_COMMIT_SLACK_SEC = 300

# GitHub's combined status endpoint reports one of: success, failure,
# error, pending. Both ``failure`` and ``error`` indicate a CI miss
# the agent should have caught — we treat them identically here.
_CI_FAILURE_STATES: frozenset[str] = frozenset({"failure", "error"})


class _PRRef(NamedTuple):
    """Minimal PR identifier — the fields we need for attribution and
    for the signal's ``detail`` payload. Materialized from the
    ``commits/{sha}/pulls`` response."""

    number: int
    title: str
    url: str


def extract_ci_failure_first_push_signals(
    sessions: list[SessionAnalysis],
    *,
    github_repo: GitHubRepo,
    repo_dir: Path,
    no_cache: bool = False,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    commit_slack_seconds: int = DEFAULT_COMMIT_SLACK_SEC,
) -> tuple[list[DiagnosticSignal], bool]:
    """Emit one ``CI_FAILURE_FIRST_PUSH`` signal per PR whose first
    commit failed CI and was authored within an analyzed session.

    Args:
        sessions: All sessions in the analysis window. Used to map
            commits back to the session that authored them.
        github_repo: The GitHub repository being analyzed.
        repo_dir: Local git working tree. Used to enumerate commits
            in the lookback window (no API call cost).
        no_cache: Forwarded to :func:`gh_api`. Skips cache reads but
            still writes — next run picks up the fresh entries.
        lookback_days: How far back to scan ``git log``. Defaults to
            90 days; matches Tier 2.
        commit_slack_seconds: Grace appended to each session's end
            time when attributing commits.

    Returns:
        ``(signals, degraded)``. ``degraded`` is ``True`` when at
        least one ``gh api`` call hit a rate limit; partial signals
        are still returned (per-extractor degradation policy from
        the spike spec section 3).

    Force-push caveat: ``pulls/{N}/commits`` returns the CURRENT
    first commit, so a PR that's been force-pushed loses its
    historical first-push CI failure. This is accepted as a v0.8
    limitation; surfacing the original first push would require the
    PR events API and significantly more complexity.
    """
    since = datetime.now().astimezone() - timedelta(days=lookback_days)
    commits = _run_git_log(repo_dir, since=since)
    if not commits:
        return [], False

    session_windows = _build_session_windows(
        sessions, slack=commit_slack_seconds,
    )
    if not session_windows:
        return [], False

    commit_to_session = _attribute_commits(commits, session_windows)
    if not commit_to_session:
        return [], False

    degraded = False
    pr_refs: dict[int, _PRRef] = {}
    for sha in commit_to_session:
        result, hit_limit = _fetch_prs_for_commit(
            github_repo, sha, no_cache=no_cache,
        )
        if hit_limit:
            degraded = True
            continue
        for ref in result:
            pr_refs.setdefault(ref.number, ref)

    signals: list[DiagnosticSignal] = []
    for ref in pr_refs.values():
        first_sha, status, hit_limit = _fetch_pr_first_status(
            github_repo, ref.number, no_cache=no_cache,
        )
        if hit_limit:
            degraded = True
            continue
        if first_sha is None or status is None:
            continue
        if status.get("state") not in _CI_FAILURE_STATES:
            continue
        # Only emit when the first commit is attributable to one of
        # our sessions — the signal is about a specific agent's miss,
        # so a first commit authored outside any analyzed session
        # has no meaningful attribution.
        attributed = commit_to_session.get(first_sha)
        if attributed is None:
            continue
        failing = [
            s for s in (status.get("statuses") or [])
            if s.get("state") in _CI_FAILURE_STATES
        ]
        if not failing:
            # Defensive: combined state says failure but no individual
            # context does. Skip rather than emit a vague signal.
            continue
        signals.append(_build_signal(ref, first_sha, failing))

    return signals, degraded


def _build_session_windows(
    sessions: list[SessionAnalysis], *, slack: int,
) -> dict[int, tuple[datetime, datetime]]:
    """Map ``id(session) → (earliest, latest + slack)`` for sessions
    that carry at least one timestamped message.

    Sessions without any timestamped messages are excluded — we have
    no basis for attributing commits to them. ``id(session)`` is the
    key so the windows survive being iterated alongside a parallel
    ``commit_to_session`` map that uses identity comparisons.
    """
    windows: dict[int, tuple[datetime, datetime]] = {}
    for session in sessions:
        timestamps = [
            m.timestamp for m in session.messages if m.timestamp is not None
        ]
        if not timestamps:
            continue
        windows[id(session)] = (
            min(timestamps),
            max(timestamps) + timedelta(seconds=slack),
        )
    return windows


def _attribute_commits(
    commits: list[_GitCommit],
    session_windows: dict[int, tuple[datetime, datetime]],
) -> dict[str, int]:
    """Map ``commit.sha → id(session)`` for commits whose timestamp
    falls inside a session's window. First-match wins.

    Sessions rarely overlap in time, so a commit lands in exactly one
    window in practice. When they do overlap, the iteration order of
    ``session_windows`` is stable (Python 3.7+ dict insertion order)
    so the earlier-built window wins.
    """
    attributed: dict[str, int] = {}
    for commit in commits:
        for sess_id, (start, end) in session_windows.items():
            if start <= commit.timestamp <= end:
                attributed[commit.sha] = sess_id
                break
    return attributed


def _fetch_prs_for_commit(
    github_repo: GitHubRepo, sha: str, *, no_cache: bool,
) -> tuple[list[_PRRef], bool]:
    """Return the PRs that include the given commit SHA.

    Returns ``(refs, rate_limited)``. ``rate_limited`` short-circuits
    further calls for this commit but doesn't poison the run — other
    commits' fetches still proceed; the caller flags ``degraded``.
    """
    endpoint = f"repos/{github_repo.owner}/{github_repo.repo}/commits/{sha}/pulls"
    # Wrap in `[...]` so a multi-PR result lands as a single JSON
    # array we can parse with one ``json.loads`` rather than newline-
    # delimited objects (gh's default when ``.[]`` is the top-level).
    jq = "[.[] | {number, title, html_url}]"
    try:
        payload = gh_api(
            endpoint,
            jq_filter=jq,
            cache_ttl=TTL_OPEN_PR_OR_CI,
            no_cache=no_cache,
        )
    except RateLimitedError as e:
        logger.warning(
            "CI_FAILURE_FIRST_PUSH rate-limited at %s; resets at %s",
            e.endpoint, e.reset_at,
        )
        return [], True
    except RuntimeError:
        logger.debug("commits/%s/pulls failed; skipping", sha, exc_info=True)
        return [], False
    if not isinstance(payload, list):
        return [], False
    refs: list[_PRRef] = []
    for item in payload:
        try:
            refs.append(_PRRef(
                number=int(item["number"]),
                title=str(item.get("title") or ""),
                url=str(item.get("html_url") or ""),
            ))
        except (KeyError, TypeError, ValueError):
            logger.debug("malformed PR entry from %s: %r", endpoint, item)
            continue
    return refs, False


def _fetch_pr_first_status(
    github_repo: GitHubRepo, pr_number: int, *, no_cache: bool,
) -> tuple[str | None, dict[str, Any] | None, bool]:
    """Fetch the first commit on a PR and its combined CI status.

    Returns ``(first_sha, status_dict, rate_limited)``. Either of the
    first two will be ``None`` on a non-rate-limit failure (malformed
    response, empty PR, etc.); the third indicates whether the
    caller should set ``degraded=True`` for the run.
    """
    owner = github_repo.owner
    repo = github_repo.repo
    commits_endpoint = f"repos/{owner}/{repo}/pulls/{pr_number}/commits"
    # Project to just the SHA per commit — payload stays tiny.
    commits_jq = "[.[] | {sha}]"
    try:
        commits_payload = gh_api(
            commits_endpoint,
            jq_filter=commits_jq,
            cache_ttl=TTL_OPEN_PR_OR_CI,
            no_cache=no_cache,
        )
    except RateLimitedError as e:
        logger.warning(
            "CI_FAILURE_FIRST_PUSH rate-limited at %s; resets at %s",
            e.endpoint, e.reset_at,
        )
        return None, None, True
    except RuntimeError:
        logger.debug(
            "pulls/%d/commits failed; skipping", pr_number, exc_info=True,
        )
        return None, None, False
    if not isinstance(commits_payload, list) or not commits_payload:
        return None, None, False
    try:
        first_sha = str(commits_payload[0]["sha"])
    except (KeyError, TypeError):
        return None, None, False

    status_endpoint = f"repos/{owner}/{repo}/commits/{first_sha}/status"
    status_jq = "{state, statuses: [.statuses[] | {context, state}]}"
    try:
        status_payload = gh_api(
            status_endpoint,
            jq_filter=status_jq,
            cache_ttl=TTL_OPEN_PR_OR_CI,
            no_cache=no_cache,
        )
    except RateLimitedError as e:
        logger.warning(
            "CI_FAILURE_FIRST_PUSH rate-limited at %s; resets at %s",
            e.endpoint, e.reset_at,
        )
        return None, None, True
    except RuntimeError:
        logger.debug(
            "commits/%s/status failed; skipping", first_sha, exc_info=True,
        )
        return None, None, False
    if not isinstance(status_payload, dict):
        return None, None, False
    return first_sha, status_payload, False


def _build_signal(
    pr: _PRRef,
    first_commit_sha: str,
    failing_contexts: list[dict[str, Any]],
) -> DiagnosticSignal:
    """Construct the ``CI_FAILURE_FIRST_PUSH`` signal for one PR.

    ``agent_type=None`` matches the Tier 2 ``FEAT_FIX_PROXIMITY``
    convention: the signal is cross-cutting (about a PR's first
    push), not attributable to a single subagent type. Per-agent
    attribution can be revisited in v0.8.1+ once we have dogfood
    data on which attribution shape produces actionable advice.
    """
    primary = failing_contexts[0]
    primary_context = str(primary.get("context") or "ci")
    primary_state = str(primary.get("state") or "failure")
    title_disp = pr.title if pr.title else "(no title)"
    message = (
        f"PR #{pr.number} ({title_disp!r}) first push failed CI: "
        f"{primary_context} {primary_state}"
    )
    return DiagnosticSignal(
        signal_type=SignalType.CI_FAILURE_FIRST_PUSH,
        severity=Severity.WARNING,
        agent_type=None,
        invocation_id=None,
        message=message,
        detail={
            "pr_number": pr.number,
            "pr_title": pr.title,
            "pr_url": pr.url,
            "first_commit_sha": first_commit_sha,
            "failing_contexts": [
                {"context": str(c.get("context") or ""),
                 "state": str(c.get("state") or "")}
                for c in failing_contexts
            ],
            "primary_context": primary_context,
            "primary_state": primary_state,
        },
    )
