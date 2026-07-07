"""Dogfood-runner entrypoint (S0 / #590): gate + SDK narrative synthesis.

Run it directly (this is the scheduler-agnostic entrypoint a cron entry fires)::

    uv run --group research python tools/dogfood_runner/runner.py
    uv run --group research python tools/dogfood_runner/runner.py --window 7d
    uv run python tools/dogfood_runner/runner.py --no-synthesis   # gate only, no SDK

Two layers, deliberately separated (architect review, #590):

1. **The gate** (:func:`tools.dogfood_runner.cli_runner.run_gate`) — deterministic,
   drives the real ``agentfluent`` CLI, owns pass/fail by reading exit codes. Runs
   with or without the ``research`` group; ``--no-synthesis`` stops here.
2. **Narrative synthesis** — an Agent SDK ``query()`` (parent Opus) fans out one
   Haiku subagent per slug to summarize that slug's snapshot, and the parent
   stitches a report. Best-effort: a synthesis failure is logged but NEVER flips
   the gate (the gate is about analysis correctness, not the narrative). The
   parent-Opus / child-Haiku split is intentional — it emits the model-divergence
   and nested-trace bytes that S5 (#595) and #112 will consume, so the runner
   dogfoods the exact v0.11 surfaces.

``claude_agent_sdk`` is imported lazily inside :func:`synthesize` so this module
imports (and the gate runs) without the ``research`` dependency-group installed.

Not part of the published ``agentfluent`` package.
"""

from __future__ import annotations

import argparse
import asyncio
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

# Repo root — the subagents run `uv run agentfluent report` here, so their cwd must
# resolve the project venv. tools/dogfood_runner/runner.py → parents[2] is the root.
REPO_DIR = Path(__file__).resolve().parents[2]

# Non-dated aliases on purpose: a dated model pin (e.g. ``claude-haiku-4-5-20251001``)
# starts long-hanging then 529s once that snapshot is retired. Centralized here so
# there is one place to bump. Current tiers: Opus 4.8 parent, Haiku 4.5 subagent.
PARENT_MODEL = "claude-opus-4-8"
SUBAGENT_MODEL = "claude-haiku-4-5"
SYNTHESIS_MAX_TURNS = 20


def _runstamp(now: datetime | None = None) -> str:
    """UTC stamp that sorts lexically by recency — drives ``latest_snapshot``."""
    return (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")


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
    return parser.parse_args(argv)


async def synthesize(report: GateReport) -> str:
    """Fan out Haiku subagents (one per snapshot) under an Opus parent; return the
    synthesized narrative. Lazy-imports the SDK so the gate never depends on it."""
    from claude_agent_sdk import (  # noqa: PLC0415 — lazy by design (research group)
        AgentDefinition,
        ClaudeAgentOptions,
        ResultMessage,
        query,
    )

    snapshots = [(r.slug, r.snapshot) for r in report.results if r.snapshot is not None]
    if not snapshots:
        return "(no snapshots produced this run — nothing to synthesize)"

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
        model=PARENT_MODEL,
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
    async for message in query(prompt=prompt, options=options):
        if isinstance(message, ResultMessage):
            final = message.result or final
    return final or "(synthesis produced no result text)"


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    cli = CliRunner()
    report = run_gate(
        cli,
        window=args.window,
        runstamp=_runstamp(),
        retention=args.retention,
        fail_on=args.fail_on,
    )
    print(render_gate_report(report))

    if not args.no_synthesis:
        try:
            narrative = asyncio.run(synthesize(report))
            print("\n--- synthesis ---\n" + narrative)
        except Exception as exc:  # noqa: BLE001 — synthesis must never flip the gate
            print(f"\n[warn] narrative synthesis skipped: {exc}", file=sys.stderr)

    # Exit code reflects ANALYSIS health only. A regression is a successful dogfood
    # that found something (surfaced in the report), not a runner failure.
    return 1 if report.is_red else 0


if __name__ == "__main__":
    raise SystemExit(main())
