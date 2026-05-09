"""Diff-table summary line rendering for #342 (window) + #347 (version).

Locks the user-visible copy: the window summary line must surface dates
and session counts; the diagnostics-version drift warning must
distinguish "drift" from "unknown" so a v0.6 baseline diffed against a
v0.7 current doesn't trigger a false alarm about detector sensitivity.
"""

from __future__ import annotations

import io
from datetime import datetime

from rich.console import Console

from agentfluent.cli.formatters.diff_table import (
    _format_window_side,
    _render_summary,
    _version_drift_line,
)
from agentfluent.core.filtering import WindowMetadata
from agentfluent.diff.models import DiffResult


def _capture(width: int = 200) -> tuple[Console, io.StringIO]:
    buf = io.StringIO()
    return Console(file=buf, width=width, force_terminal=False, no_color=True), buf


def _window(
    *,
    since: str | None = "2026-04-25T00:00:00+00:00",
    until: str | None = "2026-05-03T00:00:00+00:00",
    after: int = 6,
    before: int = 12,
) -> WindowMetadata:
    return WindowMetadata(
        since=datetime.fromisoformat(since) if since else None,
        until=datetime.fromisoformat(until) if until else None,
        session_count_before_filter=before,
        session_count_after_filter=after,
    )


class TestFormatWindowSide:
    def test_renders_dates_and_session_count(self) -> None:
        rendered = _format_window_side(_window(after=6), session_count=6)
        assert rendered == "2026-04-25 → 2026-05-03 (6 sessions)"

    def test_open_left_bound_shows_asterisk(self) -> None:
        rendered = _format_window_side(_window(since=None, after=4), session_count=4)
        assert rendered == "* → 2026-05-03 (4 sessions)"

    def test_open_right_bound_shows_asterisk(self) -> None:
        rendered = _format_window_side(_window(until=None, after=4), session_count=4)
        assert rendered == "2026-04-25 → * (4 sessions)"

    def test_none_window_uses_session_count_fallback(self) -> None:
        rendered = _format_window_side(None, session_count=11)
        assert rendered == "(window not recorded, 11 sessions)"


class TestVersionDriftLine:
    def _result(
        self, *, baseline: str | None = None, current: str | None = None,
    ) -> DiffResult:
        return DiffResult(
            baseline_diagnostics_version=baseline,
            current_diagnostics_version=current,
        )

    def test_silent_when_versions_match(self) -> None:
        assert _version_drift_line(
            self._result(baseline="0.7.0", current="0.7.0"),
        ) is None

    def test_silent_when_both_versions_missing(self) -> None:
        assert _version_drift_line(self._result()) is None

    def test_drift_warning_includes_both_versions(self) -> None:
        rendered = _version_drift_line(
            self._result(baseline="0.6.1", current="0.7.0"),
        )
        assert rendered is not None
        assert "v0.6.1" in rendered
        assert "v0.7.0" in rendered
        assert "drift" in rendered.lower()
        assert "yellow" in rendered

    def test_unknown_baseline_warns_dim(self) -> None:
        rendered = _version_drift_line(self._result(current="0.7.0"))
        assert rendered is not None
        assert "unknown" in rendered.lower()
        assert "baseline" in rendered.lower()
        assert "[dim]" in rendered

    def test_unknown_current_warns_dim(self) -> None:
        rendered = _version_drift_line(self._result(baseline="0.7.0"))
        assert rendered is not None
        assert "unknown" in rendered.lower()
        assert "current" in rendered.lower()


class TestRenderSummary:
    def test_session_line_only_when_no_windows(self) -> None:
        console, buf = _capture()
        result = DiffResult(
            new_count=1, resolved_count=0, persisting_count=2,
            baseline_session_count=6, current_session_count=11,
        )
        _render_summary(console, result)
        out = buf.getvalue()
        assert "Sessions: 6 → 11" in out
        assert "Baseline:" not in out

    def test_window_line_replaces_session_fallback(self) -> None:
        """When at least one envelope carries a window, the
        ``Sessions: A → B`` fallback is suppressed so the dates carry the
        load — keeps the header tight rather than duplicating cardinality."""
        console, buf = _capture()
        result = DiffResult(
            new_count=1, resolved_count=0, persisting_count=2,
            baseline_session_count=6, current_session_count=11,
            baseline_window=_window(after=6),
            current_window=_window(
                since="2026-05-03T00:00:00+00:00",
                until="2026-05-09T00:00:00+00:00",
                after=11,
            ),
        )
        _render_summary(console, result)
        out = buf.getvalue()
        assert "Sessions: 6 → 11" not in out
        assert "Baseline: 2026-04-25 → 2026-05-03 (6 sessions)" in out
        assert "Current: 2026-05-03 → 2026-05-09 (11 sessions)" in out

    def test_window_line_handles_legacy_baseline(self) -> None:
        console, buf = _capture()
        result = DiffResult(
            new_count=0, resolved_count=0, persisting_count=0,
            baseline_session_count=8, current_session_count=11,
            current_window=_window(after=11),
        )
        _render_summary(console, result)
        out = buf.getvalue()
        assert "Baseline: (window not recorded, 8 sessions)" in out
        assert "Current: 2026-04-25" in out

    def test_drift_warning_appears_below_window_line(self) -> None:
        console, buf = _capture()
        result = DiffResult(
            baseline_window=_window(),
            current_window=_window(),
            baseline_diagnostics_version="0.6.1",
            current_diagnostics_version="0.7.0",
        )
        _render_summary(console, result)
        out = buf.getvalue()
        baseline_idx = out.index("Baseline:")
        drift_idx = out.lower().index("drift")
        assert baseline_idx < drift_idx

    def test_no_drift_line_when_versions_match(self) -> None:
        console, buf = _capture()
        result = DiffResult(
            baseline_diagnostics_version="0.7.0",
            current_diagnostics_version="0.7.0",
        )
        _render_summary(console, result)
        assert "drift" not in buf.getvalue().lower()
        assert "unknown" not in buf.getvalue().lower()
