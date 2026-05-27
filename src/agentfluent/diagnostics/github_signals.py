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
from agentfluent.diagnostics._git_helpers import (
    DEFAULT_LOOKBACK_DAYS,
    _GitCommit,
    _run_git_log,
)
from agentfluent.diagnostics.models import DiagnosticSignal, SignalType
from agentfluent.github import (
    TTL_OPEN_PR_OR_CI,
    GhNotAuthenticatedError,
    GhNotInstalledError,
    GitHubRepo,
    RateLimitedError,
    gh_api,
)

if TYPE_CHECKING:
    from agentfluent.analytics.pipeline import SessionAnalysis

logger = logging.getLogger(__name__)

# Grace period appended to ``session.end_time`` when deciding whether a
# commit "belongs" to that session. Five minutes covers the common
# case of a deferred ``git commit`` after the session's last message;
# longer slack risks pulling in commits from a subsequent session.
DEFAULT_COMMIT_SLACK_SEC = 300

# GitHub's combined status endpoint reports one of: success, failure,
# error, pending. Both ``failure`` and ``error`` indicate a CI miss
# the agent should have caught — we treat them identically here.
# Also used to filter Check Runs conclusions (``failure``,
# ``timed_out``, ``action_required``, ``cancelled`` all surface as
# real CI misses; the legacy state field uses the narrower set).
_CI_FAILURE_STATES: frozenset[str] = frozenset({"failure", "error"})
_CI_FAILURE_CONCLUSIONS: frozenset[str] = frozenset(
    {"failure", "timed_out", "action_required", "cancelled"},
)

# Exceptions from ``agentfluent.github.gh_api`` that indicate a real
# but recoverable problem (transient server error, malformed JSON,
# auth lapse mid-run). The extractor flags ``tier3_degraded=True`` and
# skips the affected PR instead of crashing the whole diagnostics run.
# ``RateLimitedError`` is handled separately because it is the only
# class with structured reset-time data; everything else is bucketed.
_GH_RECOVERABLE: tuple[type[Exception], ...] = (
    RuntimeError,
    ValueError,
    GhNotInstalledError,
    GhNotAuthenticatedError,
)


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
        least one ``gh api`` call hit a rate limit OR a recoverable
        gh error (transient 5xx, malformed JSON, mid-run auth lapse);
        partial signals are still returned (per-extractor degradation
        policy from the spike spec section 3).

    Force-push caveat: ``pulls/{N}/commits`` returns the CURRENT
    first commit, so a PR that's been force-pushed loses its
    historical first-push CI failure. This is accepted as a v0.8
    limitation; surfacing the original first push would require the
    PR events API and significantly more complexity.

    GitHub Actions support: the legacy ``/commits/{sha}/status``
    endpoint only reports Statuses-API entries, NOT Check Runs (the
    modern API GitHub Actions uses). When the legacy status returns
    no failure but the commit was actually checked, we fall back to
    ``/commits/{sha}/check-runs`` so Actions-only repos still produce
    signals.
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
    skipped_unattributable = 0
    for ref in pr_refs.values():
        # Fetch the PR's first commit SHA first (cheap projection,
        # 1 API call). Attribution check uses the local
        # commit_to_session map (free) — so we can skip a wasted
        # status fetch when the first commit pre-dates the lookback
        # window or belongs to a session with no timestamped
        # messages. This saves 1 API call per non-attributable PR.
        first_sha, hit_limit = _fetch_pr_first_commit_sha(
            github_repo, ref.number, no_cache=no_cache,
        )
        if hit_limit:
            degraded = True
            continue
        if first_sha is None:
            continue

        attributed = commit_to_session.get(first_sha)
        if attributed is None:
            # First commit pre-dates lookback OR belongs to a
            # session with no timestamped messages OR was squashed/
            # force-pushed after our git-log snapshot. We can't
            # attribute the miss to a specific agent, so skip.
            skipped_unattributable += 1
            continue

        failing, hit_limit, hit_error = _fetch_failing_contexts(
            github_repo, first_sha, no_cache=no_cache,
        )
        if hit_limit or hit_error:
            degraded = True
            continue
        if not failing:
            continue
        signals.append(_build_signal(ref, first_sha, failing))

    if skipped_unattributable:
        logger.info(
            "CI_FAILURE_FIRST_PUSH: skipped %d PR(s) with unattributable "
            "first commits (pre-lookback, squash/force-push, or session "
            "without timestamped messages)",
            skipped_unattributable,
        )

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


def _log_rate_limit(e: RateLimitedError) -> None:
    """Single-source rate-limit WARNING — keeps message format stable
    across every gh_api call site in this module."""
    logger.warning(
        "CI_FAILURE_FIRST_PUSH rate-limited at %s; resets at %s",
        e.endpoint, e.reset_at,
    )


def _fetch_prs_for_commit(
    github_repo: GitHubRepo, sha: str, *, no_cache: bool,
) -> tuple[list[_PRRef], bool]:
    """Return the PRs that include the given commit SHA.

    Returns ``(refs, rate_limited_or_recoverable_error)``. The boolean
    flag is set both for explicit rate limits and for the broader
    family of recoverable gh errors (transient 5xx, malformed JSON,
    auth lapses mid-run). Treating recoverable errors as degradation
    rather than silent skip lets the caller flip ``tier3_degraded``
    so the user knows their results may be incomplete.
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
        _log_rate_limit(e)
        return [], True
    except _GH_RECOVERABLE as e:
        logger.warning(
            "commits/%s/pulls failed (%s); marking Tier 3 degraded",
            sha, type(e).__name__,
        )
        return [], True
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


def _fetch_pr_first_commit_sha(
    github_repo: GitHubRepo, pr_number: int, *, no_cache: bool,
) -> tuple[str | None, bool]:
    """Fetch just the first commit SHA on a PR.

    Returns ``(sha, hit_limit)`` where ``hit_limit`` is True on rate
    limit or recoverable error (caller flips ``tier3_degraded``).
    Split out from the legacy combined ``_fetch_pr_first_status`` so
    the caller can check session attribution against a free local map
    before paying for the status / check-runs fetch.

    Ordering note: GitHub's ``pulls/{N}/commits`` returns commits in
    topological order (oldest first), so ``[0]`` is the historical
    first commit on the PR — load-bearing for correctness. A future
    refactor must preserve this; consider asserting via per_page=1 if
    GitHub's ordering ever stops being contractually first-oldest.
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
        _log_rate_limit(e)
        return None, True
    except _GH_RECOVERABLE as e:
        logger.warning(
            "pulls/%d/commits failed (%s); marking Tier 3 degraded",
            pr_number, type(e).__name__,
        )
        return None, True
    if not isinstance(commits_payload, list) or not commits_payload:
        return None, False
    try:
        return str(commits_payload[0]["sha"]), False
    except (KeyError, TypeError):
        return None, False


def _fetch_failing_contexts(
    github_repo: GitHubRepo, sha: str, *, no_cache: bool,
) -> tuple[list[dict[str, str]], bool, bool]:
    """Return failing CI contexts for a commit.

    Composes two GitHub endpoints to cover both legacy Statuses and
    modern Check Runs (GitHub Actions):

    1. ``/commits/{sha}/status`` — the legacy combined status. Used
       first because it's the documented entry point and covers the
       common ``CircleCI / Travis / Statuses-API-via-App`` cases.
    2. ``/commits/{sha}/check-runs`` — the Check Runs API. Polled
       when the legacy endpoint reports no failure, because Actions
       checks do not appear in the Statuses combined state at all.

    Returns ``(failing, hit_limit, hit_error)``:

    - ``failing`` is a list of ``{context, state}`` dicts (one per
      failing CI run). Empty when no failure was detected.
    - ``hit_limit`` indicates a rate limit; caller flips
      ``tier3_degraded``.
    - ``hit_error`` indicates a recoverable gh error (transient,
      auth lapse, etc.); caller also flips ``tier3_degraded``.
    """
    owner = github_repo.owner
    repo = github_repo.repo

    # 1. Legacy combined status. Cheapest path for repos using the
    #    Statuses API (CircleCI, Travis, third-party CI Apps).
    status_endpoint = f"repos/{owner}/{repo}/commits/{sha}/status"
    status_jq = "{state, statuses: [.statuses[] | {context, state}]}"
    try:
        status_payload = gh_api(
            status_endpoint,
            jq_filter=status_jq,
            cache_ttl=TTL_OPEN_PR_OR_CI,
            no_cache=no_cache,
        )
    except RateLimitedError as e:
        _log_rate_limit(e)
        return [], True, False
    except _GH_RECOVERABLE as e:
        logger.warning(
            "commits/%s/status failed (%s); marking Tier 3 degraded",
            sha, type(e).__name__,
        )
        return [], False, True

    if isinstance(status_payload, dict):
        combined = status_payload.get("state")
        statuses = status_payload.get("statuses") or []
        if combined in _CI_FAILURE_STATES:
            failing = [
                {"context": str(s.get("context") or ""),
                 "state": str(s.get("state") or "")}
                for s in statuses
                if s.get("state") in _CI_FAILURE_STATES
            ]
            if failing:
                return failing, False, False

    # 2. Check Runs fallback. Only reached when the legacy endpoint
    #    reported no actionable failure — which is the dominant case
    #    for repos using GitHub Actions exclusively. Without this
    #    fallback the signal would have near-zero recall on modern
    #    GitHub repos.
    return _fetch_failing_check_runs(github_repo, sha, no_cache=no_cache)


def _fetch_failing_check_runs(
    github_repo: GitHubRepo, sha: str, *, no_cache: bool,
) -> tuple[list[dict[str, str]], bool, bool]:
    """Query Check Runs and return any with a failing conclusion.

    Same return shape as :func:`_fetch_failing_contexts`. Limited to
    the first 30 check-runs (gh default page size); PRs with more
    runs would need pagination, deferred to v0.8.1+ — the spike spec
    Section 4 notes this trade-off explicitly.
    """
    owner = github_repo.owner
    repo = github_repo.repo
    endpoint = f"repos/{owner}/{repo}/commits/{sha}/check-runs"
    # The API wraps runs in {total_count, check_runs: [...]}; project
    # to a flat list of {name, conclusion} dicts so we can re-use
    # ``failing_contexts``-shape downstream.
    jq = "[.check_runs[] | {name, conclusion}]"
    try:
        payload = gh_api(
            endpoint,
            jq_filter=jq,
            cache_ttl=TTL_OPEN_PR_OR_CI,
            no_cache=no_cache,
        )
    except RateLimitedError as e:
        _log_rate_limit(e)
        return [], True, False
    except _GH_RECOVERABLE as e:
        logger.warning(
            "commits/%s/check-runs failed (%s); marking Tier 3 degraded",
            sha, type(e).__name__,
        )
        return [], False, True

    if not isinstance(payload, list):
        return [], False, False

    failing: list[dict[str, str]] = []
    for run in payload:
        if not isinstance(run, dict):
            continue
        conclusion = str(run.get("conclusion") or "")
        if conclusion not in _CI_FAILURE_CONCLUSIONS:
            continue
        failing.append({
            "context": str(run.get("name") or ""),
            # Normalize the Check Runs conclusion to a Statuses-API
            # "state" so downstream consumers (correlator,
            # signal.detail) see a single vocabulary regardless of
            # which endpoint sourced the failure.
            "state": "failure",
        })
    return failing, False, False


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
