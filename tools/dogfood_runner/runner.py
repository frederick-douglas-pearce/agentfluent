"""Dogfood-runner entrypoint (S0 / #590): gate + SDK narrative synthesis.

Run it directly (this is the scheduler-agnostic entrypoint a cron entry fires)::

    uv run --group research python tools/dogfood_runner/runner.py
    uv run --group research python tools/dogfood_runner/runner.py --window 7d
    uv run python tools/dogfood_runner/runner.py --no-synthesis   # gate only, no SDK

Two layers, deliberately separated (architect review, #590):

1. **The gate** (:func:`tools.dogfood_runner.cli_runner.run_gate`) — deterministic,
   drives the real ``agentfluent`` CLI, owns pass/fail by reading exit codes. Runs
   with or without the ``research`` group; ``--no-synthesis`` stops here.
2. **Narrative synthesis** — an Agent SDK ``query()`` (a rotated parent model)
   fans out one Haiku subagent per slug to summarize that slug's snapshot, and the
   parent stitches a report. Best-effort: a synthesis failure is logged but NEVER
   flips the gate (the gate is about analysis correctness, not the narrative). The
   parent/child model split is intentional — it emits the model-divergence and
   nested-trace bytes that S5 (#595) and #112 will consume, so the runner dogfoods
   the exact v0.11 surfaces. The parent model **rotates** across
   :data:`MAIN_MODELS` by run date (#636), diversifying the corpus's model
   dimension; each run is recorded in the out-of-tree run manifest (``paths``) so
   sessions are discoverable by their main-model variant.

``claude_agent_sdk`` is imported lazily inside :func:`synthesize` so this module
imports (and the gate runs) without the ``research`` dependency-group installed.

Not part of the published ``agentfluent`` package.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from tools.dogfood_runner.cli_runner import (
    DEFAULT_SNAPSHOT_RETENTION,
    DEFAULT_WINDOW,
    CliRunner,
    GateReport,
    render_gate_report,
    run_gate,
)
from tools.dogfood_runner.paths import record_run

# Repo root — the subagents run `uv run agentfluent report` here, so their cwd must
# resolve the project venv. tools/dogfood_runner/runner.py → parents[2] is the root.
REPO_DIR = Path(__file__).resolve().parents[2]

# Non-dated aliases on purpose: a dated model pin (e.g. ``claude-haiku-4-5-20251001``)
# starts long-hanging then 529s once that snapshot is retired. Centralized here so
# there is one place to bump.
#
# The parent (synthesis) model is ROTATED across these tiers to diversify the SDK
# dogfood corpus's model dimension (#636) — every run otherwise sits at one point
# in the model dimension, and opus is overkill for orchestration+summarization
# (#635). The subagent stays Haiku (main-model-only per #636 scope). ``sonnet-4-6``
# is the non-dated alias the rest of the repo already trusts (fixtures, builders,
# the #519 matrix runner).
MAIN_MODELS = ("claude-opus-4-8", "claude-sonnet-4-6", "claude-haiku-4-5")
SUBAGENT_MODEL = "claude-haiku-4-5"
SYNTHESIS_MAX_TURNS = 20

# Manual override for the rotated parent model (``--main-model`` or this env var,
# for the cron). Unset → deterministic date-based rotation.
DOGFOOD_MAIN_MODEL_ENV = "DOGFOOD_MAIN_MODEL"


def _runstamp(now: datetime | None = None) -> str:
    """UTC stamp that sorts lexically by recency — drives ``latest_snapshot``."""
    return (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")


def select_main_model(runstamp: str, override: str | None = None) -> str:
    """Pick the synthesis parent model for this run.

    An explicit ``override`` (``--main-model`` / ``DOGFOOD_MAIN_MODEL``) wins so a
    manual run can pin one tier. Otherwise rotate deterministically across
    :data:`MAIN_MODELS` by the run's calendar date (the ``YYYYMMDD`` prefix of the
    runstamp): consecutive daily cron runs cycle opus → sonnet → haiku.

    Keyed off the date rather than a persistent counter so the choice is
    reproducible from a session's own timestamp (the map a #112 intended-vs-resolved
    cross-check wants) and so a missed cron day cannot corrupt a round-robin counter
    — at N=3 vs a 7-day week the skipped ordinals drift and even out.
    """
    if override:
        return override
    ordinal = datetime.strptime(runstamp[:8], "%Y%m%d").date().toordinal()
    return MAIN_MODELS[ordinal % len(MAIN_MODELS)]


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="dogfood_runner",
        description="Run AgentFluent's own dogfood analysis over a bounded rolling window.",
    )
    parser.add_argument(
        "--window",
        default=DEFAULT_WINDOW,
        help=f"Rolling analysis window passed to `analyze --since` (default: {DEFAULT_WINDOW}).",
    )
    parser.add_argument(
        "--fail-on",
        default="warning",
        choices=["info", "warning", "critical", "off"],
        help="Regression severity threshold for `diff` (default: warning).",
    )
    parser.add_argument(
        "--retention",
        type=int,
        default=DEFAULT_SNAPSHOT_RETENTION,
        help="Snapshots to keep per slug (default: %(default)s).",
    )
    parser.add_argument(
        "--no-synthesis",
        action="store_true",
        help="Run the deterministic gate only; skip the SDK narrative synthesis.",
    )
    parser.add_argument(
        "--main-model",
        default=None,
        help=(
            "Pin the synthesis parent model (overrides the date-based rotation "
            f"across {', '.join(MAIN_MODELS)}). Also settable via ${DOGFOOD_MAIN_MODEL_ENV}."
        ),
    )
    return parser.parse_args(argv)


async def synthesize(report: GateReport, main_model: str) -> tuple[str, str | None]:
    """Fan out Haiku subagents (one per snapshot) under a parent on ``main_model``;
    return ``(narrative, session_id)``. The ``session_id`` (off the SDK result
    stream) lets the caller record this run's corpus session against its main-model
    variant (#636). Lazy-imports the SDK so the gate never depends on it."""
    from claude_agent_sdk import (  # noqa: PLC0415 — lazy by design (research group)
        AgentDefinition,
        ClaudeAgentOptions,
        ResultMessage,
        query,
    )

    snapshots = [(r.slug, r.snapshot) for r in report.results if r.snapshot is not None]
    if not snapshots:
        return "(no snapshots produced this run — nothing to synthesize)", None

    # The subagent renders the snapshot with `agentfluent report` (the tool's OWN
    # deterministic interpreter of the JSON schema) and condenses that Markdown — it
    # does NOT interpret raw JSON itself, so it needs no data-dictionary/GLOSSARY
    # context and can't misread AgentFluent-specific signal jargon. Running `report`
    # via Bash also dogfoods that command and produces a real tool-driven subagent
    # trace (parent-Opus / child-Haiku divergence) for S5 (#595) and #112.
    summarizer = AgentDefinition(
        description="Renders one AgentFluent analyze snapshot via `report` and summarizes it.",
        prompt=(
            "You are given the path to an `agentfluent analyze --json` snapshot. "
            "Run `uv run agentfluent report <path>` with the Bash tool to render it "
            "to Markdown, then return a two-sentence summary of the most notable "
            "agent-quality signals (cost, tool errors, retries, diagnostics). "
            "Interpret the rendered report — do NOT parse the raw JSON yourself."
        ),
        tools=["Bash", "Read"],
        model=SUBAGENT_MODEL,
    )
    manifest = "\n".join(f"- {slug}: {path}" for slug, path in snapshots)
    prompt = (
        "You are synthesizing AgentFluent's daily dogfood report. For each snapshot "
        "below, delegate to the 'snapshot-summarizer' subagent (it renders the "
        "snapshot with `agentfluent report` and summarizes it), then write a short "
        "combined report highlighting the most notable agent-quality signals and any "
        "regressions across projects.\n\n"
        f"Snapshots:\n{manifest}"
    )
    options = ClaudeAgentOptions(
        model=main_model,
        # Bash so the subagents can run `agentfluent report`; Task/Agent to delegate.
        allowed_tools=["Read", "Bash", "Task", "Agent"],
        disallowed_tools=["WebFetch", "WebSearch"],
        mcp_servers={},
        setting_sources=[],  # pure SDK agent — no inherited ~/.claude env
        agents={"snapshot-summarizer": summarizer},
        cwd=str(REPO_DIR),  # so `uv run agentfluent report` resolves the project venv
        permission_mode="bypassPermissions",
        max_turns=SYNTHESIS_MAX_TURNS,
    )
    final = ""
    session_id: str | None = None
    async for message in query(prompt=prompt, options=options):
        if isinstance(message, ResultMessage):
            final = message.result or final
            session_id = message.session_id or session_id
    return final or "(synthesis produced no result text)", session_id


def _resolve_session_jsonl(session_id: str) -> str:
    """Absolute path of the corpus JSONL the SDK wrote for ``session_id``.

    Mirrors ``research/agent-sdk-probe/agent.py``: the synthesis ``query()`` runs
    with ``cwd=REPO_DIR``, so its session lands under the repo's project-slug.
    ``project_key_for_directory`` is an SDK import, kept lazy here so the SDK-free
    gate layer (``paths``/``cli_runner``) never transitively pulls it in.
    """
    from claude_agent_sdk import project_key_for_directory  # noqa: PLC0415 — lazy by design

    slug = project_key_for_directory(str(REPO_DIR))
    return str(Path.home() / ".claude" / "projects" / slug / f"{session_id}.jsonl")


def _record_run_best_effort(runstamp: str, main_model: str, session_id: str | None) -> None:
    """Append this run to the discoverability manifest — BEST-EFFORT.

    A manifest write must never flip the exit code (the same rule as synthesis:
    the deterministic gate owns pass/fail). A missing ``session_id`` or any I/O
    error is logged and swallowed, not surfaced as a failure.
    """
    if session_id is None:
        print(
            "[warn] no session_id from synthesis — run not recorded in manifest",
            file=sys.stderr,
        )
        return
    try:
        record_run(
            runstamp=runstamp,
            main_model=main_model,
            subagent_model=SUBAGENT_MODEL,
            session_id=session_id,
            session_jsonl=_resolve_session_jsonl(session_id),
        )
    except Exception as exc:  # noqa: BLE001 — recording must never flip the gate
        print(f"[warn] could not record run in manifest: {exc}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    runstamp = _runstamp()
    cli = CliRunner()
    report = run_gate(
        cli,
        window=args.window,
        runstamp=runstamp,
        retention=args.retention,
        fail_on=args.fail_on,
    )
    print(render_gate_report(report))

    if not args.no_synthesis:
        main_model = select_main_model(
            runstamp, args.main_model or os.environ.get(DOGFOOD_MAIN_MODEL_ENV)
        )
        session_id: str | None = None
        try:
            narrative, session_id = asyncio.run(synthesize(report, main_model))
            print(f"\n--- synthesis (main model: {main_model}) ---\n" + narrative)
        except Exception as exc:  # noqa: BLE001 — synthesis must never flip the gate
            print(f"\n[warn] narrative synthesis skipped: {exc}", file=sys.stderr)
        # Record whether or not synthesis raised: a None session_id just no-ops.
        _record_run_best_effort(runstamp, main_model, session_id)

    # Exit code reflects ANALYSIS health only. A regression is a successful dogfood
    # that found something (surfaced in the report), not a runner failure.
    return 1 if report.is_red else 0


if __name__ == "__main__":
    raise SystemExit(main())
