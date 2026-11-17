"""Corpus check for trace-sourced tool-use count and duration (#690).

Two reports per corpus:

**AC7 parity.** On invocations that carry both a ``toolUseResult`` rollup and a
linked subagent trace, compare the rollup's tool-use count and duration with the
trace's. The rollup is read from ``extract_agent_invocations`` output, which has
not been through trace linking, so the comparison stays meaningful after #690
makes the linked invocation's own ``tool_uses`` / ``duration_ms`` trace-sourced
(a check against the linked invocation would compare the trace with itself).

**AC9 movement.** The values #690 is expected to move: ``avg_tool_calls_per_turn``
(corpus level), active-duration coverage against ``invocation_count``, trace-linked
invocations whose duration or idle gap is ``None``, the agent-type duration cells
that are highlighted or carry the partial-coverage dagger, model-routing
complexity tiers per agent type, and diagnostics signal counts.

Run it once on the code before the change and once after, then compare:

    uv run python scripts/trace_sourcing_corpus_check.py --label after
    PYTHONPATH=/path/to/main-worktree/src \\
        uv run python scripts/trace_sourcing_corpus_check.py --label before

The first output line names the ``agentfluent`` package actually imported, so a
before/after pair can be checked for which code produced it. Diagnostics run with
the inputs ``agentfluent analyze --diagnostics`` passes, except the project's
source directory, which is not resolved here.

Corpora default to the four used in #690; pass ``--corpus NAME=DIR`` (DIR
relative to ``~/.claude/projects``, or absolute) to choose others. ``--json PATH``
writes the results for diffing. A corpus that cannot be read is reported on
stderr and skipped.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import agentfluent
from agentfluent.agents.extractor import extract_agent_invocations
from agentfluent.agents.models import AgentInvocation
from agentfluent.analytics.agent_metrics import AgentTypeMetrics
from agentfluent.analytics.pipeline import analyze_sessions
from agentfluent.cli.formatters.helpers import format_agent_duration_cell
from agentfluent.core.discovery import discover_sessions
from agentfluent.diagnostics.model_routing import aggregate_agent_stats, classify_complexity
from agentfluent.diagnostics.pipeline import run_diagnostics

PROJECTS_DIR = Path.home() / ".claude" / "projects"
DEFAULT_CORPORA = {
    "agentfluent": "-home-fdpearce-Documents-Projects-git-agentfluent",
    "claude-code-loop": "-home-fdpearce-Documents-Projects-git-claude-code-loop",
    "claude-code-sessions": "-home-fdpearce-Documents-Projects-git-claude-code-sessions",
    "us-presidential-vote-analysis": (
        "-home-fdpearce-Documents-Projects-git-us-presidential-vote-analysis"
    ),
}


def _parity(
    unlinked: dict[str, AgentInvocation], linked: list[AgentInvocation],
) -> dict[str, Any]:
    """AC7: rollup (pre-link) vs trace, on invocations that carry both."""
    both = exact = 0
    mismatches: list[int] = []
    duration_deltas_s: list[float] = []
    for inv in linked:
        trace = inv.trace
        rollup = unlinked.get(inv.tool_use_id)
        if trace is None or rollup is None or rollup.tool_uses is None:
            continue
        both += 1
        trace_count = len(trace.tool_calls)
        if rollup.tool_uses == trace_count:
            exact += 1
        else:
            mismatches.append(rollup.tool_uses - trace_count)
        if rollup.duration_ms is not None and trace.duration_ms is not None:
            duration_deltas_s.append((rollup.duration_ms - trace.duration_ms) / 1000)
    report: dict[str, Any] = {
        "rollup_and_trace": both,
        "tool_count_exact_match": f"{exact}/{both}" + (f" = {exact / both:.1%}" if both else ""),
        "tool_count_mismatches_rollup_minus_trace": sorted(mismatches),
    }
    if len(duration_deltas_s) >= 2:
        deciles = statistics.quantiles(duration_deltas_s, n=10)
        report["duration_rollup_minus_trace_s"] = {
            "median": round(statistics.median(duration_deltas_s), 2),
            "p10": round(deciles[0], 2),
            "p90": round(deciles[-1], 2),
        }
    return report


def _duration_cells(by_type: dict[str, AgentTypeMetrics]) -> dict[str, int]:
    cells = [
        format_agent_duration_cell(
            total_duration_ms=m.total_duration_ms,
            invocation_count=m.invocation_count,
            total_active_duration_ms=m.total_active_duration_ms,
            total_wallclock_ms_trace_linked=m.total_wallclock_ms_trace_linked,
            active_duration_invocation_count=m.active_duration_invocation_count,
        )
        for m in by_type.values()
    ]
    return {
        "agent_types": len(cells),
        "highlighted": sum(c.highlight for c in cells),
        "partial_dagger": sum(c.partial for c in cells),
        "unreliable_star": sum(c.unreliable for c in cells),
    }


def check_corpus(corpus_dir: Path) -> dict[str, Any]:
    paths = [info.path for info in discover_sessions(corpus_dir)]
    result = analyze_sessions(paths)
    invocations = [inv for s in result.sessions for inv in s.invocations]
    unlinked = {
        inv.tool_use_id: inv
        for s in result.sessions
        for inv in extract_agent_invocations(s.messages)
    }
    linked = [inv for inv in invocations if inv.trace is not None]

    diagnostics = run_diagnostics(
        invocations,
        mcp_tool_calls=[c for s in result.sessions for c in s.mcp_tool_calls],
        parent_messages=[m for s in result.sessions for m in s.messages],
        session_count=result.session_count,
        sessions=result.sessions,
    )
    signals = Counter(str(sig.signal_type) for sig in diagnostics.signals)

    by_type = result.agent_metrics.by_agent_type
    total_turns = sum(m.total_model_turns for m in by_type.values())
    # Pre-#690 code has no coverage-matched numerator; its published ratio
    # divides the all-invocation tool sum, so fall back to that.
    turn_numerator = sum(
        getattr(m, "total_tool_uses_with_turns", m.total_tool_uses) for m in by_type.values()
    )
    tiers = {
        stats.agent_type: classify_complexity(stats)
        for stats in aggregate_agent_stats(invocations, None).values()
    }

    return {
        "sessions": result.session_count,
        "invocations": len(invocations),
        "trace_linked": len(linked),
        "rollup_present": sum(1 for inv in unlinked.values() if inv.tool_uses is not None),
        "ac7_parity": _parity(unlinked, invocations),
        "avg_tool_calls_per_turn": (
            round(turn_numerator / total_turns, 3) if total_turns else None
        ),
        "invocations_with_tool_uses": sum(1 for i in invocations if i.tool_uses is not None),
        "invocations_with_duration": sum(1 for i in invocations if i.duration_ms is not None),
        "active_duration_invocation_count": sum(
            m.active_duration_invocation_count for m in by_type.values()
        ),
        "trace_linked_duration_none": sum(1 for i in linked if i.trace.duration_ms is None),  # type: ignore[union-attr]
        "trace_linked_idle_gap_none": sum(1 for i in linked if i.idle_gap_ms is None),
        "duration_cells": _duration_cells(by_type),
        "complexity_tiers": dict(sorted(Counter(tiers.values()).items())),
        "complexity_tier_by_type": dict(sorted(tiers.items())),
        "signals": dict(sorted(signals.items())),
    }


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--label", default="run", help="name for this run")
    parser.add_argument(
        "--corpus", action="append", default=[], metavar="NAME=DIR",
        help="corpus to check (repeatable); defaults to the #690 four",
    )
    parser.add_argument("--json", type=Path, help="write results to this file")
    args = parser.parse_args(argv)
    for corpus in args.corpus:
        name, sep, rel_dir = corpus.partition("=")
        if not (sep and name and rel_dir):
            parser.error(f"--corpus expects NAME=DIR, got {corpus!r}")
    return args


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    corpora = dict(c.split("=", 1) for c in args.corpus) or DEFAULT_CORPORA
    print(f"[{args.label}] agentfluent imported from {Path(agentfluent.__file__).parent}")

    results: dict[str, Any] = {}
    for name, rel_dir in corpora.items():
        corpus_dir = PROJECTS_DIR / rel_dir
        if not corpus_dir.is_dir():
            print(f"skip {name}: {corpus_dir} not found", file=sys.stderr)
            continue
        try:
            results[name] = check_corpus(corpus_dir)
        except (OSError, UnicodeDecodeError) as exc:
            print(f"error: skipping corpus {name}: {exc}", file=sys.stderr)
            continue
        print(f"== {name}")
        for key, value in results[name].items():
            print(f"   {key}: {value}")

    if args.json:
        try:
            args.json.write_text(
                json.dumps({"label": args.label, "corpora": results}, indent=2, default=str),
            )
        except OSError as exc:
            print(f"error: cannot write {args.json}: {exc}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
