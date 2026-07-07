"""Tests for the deterministic dogfood gate (tools/dogfood_runner/cli_runner.py).

The CLI is mocked at the ``subprocess.run`` seam (``CommandRunner``), so these run
without shelling out, without the SDK, and without a real corpus. They pin the
code-aware exit-code gate — the anti-false-green guarantee (#590, architect review).
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

from tools.dogfood_runner import cli_runner as cr


def _cp(returncode: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["fake"], returncode=returncode, stdout=stdout, stderr=stderr
    )


class QueueRunner:
    """A CommandRunner that returns queued responses in FIFO order, recording calls."""

    def __init__(self, responses: Sequence[subprocess.CompletedProcess[str]]) -> None:
        self._responses = list(responses)
        self.calls: list[list[str]] = []

    def __call__(self, cmd: Sequence[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(cmd))
        return self._responses.pop(0)


def _analyze_stdout(cost: float = 1.0) -> str:
    return json.dumps(
        {"version": "2", "command": "analyze", "data": {"session_count": 3, "total_cost": cost}}
    )


def _diff_stdout(regression: bool) -> str:
    return json.dumps(
        {"version": "2", "command": "diff", "data": {"regression_detected": regression}}
    )


# --- code-aware classification (the gate's core) -------------------------------


@pytest.mark.parametrize(
    ("rc", "expected"),
    [
        (cr.EXIT_OK, cr.Verdict.OK),
        (cr.EXIT_NO_DATA, cr.Verdict.EMPTY),  # empty per-slug window is benign
        (1, cr.Verdict.ERROR),  # EXIT_USER_ERROR is the real red
        (3, cr.Verdict.ERROR),  # analyze never regresses; unexpected → red
    ],
)
def test_classify_analyze_returncode(rc: int, expected: cr.Verdict) -> None:
    assert cr.classify_analyze_returncode(rc) is expected


@pytest.mark.parametrize(
    ("rc", "verdict", "regressed"),
    [
        (cr.EXIT_OK, cr.Verdict.OK, False),
        (cr.EXIT_REGRESSION, cr.Verdict.REGRESSION, True),  # a finding, not a runner error
        (1, cr.Verdict.ERROR, False),
    ],
)
def test_classify_diff_returncode(rc: int, verdict: cr.Verdict, regressed: bool) -> None:
    assert cr.classify_diff_returncode(rc) == (verdict, regressed)


# --- CliRunner adapter ---------------------------------------------------------


def test_enumerate_slugs_parses_projects() -> None:
    projects = [{"slug": "a"}, {"slug": "b"}, {"name": "no-slug"}]
    payload = json.dumps({"data": {"projects": projects}})
    cli = cr.CliRunner(base_cmd=["af"], run=QueueRunner([_cp(0, payload)]))
    assert cli.enumerate_slugs() == ["a", "b"]


def test_enumerate_slugs_no_data_is_empty_not_error() -> None:
    cli = cr.CliRunner(base_cmd=["af"], run=QueueRunner([_cp(cr.EXIT_NO_DATA)]))
    assert cli.enumerate_slugs() == []


def test_enumerate_slugs_raises_on_real_failure() -> None:
    cli = cr.CliRunner(base_cmd=["af"], run=QueueRunner([_cp(1, stderr="boom")]))
    with pytest.raises(cr.DogfoodError, match="list` failed"):
        cli.enumerate_slugs()


def test_analyze_ok_parses_data_and_keeps_stdout() -> None:
    stdout = _analyze_stdout(cost=2.5)
    cli = cr.CliRunner(base_cmd=["af"], run=QueueRunner([_cp(0, stdout)]))
    out = cli.analyze("-home-proj", "3d")
    assert out.verdict is cr.Verdict.OK
    assert out.data == {"session_count": 3, "total_cost": 2.5}
    assert out.stdout == stdout  # persisted verbatim as the diff baseline
    assert not out.is_red


def test_analyze_empty_window_is_not_red() -> None:
    cli = cr.CliRunner(base_cmd=["af"], run=QueueRunner([_cp(cr.EXIT_NO_DATA)]))
    out = cli.analyze("slug", "3d")
    assert out.verdict is cr.Verdict.EMPTY
    assert out.data is None
    assert not out.is_red


def test_analyze_user_error_is_red_and_keeps_stderr() -> None:
    cli = cr.CliRunner(base_cmd=["af"], run=QueueRunner([_cp(1, stderr="unknown project")]))
    out = cli.analyze("slug", "3d")
    assert out.verdict is cr.Verdict.ERROR
    assert out.is_red
    assert out.stderr == "unknown project"


def test_analyze_command_shape_and_config_dir_ordering() -> None:
    runner = QueueRunner([_cp(0, _analyze_stdout())])
    cli = cr.CliRunner(base_cmd=["af"], config_dir=Path("/cfg"), run=runner)
    cli.analyze("slug", "7d")
    cmd = runner.calls[0]
    # Global option binds to the top-level callback → precedes the subcommand.
    assert cmd == [
        "af", "--claude-config-dir", "/cfg",
        "analyze", "--project", "slug", "--since", "7d", "--json",
    ]


def test_diff_regression_is_surfaced_not_errored() -> None:
    runner = QueueRunner([_cp(cr.EXIT_REGRESSION, _diff_stdout(regression=True))])
    cli = cr.CliRunner(base_cmd=["af"], run=runner)
    out = cli.diff("slug", Path("base.json"), Path("cur.json"))
    assert out.verdict is cr.Verdict.REGRESSION
    assert out.regression_detected
    assert not out.is_red  # regression is the signal we want, not a runner failure


def test_parse_json_raises_on_garbage() -> None:
    cli = cr.CliRunner(base_cmd=["af"], run=QueueRunner([_cp(0, "not json")]))
    with pytest.raises(cr.DogfoodError, match="could not parse"):
        cli.analyze("slug", "3d")


def test_timeout_surfaces_as_dogfood_error() -> None:
    def boom(cmd: Sequence[str]) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd=list(cmd), timeout=1)

    cli = cr.CliRunner(base_cmd=["af"], run=boom)
    with pytest.raises(cr.DogfoodError, match="timed out"):
        cli.analyze("slug", "3d")


# --- run_gate orchestration ----------------------------------------------------


def test_run_gate_first_run_writes_snapshot_no_diff(tmp_path: Path) -> None:
    cli = cr.CliRunner(base_cmd=["af"], run=QueueRunner([_cp(0, _analyze_stdout())]))
    report = cr.run_gate(
        cli, window="3d", runstamp="20260101T000000Z", slugs=["slug"], state_root=tmp_path
    )
    assert not report.is_red
    result = report.results[0]
    assert result.diff is None  # no baseline on the first run
    assert result.snapshot is not None and result.snapshot.read_text() == _analyze_stdout()


def test_run_gate_second_run_diffs_against_previous(tmp_path: Path) -> None:
    # Run 1 seeds a snapshot.
    cr.run_gate(
        cr.CliRunner(base_cmd=["af"], run=QueueRunner([_cp(0, _analyze_stdout())])),
        window="3d", runstamp="20260101T000000Z", slugs=["slug"], state_root=tmp_path,
    )
    # Run 2: analyze then diff against the run-1 baseline.
    cli = cr.CliRunner(
        base_cmd=["af"],
        run=QueueRunner([_cp(0, _analyze_stdout()), _cp(0, _diff_stdout(regression=False))]),
    )
    report = cr.run_gate(
        cli, window="3d", runstamp="20260102T000000Z", slugs=["slug"], state_root=tmp_path
    )
    assert report.results[0].diff is not None
    assert not report.is_red


def test_run_gate_regression_is_reported_but_not_red(tmp_path: Path) -> None:
    cr.run_gate(
        cr.CliRunner(base_cmd=["af"], run=QueueRunner([_cp(0, _analyze_stdout())])),
        window="3d", runstamp="20260101T000000Z", slugs=["slug"], state_root=tmp_path,
    )
    cli = cr.CliRunner(
        base_cmd=["af"],
        run=QueueRunner(
            [_cp(0, _analyze_stdout(cost=9.0)), _cp(cr.EXIT_REGRESSION, _diff_stdout(True))]
        ),
    )
    report = cr.run_gate(
        cli, window="3d", runstamp="20260102T000000Z", slugs=["slug"], state_root=tmp_path
    )
    assert report.regressions  # surfaced
    assert not report.is_red  # but not a runner failure


def test_run_gate_analyze_error_makes_gate_red_and_skips_snapshot(tmp_path: Path) -> None:
    cli = cr.CliRunner(base_cmd=["af"], run=QueueRunner([_cp(1, stderr="bad slug")]))
    report = cr.run_gate(
        cli, window="3d", runstamp="20260101T000000Z", slugs=["slug"], state_root=tmp_path
    )
    assert report.is_red
    assert report.results[0].snapshot is None


def test_run_gate_empty_window_is_ok_no_snapshot(tmp_path: Path) -> None:
    cli = cr.CliRunner(base_cmd=["af"], run=QueueRunner([_cp(cr.EXIT_NO_DATA)]))
    report = cr.run_gate(
        cli, window="3d", runstamp="20260101T000000Z", slugs=["slug"], state_root=tmp_path
    )
    assert not report.is_red
    assert report.results[0].snapshot is None


def test_run_gate_enumerate_failure_is_captured_as_error(tmp_path: Path) -> None:
    cli = cr.CliRunner(base_cmd=["af"], run=QueueRunner([_cp(1, stderr="no projects dir")]))
    report = cr.run_gate(cli, window="3d", runstamp="20260101T000000Z", state_root=tmp_path)
    assert report.is_red
    assert report.errors and "list` failed" in report.errors[0]


def test_render_gate_report_includes_status_and_slugs(tmp_path: Path) -> None:
    cli = cr.CliRunner(base_cmd=["af"], run=QueueRunner([_cp(0, _analyze_stdout())]))
    report = cr.run_gate(
        cli, window="3d", runstamp="20260101T000000Z", slugs=["slug"], state_root=tmp_path
    )
    text = cr.render_gate_report(report)
    assert "window=3d" in text
    assert "status: ok" in text
    assert "slug" in text


def test_run_gate_skips_diff_across_envelope_version_bump(tmp_path: Path) -> None:
    # Run 1 writes a v2 snapshot.
    v2 = json.dumps({"version": "2", "command": "analyze", "data": {"total_cost": 1.0}})
    cr.run_gate(
        cr.CliRunner(base_cmd=["af"], run=QueueRunner([_cp(0, v2)])),
        window="3d", runstamp="20260101T000000Z", slugs=["slug"], state_root=tmp_path,
    )
    # Run 2 produces a v3 envelope. diff would exit 1 on the mismatch (→ red); the
    # runner must skip it (like a first run), NOT call diff and NOT go red.
    v3 = json.dumps({"version": "3", "command": "analyze", "data": {"total_cost": 1.0}})
    runner = QueueRunner([_cp(0, v3)])  # ONLY analyze — a diff call would exhaust the queue
    report = cr.run_gate(
        cr.CliRunner(base_cmd=["af"], run=runner),
        window="3d", runstamp="20260102T000000Z", slugs=["slug"], state_root=tmp_path,
    )
    assert report.results[0].diff is None  # diff skipped across the version bump
    assert not report.is_red
    assert len(runner.calls) == 1  # analyze only; diff never invoked


def test_run_gate_continues_after_one_slug_fails(tmp_path: Path) -> None:
    # slug1's analyze returns unparseable JSON (→ DogfoodError); slug2 succeeds.
    runner = QueueRunner([_cp(0, "not json"), _cp(0, _analyze_stdout())])
    cli = cr.CliRunner(base_cmd=["af"], run=runner)
    report = cr.run_gate(
        cli, window="3d", runstamp="20260101T000000Z", slugs=["s1", "s2"], state_root=tmp_path
    )
    assert len(report.errors) == 1 and report.errors[0].startswith("s1:")
    assert [r.slug for r in report.results] == ["s2"]  # s2 still ran
    assert report.is_red  # a per-slug failure is a real red


def test_run_gate_launch_failure_is_surfaced_not_uncaught(tmp_path: Path) -> None:
    def missing_uv(cmd: Sequence[str]) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("uv")

    cli = cr.CliRunner(base_cmd=["uv"], run=missing_uv)
    report = cr.run_gate(
        cli, window="3d", runstamp="20260101T000000Z", slugs=["slug"], state_root=tmp_path
    )
    assert report.is_red
    assert report.errors and "could not launch CLI" in report.errors[0]


def test_render_warns_on_zero_slugs_without_error() -> None:
    report = cr.GateReport(window="3d", results=[], errors=[])
    text = cr.render_gate_report(report)
    assert "status: ok" in text  # not hard-red — empty corpus is legitimate
    assert "no project-slugs analyzed" in text  # but surfaced loudly
