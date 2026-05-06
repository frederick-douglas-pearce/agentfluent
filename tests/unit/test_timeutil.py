"""Tests for ``core.timeutil.parse_datetime``.

Tests construct known-offset datetimes in their assertions rather
than mutating ``TZ`` env vars + ``time.tzset()``, which is fragile
on non-glibc platforms and not safe under ``pytest-xdist``. At least
one test exercises a non-UTC timezone so the ``astimezone()`` path
is actually covered.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from agentfluent.core.timeutil import parse_datetime


class TestRelative:
    """Relative ``Nd`` / ``Nh`` / ``Nm`` durations subtract from
    ``now`` (which is injected for determinism)."""

    NOW = datetime(2026, 5, 5, 12, 0, 0, tzinfo=UTC)

    def test_days(self) -> None:
        assert parse_datetime("7d", now=self.NOW) == self.NOW - timedelta(days=7)

    def test_hours(self) -> None:
        assert parse_datetime("12h", now=self.NOW) == self.NOW - timedelta(hours=12)

    def test_minutes(self) -> None:
        assert parse_datetime("30m", now=self.NOW) == self.NOW - timedelta(minutes=30)

    def test_zero_days_returns_now(self) -> None:
        assert parse_datetime("0d", now=self.NOW) == self.NOW

    def test_naive_now_treated_as_utc(self) -> None:
        naive = datetime(2026, 5, 5, 12, 0, 0)
        result = parse_datetime("1d", now=naive)
        assert result == datetime(2026, 5, 4, 12, 0, 0, tzinfo=UTC)

    def test_default_now_is_recent_utc(self) -> None:
        before = datetime.now(UTC)
        result = parse_datetime("0d")
        after = datetime.now(UTC)
        assert before <= result <= after


class TestIso8601WithTimezone:
    """ISO 8601 with explicit timezone offset is unambiguous —
    no platform-local-tz interaction."""

    def test_utc_zero_offset(self) -> None:
        assert parse_datetime("2026-05-05T12:00:00+00:00") == datetime(
            2026, 5, 5, 12, 0, 0, tzinfo=UTC,
        )

    def test_positive_offset_normalized_to_utc(self) -> None:
        # 12:00 at +05:00 is 07:00 UTC.
        assert parse_datetime("2026-05-05T12:00:00+05:00") == datetime(
            2026, 5, 5, 7, 0, 0, tzinfo=UTC,
        )

    def test_negative_offset_normalized_to_utc(self) -> None:
        # 12:00 at -08:00 is 20:00 UTC same day.
        assert parse_datetime("2026-05-05T12:00:00-08:00") == datetime(
            2026, 5, 5, 20, 0, 0, tzinfo=UTC,
        )

    def test_non_utc_zone_exercises_astimezone_path(self) -> None:
        """Construct the same wall-clock instant in a non-UTC zone via
        the test fixture and confirm equality after parse + UTC convert."""
        la = ZoneInfo("America/Los_Angeles")
        # 2026-05-05 05:00 PDT == 12:00 UTC (PDT is UTC-7 in May).
        expected = datetime(2026, 5, 5, 5, 0, 0, tzinfo=la).astimezone(UTC)
        assert parse_datetime("2026-05-05T05:00:00-07:00") == expected


class TestIso8601Naive:
    """Naive ISO 8601 (no timezone) — interpreted as system local
    time, converted to UTC. Asserts use the system's actual offset
    rather than mutating ``TZ`` env to avoid xdist races."""

    def test_round_trips_via_local_tz(self) -> None:
        naive = "2026-05-05T12:00:00"
        # Build the expected value the same way the function does:
        # treat the naive datetime as system local, convert to UTC.
        expected = datetime(2026, 5, 5, 12, 0, 0).astimezone().astimezone(UTC)
        assert parse_datetime(naive) == expected
        assert parse_datetime(naive).tzinfo == UTC


class TestDateOnly:
    """Date-only inputs: midnight in system local time, converted
    to UTC. On Python 3.12 this hits ``datetime.fromisoformat``
    directly (returning midnight) rather than the date.fromisoformat
    fallback — both paths produce identical results."""

    def test_midnight_in_local_tz(self) -> None:
        result = parse_datetime("2026-05-05")
        # Build the expected value the same way: midnight local -> UTC.
        expected = datetime(2026, 5, 5, 0, 0, 0).astimezone().astimezone(UTC)
        assert result == expected
        assert result.tzinfo == UTC

    def test_returns_utc_aware(self) -> None:
        assert parse_datetime("2026-01-01").tzinfo == UTC


class TestErrors:
    """Bad inputs produce ``ValueError`` with the offending value and
    the format hint embedded in the message so callers can surface it
    directly (Typer/Click error display)."""

    def test_unparseable_string_raises(self) -> None:
        with pytest.raises(ValueError, match="last tuesday"):
            parse_datetime("last tuesday")

    def test_error_message_lists_accepted_formats(self) -> None:
        with pytest.raises(ValueError, match="ISO 8601"):
            parse_datetime("not a date")

    def test_error_message_disambiguates_minutes(self) -> None:
        """``m = minutes`` is called out so users do not type ``3m``
        expecting months."""
        with pytest.raises(ValueError, match="m = minutes"):
            parse_datetime("not a date")

    def test_empty_string_raises(self) -> None:
        with pytest.raises(ValueError, match="Empty"):
            parse_datetime("")

    def test_whitespace_only_raises(self) -> None:
        with pytest.raises(ValueError, match="Empty"):
            parse_datetime("   ")

    def test_negative_relative_rejected(self) -> None:
        # The regex requires ``\d+`` so ``-7d`` does not match
        # the relative pattern; it then fails ISO parsing.
        with pytest.raises(ValueError):
            parse_datetime("-7d")

    def test_unknown_relative_unit_rejected(self) -> None:
        # ``M`` (uppercase, sometimes used for months elsewhere) is
        # not in _RELATIVE_UNITS — falls through to ISO parse, which
        # also fails.
        with pytest.raises(ValueError):
            parse_datetime("3M")

    def test_partial_relative_rejected(self) -> None:
        with pytest.raises(ValueError):
            parse_datetime("7days")


class TestStripping:
    """Leading and trailing whitespace is stripped before parsing."""

    def test_leading_whitespace(self) -> None:
        result = parse_datetime("  2026-05-05T12:00:00+00:00")
        assert result == datetime(2026, 5, 5, 12, 0, 0, tzinfo=UTC)

    def test_trailing_whitespace(self) -> None:
        result = parse_datetime("2026-05-05T12:00:00+00:00  ")
        assert result == datetime(2026, 5, 5, 12, 0, 0, tzinfo=UTC)

    def test_relative_with_whitespace(self) -> None:
        now = datetime(2026, 5, 5, 12, 0, 0, tzinfo=UTC)
        assert parse_datetime("  7d  ", now=now) == now - timedelta(days=7)
