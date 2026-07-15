"""Tests for #636 main-model rotation and run-manifest recording (runner layer).

`select_main_model` is a pure, date-keyed rotation (unit-testable without the SDK
or a live model), and `_record_run_best_effort` is the seam that must NEVER flip
the gate — a missing session_id or an I/O error is swallowed, mirroring the
best-effort contract of synthesis itself.
"""

from __future__ import annotations

from datetime import date

import pytest

from tools.dogfood_runner import runner


def _stamp(iso_date: str) -> str:
    """A runstamp (``YYYYMMDDTHHMMSSZ``) for a given ``YYYY-MM-DD``."""
    return iso_date.replace("-", "") + "T120000Z"


class TestSelectMainModel:
    def test_override_wins_regardless_of_date(self) -> None:
        assert (
            runner.select_main_model(_stamp("2026-07-15"), "claude-pinned-x")
            == "claude-pinned-x"
        )

    def test_empty_override_falls_through_to_rotation(self) -> None:
        # An empty string (e.g. an unset env var read as "") must not pin.
        assert runner.select_main_model(_stamp("2026-07-15"), "") in runner.MAIN_MODELS

    def test_deterministic_for_the_same_date(self) -> None:
        stamp = _stamp("2026-07-15")
        assert runner.select_main_model(stamp) == runner.select_main_model(stamp)

    def test_three_consecutive_days_cover_the_full_cycle(self) -> None:
        picks = {
            runner.select_main_model(_stamp(f"2026-07-{15 + i:02d}"))
            for i in range(len(runner.MAIN_MODELS))
        }
        # Consecutive ordinals mod N are distinct → every tier is exercised.
        assert picks == set(runner.MAIN_MODELS)

    def test_period_equals_number_of_models(self) -> None:
        base = _stamp("2026-07-15")
        later = _stamp("2026-07-18")  # +3 days == len(MAIN_MODELS)
        assert runner.select_main_model(base) == runner.select_main_model(later)

    def test_matches_ordinal_formula(self) -> None:
        stamp = _stamp("2026-07-15")
        expected = runner.MAIN_MODELS[date(2026, 7, 15).toordinal() % len(runner.MAIN_MODELS)]
        assert runner.select_main_model(stamp) == expected


class TestRecordRunBestEffort:
    def test_records_resolved_session_path_on_success(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, object] = {}
        monkeypatch.setattr(
            runner, "_resolve_session_jsonl", lambda sid: f"/corpus/{sid}.jsonl"
        )
        monkeypatch.setattr(
            runner, "record_run", lambda **kw: captured.update(kw) or None
        )

        runner._record_run_best_effort("20260715T120000Z", "claude-opus-4-8", "sess-9")

        assert captured == {
            "runstamp": "20260715T120000Z",
            "main_model": "claude-opus-4-8",
            "subagent_model": runner.SUBAGENT_MODEL,
            "session_id": "sess-9",
            "session_jsonl": "/corpus/sess-9.jsonl",
        }

    def test_none_session_id_is_a_noop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _must_not_call(**_kw: object) -> None:
            raise AssertionError("record_run must not run without a session_id")

        monkeypatch.setattr(runner, "record_run", _must_not_call)
        # No session_id → logged and swallowed, no write, no raise.
        runner._record_run_best_effort("20260715T120000Z", "claude-opus-4-8", None)

    def test_io_error_is_swallowed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(runner, "_resolve_session_jsonl", lambda sid: "/x.jsonl")

        def _boom(**_kw: object) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(runner, "record_run", _boom)
        # Must not propagate — a manifest write can never flip the gate.
        runner._record_run_best_effort("20260715T120000Z", "claude-haiku-4-5", "s1")
