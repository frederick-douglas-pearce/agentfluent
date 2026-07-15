"""Tests for the runner entrypoint's process-level contract (runner.main).

`main()`'s ``return 1 if report.is_red else 0`` IS the anti-false-green guarantee
at the cron boundary (a nightly run's exit code is what a monitor keys on), so it
is pinned directly here. ``--no-synthesis`` keeps the SDK entirely out of the path.
"""

from __future__ import annotations

import pytest

from tools.dogfood_runner import runner
from tools.dogfood_runner.cli_runner import GateReport


def _healthy() -> GateReport:
    return GateReport(window="3d", results=[], errors=[])


def _red() -> GateReport:
    return GateReport(window="3d", results=[], errors=["-home-proj: analyze failed (exit 1)"])


def test_main_exits_zero_on_healthy_report(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner, "run_gate", lambda *a, **k: _healthy())
    assert runner.main(["--no-synthesis"]) == 0


def test_main_exits_one_on_red_report(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner, "run_gate", lambda *a, **k: _red())
    # The gate went red — the process MUST signal it (exit 1), never false-green.
    assert runner.main(["--no-synthesis"]) == 1


def test_main_no_synthesis_never_imports_sdk(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner, "run_gate", lambda *a, **k: _healthy())

    def _boom(_report: GateReport, _model: str) -> str:
        raise AssertionError("synthesize must not be called under --no-synthesis")

    monkeypatch.setattr(runner, "synthesize", _boom)
    assert runner.main(["--no-synthesis"]) == 0


def test_main_synthesis_failure_never_flips_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner, "run_gate", lambda *a, **k: _healthy())

    async def _boom(_report: GateReport, _model: str) -> tuple[str, str | None]:
        raise RuntimeError("SDK not installed / auth missing")

    monkeypatch.setattr(runner, "synthesize", _boom)
    # A synthesis failure leaves session_id None; recording must also stay
    # best-effort and never flip the gate.
    monkeypatch.setattr(runner, "_record_run_best_effort", lambda *a, **k: None)
    # Synthesis blows up, but the gate was green → exit stays 0 (best-effort).
    assert runner.main([]) == 0
