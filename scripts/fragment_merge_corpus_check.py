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

Corpora default to the four used in #694; pass ``--corpus NAME=DIR`` (DIR
relative to ``~/.claude/projects``) to choose others. ``--json PATH`` writes
the results for diffing.
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
from agentfluent.core.discovery import discover_sessions
from agentfluent.core.parser import parse_session
from agentfluent.diagnostics.pipeline import run_diagnostics
from agentfluent.traces.models import SubagentTrace

PROJECTS_DIR = Path.home() / ".claude" / "projects"
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


def _id_counts(corpus_dir: Path) -> dict[str, int]:
    counts = Counter[str]()
    for path in sorted(corpus_dir.rglob("*.jsonl")):
        kind = "sub" if "subagents" in path.parts else "main"
        counts[f"{kind}_raw_tool_use_ids"] += len(_raw_tool_use_ids(path))
        counts[f"{kind}_parsed_tool_use_ids"] += len(_parsed_tool_use_ids(path))
    return dict(counts)


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

    diagnostics = run_diagnostics(invocations, sessions=result.sessions)
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
        "signals": dict(sorted(signals.items())),
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
        results[name] = check_corpus(corpus_dir)
        print(f"== {name}")
        for key, value in results[name].items():
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
