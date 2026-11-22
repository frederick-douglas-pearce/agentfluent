"""Corpus check for the assistant-fragment merge (#694).

Reports, per corpus, how many unique ``tool_use`` ids the raw JSONL holds
versus how many survive ``parse_session``'s fragment merge, split into main
sessions and subagent traces. Alongside that it reports the totals the merge
must NOT move (session and trace tokens and cost) and the values it is
expected to move (trace tool counts, retries, errors, active duration, and
diagnostics signal counts).

Run it once on the code before a merge change and once after, then compare:

    uv run python scripts/fragment_merge_corpus_check.py --label after
    PYTHONPATH=/path/to/main-worktree/src \\
        uv run python scripts/fragment_merge_corpus_check.py --label before

The first output line names the ``agentfluent`` package actually imported, so
a before/after pair can be checked for which code produced it.

Diagnostics are run with the inputs ``agentfluent analyze --diagnostics``
passes (parent messages, parent MCP calls, session count, sessions), except
the project's source directory, which is not resolved here -- so the
config-scan audits read only the user-level Claude config.

``cumulative_path_groups`` counts multi-line message groups the merge reads
as legacy cumulative snapshots (``None`` on code that predates that rule).
A nonzero value means the two accepted ambiguities documented on
``core.parser._merge_fragment_group`` may be live in that corpus.

Corpora default to the four used in #694; pass ``--corpus NAME=DIR`` (DIR
relative to ``~/.claude/projects``, or absolute) to choose others.
``trace_active_clamped_zero`` counts linked traces whose idle gap exceeds
their span (the clamp in ``active_duration_ms`` fired), ``trace_active_zero``
those whose active duration is 0 for any reason (clamped, or idle exactly
equal to the span), and ``trace_idle_gap_none`` those with a span but no computable
idle gap (#697); ``--json`` also carries ``idle_gap_ms_by_trace`` so a
before/after pair can be diffed for traces whose idle gap became ``None``.

``--json PATH`` writes the results for diffing. A corpus or file that cannot
be read is reported on stderr and skipped.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import agentfluent
from agentfluent.analytics.pipeline import analyze_sessions
from agentfluent.core import parser as core_parser
from agentfluent.core.discovery import discover_sessions
from agentfluent.core.parser import parse_session
from agentfluent.core.session import SessionMessage
from agentfluent.diagnostics.pipeline import run_diagnostics
from agentfluent.traces.models import SubagentTrace

PROJECTS_DIR = Path.home() / ".claude" / "projects"
# Per-trace idle gap, written to --json only (not printed), so two runs can be
# diffed for traces whose idle gap became None (#697).
PER_TRACE_KEY = "idle_gap_ms_by_trace"
DEFAULT_CORPORA = {
    "agentfluent": "-home-fdpearce-Documents-Projects-git-agentfluent",
    "claude-code-loop": "-home-fdpearce-Documents-Projects-git-claude-code-loop",
    "claude-code-sessions": "-home-fdpearce-Documents-Projects-git-claude-code-sessions",
    "us-presidential-vote-analysis": (
        "-home-fdpearce-Documents-Projects-git-us-presidential-vote-analysis"
    ),
}


def _raw_tool_use_ids(path: Path) -> set[str]:
    """Unique ``tool_use`` ids in assistant lines, read straight from JSONL."""
    ids: set[str] = set()
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(record, dict) or record.get("type") != "assistant":
                    continue
                content = (record.get("message") or {}).get("content")
                if not isinstance(content, list):
                    continue
                for block in content:
                    if (
                        isinstance(block, dict)
                        and block.get("type") == "tool_use"
                        and block.get("id")
                    ):
                        ids.add(block["id"])
    except OSError as exc:
        print(f"warning: cannot read {path}: {exc}", file=sys.stderr)
    return ids


def _parsed_tool_use_ids(path: Path) -> set[str]:
    """Unique ``tool_use`` ids that survive ``parse_session``'s merge."""
    return {
        block.id
        for message in parse_session(path)
        if message.type == "assistant"
        for block in message.content_blocks
        if block.type == "tool_use" and block.id
    }


def _cumulative_path_groups(path: Path) -> int | None:
    """Multi-line message groups the merge would read as cumulative snapshots."""
    is_cumulative = getattr(core_parser, "_is_cumulative_snapshot_group", None)
    if is_cumulative is None:
        return None
    groups: dict[str, list[SessionMessage]] = {}
    for message in parse_session(path, deduplicate=False):
        if message.type == "assistant" and message.message_id:
            groups.setdefault(message.message_id, []).append(message)
    return sum(1 for g in groups.values() if len(g) > 1 and is_cumulative(g))


def _id_counts(corpus_dir: Path) -> dict[str, int | None]:
    counts = Counter[str]()
    cumulative: int | None = 0
    for path in sorted(corpus_dir.rglob("*.jsonl")):
        kind = "sub" if "subagents" in path.relative_to(corpus_dir).parts else "main"
        try:
            parsed = _parsed_tool_use_ids(path)
            groups = _cumulative_path_groups(path)
        except (OSError, UnicodeDecodeError) as exc:
            print(f"warning: skipping {path}: {exc}", file=sys.stderr)
            continue
        counts[f"{kind}_raw_tool_use_ids"] += len(_raw_tool_use_ids(path))
        counts[f"{kind}_parsed_tool_use_ids"] += len(parsed)
        cumulative = None if groups is None or cumulative is None else cumulative + groups
    return {**counts, "cumulative_path_groups": cumulative}


def _same_turn_retry_sequences(trace: SubagentTrace) -> int:
    """Retry runs containing two adjacent calls from one assistant message.

    Calls merged from one message share its first line's timestamp, so two
    adjacent members with the same timestamp were issued in parallel -- one
    could not have seen the other's result, so it is not a retry of it.
    """
    count = 0
    for sequence in trace.retry_sequences:
        stamps = [trace.tool_calls[i].timestamp for i in sequence.tool_call_indices]
        if any(
            a is not None and a == b for a, b in zip(stamps, stamps[1:], strict=False)
        ):
            count += 1
    return count


def check_corpus(corpus_dir: Path) -> dict[str, Any]:
    paths = [info.path for info in discover_sessions(corpus_dir)]
    result = analyze_sessions(paths)
    invocations = [inv for s in result.sessions for inv in s.invocations]
    traces = [inv.trace for inv in invocations if inv.trace is not None]

    diagnostics = run_diagnostics(
        invocations,
        mcp_tool_calls=[c for s in result.sessions for c in s.mcp_tool_calls],
        parent_messages=[m for s in result.sessions for m in s.messages],
        session_count=result.session_count,
        sessions=result.sessions,
    )
    signals = Counter(str(sig.signal_type) for sig in diagnostics.signals)

    tokens = result.token_metrics
    agents = result.agent_metrics
    return {
        "sessions": result.session_count,
        **_id_counts(corpus_dir),
        "session_total_tokens": tokens.total_tokens,
        "session_total_cost_usd": round(tokens.total_cost, 6),
        "api_call_count": tokens.api_call_count,
        "agent_spend_tokens": sum(m.spend_tokens for m in agents.by_agent_type.values()),
        "agent_cost_usd": round(
            sum(m.estimated_total_cost_usd for m in agents.by_agent_type.values()), 6,
        ),
        "trace_count": len(traces),
        "trace_usage_tokens": sum(t.usage.total_tokens for t in traces),
        "trace_tool_calls": sum(len(t.tool_calls) for t in traces),
        "trace_errors": sum(t.total_errors for t in traces),
        "trace_retries": sum(t.total_retries for t in traces),
        "trace_retry_sequences": sum(len(t.retry_sequences) for t in traces),
        "same_turn_retry_sequences": sum(_same_turn_retry_sequences(t) for t in traces),
        "main_tool_calls": result.tool_metrics.total_tool_calls,
        "total_active_duration_ms": sum(
            m.total_active_duration_ms for m in agents.by_agent_type.values()
        ),
        "active_duration_invocations": sum(
            m.active_duration_invocation_count for m in agents.by_agent_type.values()
        ),
        "trace_active_clamped_zero": sum(
            1
            for t in traces
            if t.duration_ms is not None
            and t.idle_gap_ms is not None
            and t.idle_gap_ms > t.duration_ms
        ),
        "trace_active_zero": sum(
            1 for t in traces if t.duration_ms and t.active_duration_ms == 0
        ),
        "trace_idle_gap_none": sum(
            1 for t in traces if t.duration_ms is not None and t.idle_gap_ms is None
        ),
        "trace_idle_gap_ms": sum(t.idle_gap_ms or 0 for t in traces),
        "signals": dict(sorted(signals.items())),
        PER_TRACE_KEY: {
            f"{t.source_file.parent.parent.name}/{t.agent_id}": t.idle_gap_ms
            for t in traces
            if t.source_file is not None
        },
    }


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--label", default="run", help="name for this run")
    parser.add_argument(
        "--corpus", action="append", default=[], metavar="NAME=DIR",
        help="corpus to check (repeatable); defaults to the #694 four",
    )
    parser.add_argument("--json", type=Path, help="write results to this file")
    return parser.parse_args(argv)


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
            if key != PER_TRACE_KEY:
                print(f"   {key}: {value}")

    if args.json:
        try:
            args.json.write_text(
                json.dumps({"label": args.label, "corpora": results}, indent=2),
            )
        except OSError as exc:
            print(f"error: cannot write {args.json}: {exc}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
