"""Tests for ``agentfluent.cli._time_args.parse_time_window``."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
import typer
from rich.console import Console

from agentfluent.cli._time_args import parse_time_window
from agentfluent.cli.exit_codes import EXIT_USER_ERROR


@pytest.fixture()
def err_console() -> Console:
    """Stderr console with a wide width so error messages don't wrap mid-substring."""
    return Console(stderr=True, width=200)


class TestHappyPath:
    def test_both_none_returns_none_pair(self, err_console: Console) -> None:
        since, until = parse_time_window(None, None, err_console=err_console)
        assert since is None
        assert until is None

    def test_since_only(self, err_console: Console) -> None:
        # Naive date-only is interpreted in system local time per
        # ``parse_datetime``'s contract; the UTC result depends on TZ, so
        # we verify only the parsed-and-aware shape, not the wall-clock
        # value.
        since, until = parse_time_window(
            "2026-05-01", None, err_console=err_console,
        )
        assert since is not None
        assert since.tzinfo is not None
        assert until is None

    def test_until_only(self, err_console: Console) -> None:
        since, until = parse_time_window(
            None, "2026-05-08", err_console=err_console,
        )
        assert since is None
        assert until is not None
        assert until.tzinfo is not None

    def test_relative_format(self, err_console: Console) -> None:
        since, until = parse_time_window(
            "7d", None, err_console=err_console,
        )
        assert since is not None
        # Relative durations resolve to UTC-aware.
        assert since.tzinfo is not None

    def test_iso_8601_with_offset(self, err_console: Console) -> None:
        since, _ = parse_time_window(
            "2026-05-01T12:00:00+00:00", None, err_console=err_console,
        )
        assert since == datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)


class TestErrors:
    def test_unparseable_since_exits_user_error(
        self, err_console: Console, capsys: pytest.CaptureFixture[str],
    ) -> None:
        with pytest.raises(typer.Exit) as exc_info:
            parse_time_window("not-a-date", None, err_console=err_console)
        assert exc_info.value.exit_code == EXIT_USER_ERROR
        captured = capsys.readouterr()
        assert "--since" in captured.err
        assert "not-a-date" in captured.err

    def test_unparseable_until_exits_user_error(
        self, err_console: Console, capsys: pytest.CaptureFixture[str],
    ) -> None:
        with pytest.raises(typer.Exit) as exc_info:
            parse_time_window(None, "garbage", err_console=err_console)
        assert exc_info.value.exit_code == EXIT_USER_ERROR
        assert "--until" in capsys.readouterr().err

    def test_inverted_interval_rejected(
        self, err_console: Console, capsys: pytest.CaptureFixture[str],
    ) -> None:
        with pytest.raises(typer.Exit) as exc_info:
            parse_time_window(
                "2026-05-08", "2026-05-01", err_console=err_console,
            )
        assert exc_info.value.exit_code == EXIT_USER_ERROR
        err = capsys.readouterr().err
        assert "did you swap them" in err
        assert "2026-05-08" in err
        assert "2026-05-01" in err

    def test_equal_bounds_rejected(
        self, err_console: Console, capsys: pytest.CaptureFixture[str],
    ) -> None:
        # since == until produces an empty half-open window — reject as
        # a foot-gun rather than silently returning zero sessions.
        with pytest.raises(typer.Exit) as exc_info:
            parse_time_window(
                "2026-05-01", "2026-05-01", err_console=err_console,
            )
        assert exc_info.value.exit_code == EXIT_USER_ERROR
        assert "did you swap them" in capsys.readouterr().err
