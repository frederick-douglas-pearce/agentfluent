"""Unit tests for ``core.filtering.filter_sessions_by_time`` (#301).

Covers each branch of the half-open interval ``[since, until)``,
None-timestamp policy, identity behavior with both bounds open, and
mixed-timezone inputs (which must not raise ``TypeError``).
"""

from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

from agentfluent.core.discovery import SessionInfo
from agentfluent.core.filtering import filter_sessions_by_time


def _session(
    name: str,
    ts: datetime | None,
) -> SessionInfo:
    """Build a minimal ``SessionInfo`` for filter tests."""
    return SessionInfo(
        filename=f"{name}.jsonl",
        path=Path(f"/tmp/{name}.jsonl"),
        size_bytes=0,
        modified=datetime(2026, 1, 1, tzinfo=UTC),
        first_message_timestamp=ts,
    )


_BASE = datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)


class TestIdentityBehavior:
    """Both bounds ``None`` returns the input list unchanged, including
    sessions with ``first_message_timestamp is None``."""

    def test_no_bounds_returns_input_unchanged(self) -> None:
        sessions = [
            _session("a", _BASE),
            _session("b", _BASE + timedelta(hours=1)),
        ]
        result = filter_sessions_by_time(sessions)
        assert result == sessions

    def test_no_bounds_includes_none_timestamp_sessions(self) -> None:
        sessions = [_session("a", None), _session("b", _BASE)]
        result = filter_sessions_by_time(sessions)
        assert result == sessions

    def test_empty_input_no_bounds(self) -> None:
        assert filter_sessions_by_time([]) == []


class TestSinceOnly:
    """``since`` set, ``until=None`` → ``first_message_timestamp >= since``."""

    def test_includes_sessions_at_or_after_since(self) -> None:
        sessions = [
            _session("before", _BASE - timedelta(hours=1)),
            _session("at_boundary", _BASE),
            _session("after", _BASE + timedelta(hours=1)),
        ]
        result = filter_sessions_by_time(sessions, since=_BASE)
        names = [s.filename for s in result]
        assert "before.jsonl" not in names
        assert "at_boundary.jsonl" in names
        assert "after.jsonl" in names

    def test_excludes_none_timestamp_when_since_set(self) -> None:
        sessions = [_session("a", None), _session("b", _BASE)]
        result = filter_sessions_by_time(sessions, since=_BASE)
        assert [s.filename for s in result] == ["b.jsonl"]


class TestUntilOnly:
    """``until`` set, ``since=None`` → strict ``first_message_timestamp < until``."""

    def test_excludes_session_at_until_boundary(self) -> None:
        sessions = [
            _session("before", _BASE - timedelta(minutes=1)),
            _session("at_boundary", _BASE),  # excluded: half-open
            _session("after", _BASE + timedelta(hours=1)),
        ]
        result = filter_sessions_by_time(sessions, until=_BASE)
        names = [s.filename for s in result]
        assert "before.jsonl" in names
        assert "at_boundary.jsonl" not in names
        assert "after.jsonl" not in names

    def test_excludes_none_timestamp_when_until_set(self) -> None:
        sessions = [_session("a", None), _session("b", _BASE - timedelta(hours=1))]
        result = filter_sessions_by_time(sessions, until=_BASE)
        assert [s.filename for s in result] == ["b.jsonl"]


class TestBothBounds:
    """Both bounds → half-open ``[since, until)``."""

    def test_only_sessions_in_window(self) -> None:
        since = _BASE
        until = _BASE + timedelta(hours=2)
        sessions = [
            _session("before_since", since - timedelta(minutes=1)),
            _session("at_since", since),
            _session("middle", since + timedelta(hours=1)),
            _session("at_until", until),  # excluded by half-open
            _session("after_until", until + timedelta(minutes=1)),
        ]
        result = filter_sessions_by_time(sessions, since=since, until=until)
        names = [s.filename for s in result]
        assert names == ["at_since.jsonl", "middle.jsonl"]

    def test_window_with_no_matches_returns_empty(self) -> None:
        sessions = [
            _session("a", _BASE - timedelta(days=10)),
            _session("b", _BASE - timedelta(days=9)),
        ]
        result = filter_sessions_by_time(
            sessions, since=_BASE, until=_BASE + timedelta(days=1),
        )
        assert result == []

    def test_window_includes_all(self) -> None:
        sessions = [
            _session("a", _BASE),
            _session("b", _BASE + timedelta(hours=1)),
        ]
        result = filter_sessions_by_time(
            sessions,
            since=_BASE - timedelta(days=1),
            until=_BASE + timedelta(days=1),
        )
        assert len(result) == 2

    def test_excludes_none_timestamp_with_both_bounds(self) -> None:
        sessions = [_session("a", None), _session("b", _BASE)]
        result = filter_sessions_by_time(
            sessions,
            since=_BASE - timedelta(hours=1),
            until=_BASE + timedelta(hours=1),
        )
        assert [s.filename for s in result] == ["b.jsonl"]


class TestTimezoneNormalization:
    """Mixed aware/naive inputs and non-UTC offsets all normalize to UTC
    before comparison; no ``TypeError`` from cross-timezone subtraction."""

    def test_naive_session_timestamp_treated_as_utc(self) -> None:
        # Parser contract: JSONL ``timestamp`` field is ISO 8601 UTC; if
        # an upstream caller hands us a naive datetime, we assume UTC.
        naive_ts = datetime(2026, 5, 1, 12, 0, 0)  # no tzinfo
        sessions = [_session("naive", naive_ts)]
        # Aware bounds bracket the equivalent UTC instant.
        result = filter_sessions_by_time(
            sessions,
            since=_BASE - timedelta(minutes=1),
            until=_BASE + timedelta(minutes=1),
        )
        assert len(result) == 1

    def test_naive_bounds_treated_as_utc(self) -> None:
        naive_since = datetime(2026, 5, 1, 11, 0, 0)
        naive_until = datetime(2026, 5, 1, 13, 0, 0)
        sessions = [_session("a", _BASE)]  # 12:00 UTC
        result = filter_sessions_by_time(
            sessions, since=naive_since, until=naive_until,
        )
        assert len(result) == 1

    def test_non_utc_aware_bounds(self) -> None:
        # since=11:00 in UTC+5 (== 06:00 UTC), so 12:00 UTC is inside.
        plus_five = timezone(timedelta(hours=5))
        since = datetime(2026, 5, 1, 11, 0, 0, tzinfo=plus_five)  # 06:00 UTC
        until = datetime(2026, 5, 1, 20, 0, 0, tzinfo=plus_five)  # 15:00 UTC
        sessions = [_session("a", _BASE)]
        result = filter_sessions_by_time(sessions, since=since, until=until)
        assert len(result) == 1

    def test_non_utc_aware_session_timestamp(self) -> None:
        # Session ts in UTC-7 (== 19:00 UTC); window in UTC.
        minus_seven = timezone(timedelta(hours=-7))
        ts = datetime(2026, 5, 1, 12, 0, 0, tzinfo=minus_seven)  # 19:00 UTC
        sessions = [_session("a", ts)]
        result = filter_sessions_by_time(
            sessions,
            since=datetime(2026, 5, 1, 18, 0, 0, tzinfo=UTC),
            until=datetime(2026, 5, 1, 20, 0, 0, tzinfo=UTC),
        )
        assert len(result) == 1


class TestEmptyInput:
    """Empty input list with bounds returns empty; no exception."""

    def test_empty_input_with_since(self) -> None:
        assert filter_sessions_by_time([], since=_BASE) == []

    def test_empty_input_with_both_bounds(self) -> None:
        assert filter_sessions_by_time(
            [], since=_BASE, until=_BASE + timedelta(days=1),
        ) == []
