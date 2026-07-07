"""Deterministic, SDK-free core of the dogfood-runner (S0 / #590).

This module owns the **pass/fail gate**. Per the architect review on #590
(anti-false-green, PRD Risk R), the correctness signal must never route through
an LLM subagent — an LLM can transcribe ``$?`` wrong, silently retry-and-report
success, or lossily summarize a Bash result. So the parent shells out to the real
``agentfluent`` CLI here, in plain Python, and reads ``returncode`` directly. The
SDK subagents (in :mod:`tools.dogfood_runner.runner`) only synthesize narrative;
they are never on the critical path of the gate.

The gate is **code-aware**, not a naive ``== 0`` check (which false-reds on every
empty per-slug window):

* ``analyze`` exit 0 → OK; exit 2 (NO_DATA) → EMPTY (benign — window had no
  sessions for that slug); exit 1 or anything else → ERROR (real red).
* ``diff`` exit 0 → OK; exit 3 (REGRESSION) → a *finding* we want to surface, not
  a runner failure; anything else → ERROR.

Kept free of any ``claude_agent_sdk`` import so unit tests import it without the
``research`` dependency-group installed (that import is lazy, in ``runner``).
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from agentfluent.cli.exit_codes import (
    EXIT_NO_DATA,
    EXIT_OK,
    EXIT_REGRESSION,
)
from tools.dogfood_runner.paths import (
    DEFAULT_SNAPSHOT_RETENTION,
    latest_snapshot,
    new_snapshot_path,
    prune_snapshots,
)

# The runner drives the CLI through the project venv so a cron entry needs no
# activated environment. Overridable (tests inject a fake; a caller may point at
# an already-installed ``agentfluent`` console script).
DEFAULT_BASE_CMD: tuple[str, ...] = ("uv", "run", "agentfluent")

# Bounded rolling window (AC): analyze the last few days, NOT the whole corpus.
# A few-day window on a daily cadence overlaps enough to catch sudden deltas
# cleanly. Configurable; this is a build-time default, not a filing decision.
DEFAULT_WINDOW = "3d"

# Cap a single CLI invocation so a hung analyze can't wedge the cron run forever.
DEFAULT_TIMEOUT_S = 600

CommandRunner = Callable[[Sequence[str]], "subprocess.CompletedProcess[str]"]


class DogfoodError(RuntimeError):
    """A dogfood-runner failure that must surface (never swallowed to false-green)."""


class Verdict(StrEnum):
    OK = "ok"
    EMPTY = "empty"  # searched, nothing in the window — benign
    REGRESSION = "regression"  # diff finding we want to surface, not a runner error
    ERROR = "error"  # a real failure — the gate goes red


def classify_analyze_returncode(returncode: int) -> Verdict:
    """Map an ``agentfluent analyze`` exit code to a verdict (code-aware gate)."""
    if returncode == EXIT_OK:
        return Verdict.OK
    if returncode == EXIT_NO_DATA:
        return Verdict.EMPTY  # empty per-slug window is expected, not a failure
    return Verdict.ERROR  # EXIT_USER_ERROR, or an unexpected code (analyze never regresses)


def classify_diff_returncode(returncode: int) -> tuple[Verdict, bool]:
    """Map an ``agentfluent diff`` exit code to ``(verdict, regression_detected)``."""
    if returncode == EXIT_OK:
        return Verdict.OK, False
    if returncode == EXIT_REGRESSION:
        return Verdict.REGRESSION, True  # the signal the runner exists to surface
    return Verdict.ERROR, False


@dataclass
class AnalyzeOutcome:
    slug: str
    command: list[str]
    returncode: int
    verdict: Verdict
    data: dict[str, Any] | None  # parsed ``.data`` envelope (None unless OK)
    stdout: str  # raw stdout — persisted verbatim as the diff baseline
    stderr: str

    @property
    def is_red(self) -> bool:
        return self.verdict is Verdict.ERROR


@dataclass
class DiffOutcome:
    slug: str
    command: list[str]
    returncode: int
    verdict: Verdict
    regression_detected: bool
    data: dict[str, Any] | None
    stderr: str

    @property
    def is_red(self) -> bool:
        return self.verdict is Verdict.ERROR


@dataclass
class SlugResult:
    slug: str
    analyze: AnalyzeOutcome
    diff: DiffOutcome | None  # None on the first run for a slug (no baseline yet)
    snapshot: Path | None


@dataclass
class GateReport:
    window: str
    results: list[SlugResult]
    errors: list[str]  # top-level failures (e.g. slug enumeration blew up)

    @property
    def is_red(self) -> bool:
        """True if the analysis itself failed anywhere — the cron must alert.

        Deliberately excludes regressions: a regression is a *successful* dogfood
        that found something, surfaced in the report, not a runner malfunction.
        """
        if self.errors:
            return True
        return any(
            r.analyze.is_red or (r.diff is not None and r.diff.is_red)
            for r in self.results
        )

    @property
    def regressions(self) -> list[SlugResult]:
        return [r for r in self.results if r.diff is not None and r.diff.regression_detected]


def _default_run(cmd: Sequence[str], timeout_s: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 — cmd is built from a fixed base + validated slugs
        list(cmd),
        capture_output=True,
        text=True,
        timeout=timeout_s,
    )


def _parse_json(text: str) -> dict[str, Any]:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise DogfoodError(f"could not parse CLI JSON output: {exc}") from exc
    if not isinstance(parsed, dict):
        raise DogfoodError("CLI JSON output was not an object")
    return parsed


class CliRunner:
    """Thin, deterministic adapter over the real ``agentfluent`` CLI."""

    def __init__(
        self,
        *,
        base_cmd: Sequence[str] = DEFAULT_BASE_CMD,
        config_dir: Path | None = None,
        run: CommandRunner | None = None,
        timeout_s: int = DEFAULT_TIMEOUT_S,
    ) -> None:
        self._base = list(base_cmd)
        self._config_dir = config_dir
        self._timeout_s = timeout_s
        self._run: CommandRunner = run or (lambda cmd: _default_run(cmd, timeout_s))

    def _build_cmd(self, sub_args: Sequence[str]) -> list[str]:
        # Global options bind to the top-level callback, so they precede the
        # subcommand: ``agentfluent --claude-config-dir X analyze ...``.
        global_opts: list[str] = []
        if self._config_dir is not None:
            global_opts += ["--claude-config-dir", str(self._config_dir)]
        return [*self._base, *global_opts, *sub_args]

    def _execute(
        self, sub_args: Sequence[str]
    ) -> tuple[list[str], subprocess.CompletedProcess[str]]:
        cmd = self._build_cmd(sub_args)
        try:
            result = self._run(cmd)
        except subprocess.TimeoutExpired as exc:
            raise DogfoodError(
                f"CLI timed out after {self._timeout_s}s: {' '.join(cmd)}"
            ) from exc
        return cmd, result

    def enumerate_slugs(self) -> list[str]:
        """Project-slugs to fan out over (``agentfluent list --format json``)."""
        cmd, result = self._execute(["list", "--format", "json"])
        if result.returncode == EXIT_NO_DATA:
            return []  # no corpus / no projects — nothing to dogfood, not an error
        if result.returncode != EXIT_OK:
            raise DogfoodError(
                f"`agentfluent list` failed (exit {result.returncode}): "
                f"{result.stderr.strip()}"
            )
        payload = _parse_json(result.stdout)
        projects = payload.get("data", {}).get("projects", [])
        return [p["slug"] for p in projects if isinstance(p, dict) and p.get("slug")]

    def analyze(self, slug: str, since: str, *, until: str | None = None) -> AnalyzeOutcome:
        sub_args = ["analyze", "--project", slug, "--since", since, "--json"]
        if until is not None:
            sub_args += ["--until", until]
        cmd, result = self._execute(sub_args)
        verdict = classify_analyze_returncode(result.returncode)
        data = _parse_json(result.stdout).get("data") if verdict is Verdict.OK else None
        return AnalyzeOutcome(
            slug=slug,
            command=cmd,
            returncode=result.returncode,
            verdict=verdict,
            data=data,
            stdout=result.stdout,
            stderr=result.stderr,
        )

    def diff(
        self,
        slug: str,
        baseline: Path,
        current: Path,
        *,
        fail_on: str = "warning",
    ) -> DiffOutcome:
        sub_args = ["diff", str(baseline), str(current), "--fail-on", fail_on, "--json"]
        cmd, result = self._execute(sub_args)
        verdict, regressed = classify_diff_returncode(result.returncode)
        data = (
            _parse_json(result.stdout).get("data")
            if verdict in (Verdict.OK, Verdict.REGRESSION)
            else None
        )
        return DiffOutcome(
            slug=slug,
            command=cmd,
            returncode=result.returncode,
            verdict=verdict,
            regression_detected=regressed,
            data=data,
            stderr=result.stderr,
        )


def run_gate(
    cli: CliRunner,
    *,
    window: str,
    runstamp: str,
    slugs: Sequence[str] | None = None,
    state_root: Path | None = None,
    retention: int = DEFAULT_SNAPSHOT_RETENTION,
    fail_on: str = "warning",
) -> GateReport:
    """Run the bounded-window analyze + window-over-window diff over each slug.

    Deterministic given its inputs (the CLI seam and ``runstamp`` are injected),
    so it is fully unit-testable without the SDK or a live model.
    """
    try:
        target_slugs = list(slugs) if slugs is not None else cli.enumerate_slugs()
    except DogfoodError as exc:
        return GateReport(window=window, results=[], errors=[str(exc)])

    results: list[SlugResult] = []
    errors: list[str] = []
    for slug in target_slugs:
        try:
            results.append(
                _run_one_slug(
                    cli,
                    slug=slug,
                    window=window,
                    runstamp=runstamp,
                    state_root=state_root,
                    retention=retention,
                    fail_on=fail_on,
                )
            )
        except DogfoodError as exc:
            # A per-slug failure is surfaced, never swallowed; other slugs still run.
            errors.append(f"{slug}: {exc}")
    return GateReport(window=window, results=results, errors=errors)


def _run_one_slug(
    cli: CliRunner,
    *,
    slug: str,
    window: str,
    runstamp: str,
    state_root: Path | None,
    retention: int,
    fail_on: str,
) -> SlugResult:
    outcome = cli.analyze(slug, window)
    snapshot: Path | None = None
    diff_outcome: DiffOutcome | None = None
    if outcome.verdict is Verdict.OK:
        # Baseline is the PREVIOUS run's snapshot — read before writing this one.
        baseline = latest_snapshot(slug, root=state_root)
        snapshot = new_snapshot_path(slug, runstamp, root=state_root)
        snapshot.write_text(outcome.stdout)
        if baseline is not None:
            diff_outcome = cli.diff(slug, baseline, snapshot, fail_on=fail_on)
        prune_snapshots(slug, keep=retention, root=state_root)
    return SlugResult(slug=slug, analyze=outcome, diff=diff_outcome, snapshot=snapshot)


def render_gate_report(report: GateReport) -> str:
    """Deterministic plaintext report — the machine-verifiable dogfood summary.

    The SDK narrative synthesis (``runner``) is layered on top of this; this text
    stands alone so the cron output is meaningful even when synthesis is skipped.
    """
    lines = [
        f"AgentFluent dogfood — window={report.window}",
        f"status: {'RED (analysis error)' if report.is_red else 'ok'}",
        f"slugs analyzed: {len(report.results)}",
    ]
    for result in report.results:
        a = result.analyze
        parts = [f"  [{a.verdict.value}] {result.slug}"]
        if result.diff is not None:
            d = result.diff
            flag = "REGRESSION" if d.regression_detected else d.verdict.value
            parts.append(f"— diff: {flag}")
        lines.append(" ".join(parts))
        if a.is_red and a.stderr.strip():
            lines.append(f"      stderr: {a.stderr.strip().splitlines()[-1]}")
    if report.regressions:
        lines.append(f"regressions detected in {len(report.regressions)} slug(s)")
    for err in report.errors:
        lines.append(f"ERROR {err}")
    return "\n".join(lines)
