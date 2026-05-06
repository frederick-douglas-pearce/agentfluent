"""Datetime parsing utilities for CLI inputs.

Sibling to ``parser.parse_timestamp``, which handles JSONL-ingestion
timestamps (always ISO 8601, may carry a trailing ``Z``, returns
``None`` on failure). This module handles user-typed CLI inputs for
the ``--since`` / ``--until`` flags introduced in v0.6 (#293):
relative durations (``7d`` / ``12h`` / ``30m``), date-only strings,
and ISO 8601 datetimes with or without timezone. Naive inputs are
interpreted as the system local timezone, then converted to UTC, so
the rest of the pipeline can compare against the UTC-aware
``SessionInfo.first_message_timestamp`` field added in #294.

Stdlib only — no external date-parsing dependencies. Single function,
``parse_datetime``, with optional ``now`` injection for testability.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

_RELATIVE_PATTERN = re.compile(r"^(\d+)([dhm])$")
_RELATIVE_UNITS: dict[str, str] = {"d": "days", "h": "hours", "m": "minutes"}

# Disambiguates ``m`` as minutes (not months) in the error message —
# the worst failure mode would be a user typing ``3m`` meaning months
# and silently getting "3 minutes ago".
_ACCEPTED_FORMATS_HINT = (
    "Accepted formats: ISO 8601 (e.g., 2026-05-05T12:00:00 or "
    "2026-05-05T12:00:00+00:00), date-only (2026-05-05), or relative "
    "(7d = days, 12h = hours, 30m = minutes)."
)


def parse_datetime(
    value: str, *, now: datetime | None = None,
) -> datetime:
    """Parse a user-supplied datetime, returning a UTC-aware ``datetime``.

    Format precedence (first match wins):

    1. **Relative**: ``Nd`` / ``Nh`` / ``Nm`` → N units before ``now``.
       Negative durations are not accepted (the regex requires ``\\d+``).
    2. **ISO 8601 datetime**: with or without timezone. Naive inputs are
       interpreted as system local time, then converted to UTC.
    3. **Date-only**: ISO 8601 ``YYYY-MM-DD`` → start of day in system
       local time, converted to UTC.

    On Python 3.12+, ``datetime.fromisoformat`` accepts the date-only
    form directly (returning midnight), so the explicit ``date.fromisoformat``
    fallback is rarely reached in practice — kept for forward
    compatibility with hypothetical future inputs that ``fromisoformat``
    rejects but ``date.fromisoformat`` accepts.

    Naive inputs falling on a DST transition resolve per the platform's
    ``localtime()`` rules — deterministic but not portable. Users who
    need unambiguous behavior should pass an ISO 8601 string with an
    explicit timezone offset.

    ``now`` is injected for testability; defaults to ``datetime.now(UTC)``.
    Naive ``now`` values are treated as UTC.

    Raises:
        ValueError: When the input is empty, whitespace-only, or does
            not match any accepted format. The error message includes
            the offending value and a list of accepted formats.
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Empty datetime value. {_ACCEPTED_FORMATS_HINT}")
    value = value.strip()

    if rel := _RELATIVE_PATTERN.match(value):
        amount = int(rel.group(1))
        unit_key = _RELATIVE_UNITS[rel.group(2)]
        anchor = now if now is not None else datetime.now(UTC)
        if anchor.tzinfo is None:
            anchor = anchor.replace(tzinfo=UTC)
        return anchor - timedelta(**{unit_key: amount})

    try:
        dt = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(
            f"Could not parse datetime: {value!r}. {_ACCEPTED_FORMATS_HINT}",
        ) from exc

    # Naive ISO 8601 (datetime or date-only-as-midnight) — attach the
    # system's local timezone before converting to UTC so the user's
    # mental model ("the wall-clock time on my machine") is preserved.
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return dt.astimezone(UTC)
