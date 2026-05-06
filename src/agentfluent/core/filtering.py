"""Date-range filter for ``SessionInfo`` lists.

Single source of truth for timezone normalization, half-open interval
semantics, and the missing-timestamp policy used by every CLI surface
that accepts ``--since`` / ``--until``.
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

    Half-open interval per D024/D025: a session is included when
    ``since <= first_message_timestamp < until``. ``None`` bounds are
    open-ended.

    **Missing-timestamp policy.** Sessions where
    ``first_message_timestamp is None`` are excluded when *either* bound
    is set — a session with no derivable start time cannot be placed
    inside or outside a window safely, and silently including it would
    distort the filtered result. With both bounds ``None`` the input
    list is returned unchanged (identity, not a copy — caller must not
    mutate) so unfiltered callers pay no policy cost.

    **Timezone normalization.** All comparisons happen in UTC. Naive
    datetimes are assumed to be UTC — matching the JSONL ``timestamp``
    field's ISO-8601-with-Z format. Note this differs from
    ``timeutil.parse_datetime``, which assumes naive CLI input is local
    time; both are correct for their respective contracts.
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
