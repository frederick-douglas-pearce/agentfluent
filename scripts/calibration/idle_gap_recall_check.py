"""Idle-gap subtraction recall on the "obviously-stuck" trace set (#697).

Re-checks the regression guard from ``threshold_validation.ipynb`` §11 against
the production heuristic (``agentfluent.traces.parser._compute_idle_gap_ms``)
rather than the notebook's own re-implementation of the pre-#697 per-call
logic.

The stuck set uses §11's definition, rebuilt from the local corpus: a trace
with at least two timed tool calls whose span (earliest ``tool_use`` to latest
``tool_result``) exceeds 10 minutes and whose single biggest per-call gap is
more than half of that span. A stuck trace is *caught* when the production
heuristic subtracts a nonzero idle gap from it.

    uv run python scripts/calibration/idle_gap_recall_check.py

Point ``PYTHONPATH`` at another checkout's ``src`` to measure that code instead;
the first output line names the ``agentfluent`` package actually imported.
``--root`` overrides the projects directory (default ``~/.claude/projects``).
A trace file that cannot be parsed is reported on stderr and skipped.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import agentfluent
from agentfluent.traces.models import SubagentTrace
from agentfluent.traces.parser import parse_subagent_trace

STUCK_MIN_SPAN_MS = 10 * 60_000
STUCK_MIN_BIGGEST_SHARE = 0.5


def is_obviously_stuck(trace: SubagentTrace) -> bool:
    """§11's definition, computed per call as the notebook does."""
    timed = [
        tc for tc in trace.tool_calls
        if tc.timestamp is not None and tc.result_timestamp is not None
    ]
    if len(timed) < 2:
        return False
    span_ms = (
        max(tc.result_timestamp for tc in timed if tc.result_timestamp)
        - min(tc.timestamp for tc in timed if tc.timestamp)
    ).total_seconds() * 1000
    if span_ms <= STUCK_MIN_SPAN_MS:
        return False
    biggest_ms = max(
        (tc.result_timestamp - tc.timestamp).total_seconds() * 1000
        for tc in timed
        if tc.timestamp is not None and tc.result_timestamp is not None
    )
    return biggest_ms / span_ms > STUCK_MIN_BIGGEST_SHARE


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--root", type=Path, default=Path.home() / ".claude" / "projects",
        help="projects directory to scan",
    )
    args = parser.parse_args(argv)
    print(f"agentfluent imported from {Path(agentfluent.__file__).parent}")

    stuck: list[tuple[str, SubagentTrace]] = []
    for path in sorted(args.root.glob("*/*/subagents/agent-*.jsonl")):
        try:
            trace = parse_subagent_trace(path)
        except (OSError, ValueError, TypeError) as exc:
            print(f"skip {path.name}: {exc}", file=sys.stderr)
            continue
        if is_obviously_stuck(trace):
            stuck.append((f"{path.parent.parent.parent.name}/{trace.agent_id}", trace))

    missed = [name for name, trace in stuck if not trace.idle_gap_ms]
    print(f"obviously-stuck traces: {len(stuck)}")
    print(f"caught (idle_gap_ms > 0): {len(stuck) - len(missed)} / {len(stuck)}")
    for name in missed:
        trace = next(t for n, t in stuck if n == name)
        # A call without a turn_index is its own turn, as in the heuristic.
        timed_turns = {
            ("call", position) if tc.turn_index is None else ("turn", tc.turn_index)
            for position, tc in enumerate(trace.tool_calls)
            if tc.timestamp is not None and tc.result_timestamp is not None
        }
        print(f"  missed: {name} idle_gap_ms={trace.idle_gap_ms} "
              f"timed_turns={len(timed_turns)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
