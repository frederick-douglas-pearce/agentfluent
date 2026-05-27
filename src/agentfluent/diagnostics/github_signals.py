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

# PR_REVIEW_COMMENT_DENSITY defaults. All configurable via the
# public extractor's kwargs.
#
# ``density_threshold`` is the floor above which we emit an INFO
# signal. ``warning_multiplier`` doubles the threshold for the WARNING
# tier (per the issue body: "INFO ... or WARNING if density is 2x
# threshold"). ``min_lines_changed`` suppresses noisy small-PR signals
# (a single comment on a 3-line PR gives density 0.33, which would
# fire spuriously); the issue body explicitly suggests a 20-line gate.
DEFAULT_DENSITY_THRESHOLD = 0.1
DEFAULT_WARNING_MULTIPLIER = 2.0
DEFAULT_MIN_LINES_CHANGED = 20


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
    pr_refs, commit_to_session, degraded = _enumerate_attributed_prs(
        sessions,
        github_repo=github_repo,
        repo_dir=repo_dir,
        no_cache=no_cache,
        lookback_days=lookback_days,
        commit_slack_seconds=commit_slack_seconds,
        signal_name="CI_FAILURE_FIRST_PUSH",
    )
    if not pr_refs and not commit_to_session:
        return [], degraded

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
        signals.append(_build_ci_failure_signal(ref, first_sha, failing))

    if skipped_unattributable:
        logger.info(
            "CI_FAILURE_FIRST_PUSH: skipped %d PR(s) with unattributable "
            "first commits (pre-lookback, squash/force-push, or session "
            "without timestamped messages)",
            skipped_unattributable,
        )

    return signals, degraded


def extract_pr_review_comment_density_signals(
    sessions: list[SessionAnalysis],
    *,
    github_repo: GitHubRepo,
    repo_dir: Path,
    no_cache: bool = False,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    commit_slack_seconds: int = DEFAULT_COMMIT_SLACK_SEC,
    density_threshold: float = DEFAULT_DENSITY_THRESHOLD,
    warning_multiplier: float = DEFAULT_WARNING_MULTIPLIER,
    min_lines_changed: int = DEFAULT_MIN_LINES_CHANGED,
) -> tuple[list[DiagnosticSignal], bool]:
    """Emit one ``PR_REVIEW_COMMENT_DENSITY`` signal per PR where the
    external review-comment density crosses the threshold.

    "Density" = external inline comments per line changed, where
    ``lines_changed = additions + deletions``. "External" excludes
    self-reviews (comments authored by the same user who opened the
    PR) — the signal measures the reviewer effort an agent-side
    review subagent could have shortcut, not the author's own
    self-annotation.

    Severity is ``INFO`` at and above ``density_threshold``,
    upgraded to ``WARNING`` at ``density_threshold *
    warning_multiplier``. Below the threshold (or below the
    ``min_lines_changed`` gate, or with zero external comments) no
    signal is emitted.

    Returns ``(signals, degraded)``. ``degraded`` is True when at
    least one ``gh api`` call hit a rate limit or recoverable error
    (transient 5xx, malformed JSON, mid-run auth lapse); partial
    signals are still returned (per-extractor degradation policy
    from the spike spec section 3).

    Endpoint choice: the issue body suggests "use the ``reviews``
    endpoint which returns both" inline + body comments, but the
    GitHub API surface disagrees — ``/pulls/{N}/reviews`` returns
    review-level objects (state, body, user) without per-review
    inline counts. We use ``/pulls/{N}/comments`` directly for the
    inline-comment list, plus ``/pulls/{N}`` for size and author.
    Two API calls per PR, matching the spike's budget estimate.
    """
    pr_refs, _commit_to_session, degraded = _enumerate_attributed_prs(
        sessions,
        github_repo=github_repo,
        repo_dir=repo_dir,
        no_cache=no_cache,
        lookback_days=lookback_days,
        commit_slack_seconds=commit_slack_seconds,
        signal_name="PR_REVIEW_COMMENT_DENSITY",
    )
    if not pr_refs:
        return [], degraded

    signals: list[DiagnosticSignal] = []
    for ref in pr_refs.values():
        detail, hit_limit_or_error = _fetch_pr_detail(
            github_repo, ref.number, no_cache=no_cache,
        )
        if hit_limit_or_error:
            degraded = True
            continue
        if detail is None:
            continue
        lines_changed = int(detail.get("additions") or 0) + int(
            detail.get("deletions") or 0,
        )
        if lines_changed < min_lines_changed:
            continue
        author = str(((detail.get("user") or {}).get("login")) or "")

        external_count, hit_limit_or_error = _fetch_external_comment_count(
            github_repo, ref.number, author=author, no_cache=no_cache,
        )
        if hit_limit_or_error:
            degraded = True
            continue
        if external_count <= 0:
            continue
        density = external_count / max(lines_changed, 1)
        if density < density_threshold:
            continue
        # Prefer the freshly-fetched title (the PR-detail call gives
        # us the current title, while the helper's _PRRef.title may
        # be stale due to setdefault first-write-wins across multiple
        # commit/{sha}/pulls responses).
        title = str(detail.get("title") or ref.title)
        url = str(detail.get("html_url") or ref.url)
        signals.append(_build_density_signal(
            pr_number=ref.number,
            pr_title=title,
            pr_url=url,
            author=author,
            additions=int(detail.get("additions") or 0),
            deletions=int(detail.get("deletions") or 0),
            external_count=external_count,
            density=density,
            density_threshold=density_threshold,
            warning_multiplier=warning_multiplier,
        ))

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


def _enumerate_attributed_prs(
    sessions: list[SessionAnalysis],
    *,
    github_repo: GitHubRepo,
    repo_dir: Path,
    no_cache: bool,
    lookback_days: int,
    commit_slack_seconds: int,
    signal_name: str,
) -> tuple[dict[int, _PRRef], dict[str, int], bool]:
    """Enumerate PRs touched by sessions in the analysis window.

    Shared first half of every per-PR Tier 3 extractor:

    1. ``git log --since`` to enumerate commits in the lookback window
       (local; no API call cost).
    2. Build per-session ``(start, end + slack)`` windows from each
       session's timestamped messages.
    3. Attribute commits to sessions by timestamp containment
       (first-match wins on overlap).
    4. For each session-attributed commit, fetch the PRs that
       include it via ``gh api commits/{sha}/pulls`` and dedup by
       PR number.

    Returns ``(pr_refs, commit_to_session, degraded)``:

    - ``pr_refs`` maps each unique PR number to a minimal
      :class:`_PRRef`. Empty when no PRs match.
    - ``commit_to_session`` maps each session-attributed commit SHA
      to ``id(session)``. Empty when no commits were attributable.
    - ``degraded`` is True when at least one ``commits/{sha}/pulls``
      fetch hit a rate limit or recoverable gh error. The caller
      forwards this to the per-extractor ``degraded`` accumulator.

    ``signal_name`` is forwarded to the per-call log helpers so
    warnings name the calling signal honestly. Architect note 1 in
    the #401 implementation-plan review (PR comment) called this
    out: pre-fix, the rate-limit log message hardcoded
    ``CI_FAILURE_FIRST_PUSH`` even when triggered by another
    signal's call path.
    """
    since = datetime.now().astimezone() - timedelta(days=lookback_days)
    commits = _run_git_log(repo_dir, since=since)
    if not commits:
        return {}, {}, False

    session_windows = _build_session_windows(
        sessions, slack=commit_slack_seconds,
    )
    if not session_windows:
        return {}, {}, False

    commit_to_session = _attribute_commits(commits, session_windows)
    if not commit_to_session:
        return {}, {}, False

    degraded = False
    pr_refs: dict[int, _PRRef] = {}
    for sha in commit_to_session:
        result, hit_limit = _fetch_prs_for_commit(
            github_repo, sha, no_cache=no_cache, signal_name=signal_name,
        )
        if hit_limit:
            degraded = True
            continue
        for ref in result:
            pr_refs.setdefault(ref.number, ref)
    return pr_refs, commit_to_session, degraded


def _log_rate_limit(e: RateLimitedError, *, signal_name: str) -> None:
    """Single-source rate-limit WARNING — keeps message format stable
    across every ``gh_api`` call site in this module.

    ``signal_name`` is required so the WARNING attribution is honest
    when shared helpers (``_fetch_prs_for_commit``,
    ``_enumerate_attributed_prs``) are called from multiple signal
    extractors. Pre-fix the helper hardcoded ``CI_FAILURE_FIRST_PUSH``,
    which would silently misattribute the rate-limit to one signal
    when triggered by another.
    """
    logger.warning(
        "%s rate-limited at %s; resets at %s",
        signal_name, e.endpoint, e.reset_at,
    )


def _fetch_prs_for_commit(
    github_repo: GitHubRepo, sha: str, *, no_cache: bool, signal_name: str,
) -> tuple[list[_PRRef], bool]:
    """Return the PRs that include the given commit SHA.

    Returns ``(refs, rate_limited_or_recoverable_error)``. The boolean
    flag is set both for explicit rate limits and for the broader
    family of recoverable gh errors (transient 5xx, malformed JSON,
    auth lapses mid-run). Treating recoverable errors as degradation
    rather than silent skip lets the caller flip ``tier3_degraded``
    so the user knows their results may be incomplete.

    ``signal_name`` is forwarded to log messages so rate-limit /
    degraded warnings name the correct signal — this helper is
    shared by every per-PR extractor (via
    :func:`_enumerate_attributed_prs`), so hardcoding a name would
    misattribute warnings when one signal's call path triggers a
    rate limit that affects another.
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
        _log_rate_limit(e, signal_name=signal_name)
        return [], True
    except _GH_RECOVERABLE as e:
        logger.warning(
            "%s: commits/%s/pulls failed (%s); marking Tier 3 degraded",
            signal_name, sha, type(e).__name__,
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
        _log_rate_limit(e, signal_name="CI_FAILURE_FIRST_PUSH")
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
        _log_rate_limit(e, signal_name="CI_FAILURE_FIRST_PUSH")
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
        _log_rate_limit(e, signal_name="CI_FAILURE_FIRST_PUSH")
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


def _build_ci_failure_signal(
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


def _fetch_pr_detail(
    github_repo: GitHubRepo, pr_number: int, *, no_cache: bool,
) -> tuple[dict[str, Any] | None, bool]:
    """Fetch the PR detail object projected to the fields we need.

    Returns ``(detail, hit_limit_or_error)``. ``detail`` is a dict
    carrying ``additions``, ``deletions``, ``user.login``, ``title``,
    ``html_url`` — or ``None`` when the projection failed to parse.
    The boolean covers both rate-limit and recoverable-error paths
    (the caller flips ``tier3_degraded`` either way).
    """
    endpoint = (
        f"repos/{github_repo.owner}/{github_repo.repo}/pulls/{pr_number}"
    )
    jq = "{additions, deletions, user: {login}, title, html_url}"
    try:
        payload = gh_api(
            endpoint,
            jq_filter=jq,
            cache_ttl=TTL_OPEN_PR_OR_CI,
            no_cache=no_cache,
        )
    except RateLimitedError as e:
        _log_rate_limit(e, signal_name="PR_REVIEW_COMMENT_DENSITY")
        return None, True
    except _GH_RECOVERABLE as e:
        logger.warning(
            "PR_REVIEW_COMMENT_DENSITY: pulls/%d failed (%s); "
            "marking Tier 3 degraded",
            pr_number, type(e).__name__,
        )
        return None, True
    if not isinstance(payload, dict):
        return None, False
    return payload, False


def _fetch_external_comment_count(
    github_repo: GitHubRepo,
    pr_number: int,
    *,
    author: str,
    no_cache: bool,
) -> tuple[int, bool]:
    """Count inline review comments on a PR, excluding self-reviews.

    The ``author`` parameter is the PR author's GitHub login;
    comments whose ``user.login`` equals it are filtered out so the
    density signal measures *external* review effort rather than the
    author's own self-annotation.

    Empty / unrecognized author (None / empty string) disables the
    filter — better to count everyone than to silently exclude
    nobody when we couldn't resolve the author.

    Returns ``(count, hit_limit_or_error)``. The boolean covers both
    rate-limit and recoverable-error paths.
    """
    endpoint = (
        f"repos/{github_repo.owner}/{github_repo.repo}"
        f"/pulls/{pr_number}/comments"
    )
    jq = "[.[] | {user: {login}}]"
    try:
        payload = gh_api(
            endpoint,
            jq_filter=jq,
            cache_ttl=TTL_OPEN_PR_OR_CI,
            no_cache=no_cache,
        )
    except RateLimitedError as e:
        _log_rate_limit(e, signal_name="PR_REVIEW_COMMENT_DENSITY")
        return 0, True
    except _GH_RECOVERABLE as e:
        logger.warning(
            "PR_REVIEW_COMMENT_DENSITY: pulls/%d/comments failed (%s); "
            "marking Tier 3 degraded",
            pr_number, type(e).__name__,
        )
        return 0, True
    if not isinstance(payload, list):
        return 0, False
    count = 0
    for item in payload:
        if not isinstance(item, dict):
            continue
        login = str(((item.get("user") or {}).get("login")) or "")
        if author and login == author:
            # Self-review: the PR author commenting on their own
            # diff. The density signal measures EXTERNAL reviewer
            # effort, so skip.
            continue
        count += 1
    return count, False


def _build_density_signal(
    *,
    pr_number: int,
    pr_title: str,
    pr_url: str,
    author: str,
    additions: int,
    deletions: int,
    external_count: int,
    density: float,
    density_threshold: float,
    warning_multiplier: float,
) -> DiagnosticSignal:
    """Construct one ``PR_REVIEW_COMMENT_DENSITY`` signal.

    ``agent_type=None`` matches the Tier 2 ``FEAT_FIX_PROXIMITY``
    and Tier 3 ``CI_FAILURE_FIRST_PUSH`` conventions: the signal is
    cross-cutting (about a PR's reviewer-effort cost), not
    attributable to a single subagent type. Per-agent attribution
    can be revisited in v0.8.1+.

    Severity tiers:

    - ``WARNING`` when ``density >= density_threshold * warning_multiplier``
      — the PR pulled significant reviewer effort, strongly suggesting
      an architect / code-review subagent could have caught issues
      pre-PR.
    - ``INFO`` at or above ``density_threshold`` — the PR pulled
      enough reviewer effort to be worth noting, but not so much it
      demands action.
    """
    lines_changed = additions + deletions
    title_disp = pr_title if pr_title else "(no title)"
    severity = (
        Severity.WARNING
        if density >= density_threshold * warning_multiplier
        else Severity.INFO
    )
    comment_word = "comment" if external_count == 1 else "comments"
    line_word = "line" if lines_changed == 1 else "lines"
    message = (
        f"PR #{pr_number} ({title_disp!r}) received {external_count} "
        f"external review {comment_word} across {lines_changed} "
        f"{line_word} changed (density: {density:.2f})"
    )
    return DiagnosticSignal(
        signal_type=SignalType.PR_REVIEW_COMMENT_DENSITY,
        severity=severity,
        agent_type=None,
        invocation_id=None,
        message=message,
        detail={
            "pr_number": pr_number,
            "pr_title": pr_title,
            "pr_url": pr_url,
            "author": author,
            "additions": additions,
            "deletions": deletions,
            "lines_changed": lines_changed,
            "external_comment_count": external_count,
            "density": density,
            "threshold": density_threshold,
            "warning_multiplier": warning_multiplier,
        },
    )
