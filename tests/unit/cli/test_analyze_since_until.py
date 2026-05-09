"""Tests for ``agentfluent analyze --since/--until``.

Seeded fixture session has its first message at 2026-04-10T10:00:00Z.
Tests pick boundaries on either side of that anchor.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from agentfluent.cli.exit_codes import EXIT_NO_DATA, EXIT_OK, EXIT_USER_ERROR
from agentfluent.cli.formatters.json_output import parse_json_output


class TestFlagInteractionErrors:
    """Mutually-exclusive flag combinations and inverted intervals must
    surface ``EXIT_USER_ERROR`` with a clear message."""

    @pytest.mark.parametrize(
        ("flag", "value"),
        [("--since", "7d"), ("--until", "2026-05-01")],
    )
    def test_session_and_time_flags_mutually_exclusive(
        self,
        runner: CliRunner,
        cli_app: typer.Typer,
        populated_home: Path,
        flag: str,
        value: str,
    ) -> None:
        result = runner.invoke(
            cli_app,
            [
                "analyze", "--project", "project",
                "--session", "session-1.jsonl",
                flag, value,
            ],
        )
        assert result.exit_code == EXIT_USER_ERROR
        assert "cannot be combined with --session" in result.stderr

    def test_inverted_interval_rejected(
        self, runner: CliRunner, cli_app: typer.Typer, populated_home: Path,
    ) -> None:
        result = runner.invoke(
            cli_app,
            [
                "analyze", "--project", "project",
                "--since", "2026-05-08",
                "--until", "2026-05-01",
            ],
        )
        assert result.exit_code == EXIT_USER_ERROR
        assert "swap" in result.stderr

    def test_unparseable_since(
        self, runner: CliRunner, cli_app: typer.Typer, populated_home: Path,
    ) -> None:
        result = runner.invoke(
            cli_app,
            ["analyze", "--project", "project", "--since", "not-a-date"],
        )
        assert result.exit_code == EXIT_USER_ERROR
        assert "--since" in result.stderr


class TestEmptyWindow:
    def test_window_with_no_sessions_exits_no_data(
        self, runner: CliRunner, cli_app: typer.Typer, populated_home: Path,
    ) -> None:
        # Session is at 2026-04-10; --since 2026-12-31 puts it before
        # the window → empty filtered list → EXIT_NO_DATA.
        result = runner.invoke(
            cli_app,
            [
                "analyze", "--project", "project",
                "--since", "2026-12-31",
            ],
        )
        assert result.exit_code == EXIT_NO_DATA
        assert "No sessions found in the specified time window" in result.stderr


class TestFilterApplied:
    def test_window_brackets_session(
        self, runner: CliRunner, cli_app: typer.Typer, populated_home: Path,
    ) -> None:
        # Session at 2026-04-10 falls inside [2026-04-01, 2026-05-01).
        result = runner.invoke(
            cli_app,
            [
                "analyze", "--project", "project",
                "--since", "2026-04-01",
                "--until", "2026-05-01",
                "--quiet",
            ],
        )
        assert result.exit_code == EXIT_OK

    def test_session_excluded_by_until(
        self, runner: CliRunner, cli_app: typer.Typer, populated_home: Path,
    ) -> None:
        # --until 2026-04-01 cuts off before the session → empty.
        result = runner.invoke(
            cli_app,
            [
                "analyze", "--project", "project",
                "--until", "2026-04-01",
            ],
        )
        assert result.exit_code == EXIT_NO_DATA


class TestVerboseWindowNote:
    def test_verbose_prints_resolved_window(
        self, runner: CliRunner, cli_app: typer.Typer, populated_home: Path,
    ) -> None:
        result = runner.invoke(
            cli_app,
            [
                "analyze", "--project", "project",
                "--since", "2026-04-01",
                "--until", "2026-05-01",
                "--verbose",
            ],
        )
        assert result.exit_code == EXIT_OK
        # Verbose-mode dim note shows the resolved window and counts.
        assert "Filtering: sessions from" in result.stderr
        assert "1 of 1 sessions" in result.stderr

    def test_quiet_does_not_print_window_note(
        self, runner: CliRunner, cli_app: typer.Typer, populated_home: Path,
    ) -> None:
        result = runner.invoke(
            cli_app,
            [
                "analyze", "--project", "project",
                "--since", "2026-04-01",
                "--until", "2026-05-01",
                "--quiet",
            ],
        )
        assert result.exit_code == EXIT_OK
        assert "Filtering: sessions from" not in result.stderr


class TestSinceAndLatestComposition:
    """``--since`` filters first; ``--latest N`` then takes the N most
    recent of the post-filter set (per PRD Section 5)."""

    def test_since_then_latest(
        self, runner: CliRunner, cli_app: typer.Typer, populated_home: Path,
    ) -> None:
        # One session in window; --latest 5 still resolves to that one.
        result = runner.invoke(
            cli_app,
            [
                "analyze", "--project", "project",
                "--since", "2026-04-01",
                "--latest", "5",
                "--quiet",
            ],
        )
        assert result.exit_code == EXIT_OK


class TestNoChangeWhenFlagsAbsent:
    """No regressions when --since/--until are not supplied."""

    def test_default_invocation_unchanged(
        self, runner: CliRunner, cli_app: typer.Typer, populated_home: Path,
    ) -> None:
        # Baseline analyze with no time flags must still succeed and
        # NOT print the filtering note even with --verbose.
        result = runner.invoke(
            cli_app,
            ["analyze", "--project", "project", "--verbose"],
        )
        assert result.exit_code == EXIT_OK
        assert "Filtering: sessions from" not in result.stderr


class TestWindowMetadataInJsonOutput:
    """``data.window`` echoes the resolved exclusive UTC bounds plus
    pre-/post-filter session counts when ``--since``/``--until`` are
    supplied; ``None`` (JSON null) when no filter is applied (#298).
    """

    def test_since_only_populates_window_with_null_until(
        self, runner: CliRunner, cli_app: typer.Typer, populated_home: Path,
    ) -> None:
        result = runner.invoke(
            cli_app,
            [
                "analyze", "--project", "project",
                "--since", "2026-04-01",
                "--json",
            ],
        )
        assert result.exit_code == EXIT_OK
        data = parse_json_output(result.stdout, expected_command="analyze")
        assert isinstance(data, dict)
        window = data.get("window")
        assert window is not None, "window must be populated when --since is set"
        assert isinstance(window["since"], str)
        assert window["since"].startswith("2026-04-01")
        assert window["until"] is None
        # Fixture has exactly one session, and it falls inside the window.
        assert window["session_count_before_filter"] == 1
        assert window["session_count_after_filter"] == 1

    def test_no_time_flags_yields_null_window(
        self, runner: CliRunner, cli_app: typer.Typer, populated_home: Path,
    ) -> None:
        result = runner.invoke(
            cli_app,
            ["analyze", "--project", "project", "--json"],
        )
        assert result.exit_code == EXIT_OK
        data = parse_json_output(result.stdout, expected_command="analyze")
        assert isinstance(data, dict)
        # Field is present but null when no time filter is applied.
        assert "window" in data
        assert data["window"] is None

    def test_since_and_until_populate_both_bounds(
        self, runner: CliRunner, cli_app: typer.Typer, populated_home: Path,
    ) -> None:
        result = runner.invoke(
            cli_app,
            [
                "analyze", "--project", "project",
                "--since", "2026-04-01",
                "--until", "2026-05-01",
                "--json",
            ],
        )
        assert result.exit_code == EXIT_OK
        data = parse_json_output(result.stdout, expected_command="analyze")
        assert isinstance(data, dict)
        window = data.get("window")
        assert window is not None
        assert isinstance(window["since"], str)
        assert window["since"].startswith("2026-04-01")
        # `until` echoes the resolved exclusive UTC bound matching the
        # half-open `[since, until)` semantics.
        assert isinstance(window["until"], str)
        assert window["until"].startswith("2026-05-01")
        assert window["session_count_before_filter"] == 1
        assert window["session_count_after_filter"] == 1


class TestDiagnosticsVersionStamp:
    """``analyze --json`` envelopes carry ``diagnostics_version`` so
    ``diff`` can warn on detector-version drift between runs (#347)."""

    def test_envelope_includes_package_version(
        self, runner: CliRunner, cli_app: typer.Typer, populated_home: Path,
    ) -> None:
        from agentfluent import __version__

        result = runner.invoke(
            cli_app, ["analyze", "--project", "project", "--json"],
        )
        assert result.exit_code == EXIT_OK
        data = parse_json_output(result.stdout, expected_command="analyze")
        assert isinstance(data, dict)
        assert data.get("diagnostics_version") == __version__
