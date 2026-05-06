"""Session-level filter utilities (#301).

Single source of truth for date-range filtering of ``SessionInfo``
lists, consumed by ``list --since/--until`` (#296), ``analyze
--since/--until`` (#297), and forward-compatibly by per-session
diagnostics (#201, deferred to v0.7). Centralizing the filter here
prevents drift between three call sites that would otherwise each
reimplement timezone normalization, half-open interval semantics, and
the missing-timestamp policy.

Pure module: no I/O, no logging, no exceptions for empty results. The
caller decides what to render for "no sessions matched."
"""

from __future__ import annotations

from datetime import UTC, datetime

from agentfluent.core.discovery import SessionInfo


def filter_sessions_by_time(
    sessions: list[SessionInfo],
    since: datetime | None = None,
    until: datetime | None = None,
) -> list[SessionInfo]:
    """Return ``sessions`` whose ``first_message_timestamp`` falls in ``[since, until)``.

    Half-open interval per D024/D025 and the v0.6 PRD: a session is
    included when ``since <= first_message_timestamp < until``. ``None``
    bounds are open-ended.

    **Missing-timestamp policy.** Sessions where
    ``first_message_timestamp is None`` (empty file, unreadable file,
    no parseable timestamps) are excluded when *either* bound is set —
    a session with no derivable start time cannot be placed inside or
    outside a window safely, and silently including it would distort
    the filtered result. With both bounds ``None`` (the identity case),
    the input list is returned unchanged including any None-timestamp
    sessions, so callers that don't filter pay no policy cost.

    **Timezone normalization.** All comparisons happen in UTC. Naive
    datetimes (no ``tzinfo``) are assumed to be UTC — matching the
    JSONL ``timestamp`` field's ISO-8601-with-Z format and
    ``timeutil.parse_datetime``'s post-conversion contract — so mixed
    aware/naive inputs do not raise ``TypeError``.
    """
    if since is None and until is None:
        return sessions

    since_utc = _to_utc(since) if since is not None else None
    until_utc = _to_utc(until) if until is not None else None

    result: list[SessionInfo] = []
    for session in sessions:
        ts = session.first_message_timestamp
        if ts is None:
            continue
        ts_utc = _to_utc(ts)
        if since_utc is not None and ts_utc < since_utc:
            continue
        if until_utc is not None and ts_utc >= until_utc:
            continue
        result.append(session)
    return result


def _to_utc(dt: datetime) -> datetime:
    """Normalize ``dt`` to a UTC-aware ``datetime``.

    Naive inputs are assumed UTC (matches the JSONL parser's contract
    for the ``timestamp`` field). Aware inputs are converted via
    ``astimezone``.
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)
