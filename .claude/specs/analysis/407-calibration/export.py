"""Throwaway: export TOOL_ORCHESTRATION_CHAIN matching invocations for #407.

Mirrors analytics.pipeline per-session loading + trace linking, applies the
_is_orchestration_chain predicate, and dumps a TSV + per-detection context
(trace turn count, distinct tools, task description) for manual TP/FP review.
"""
from __future__ import annotations

import json
from pathlib import Path

from agentfluent.agents.extractor import extract_agent_invocations
from agentfluent.core.parser import parse_session
from agentfluent.diagnostics.tool_orchestration import (
    _MIN_TOKENS_PER_TOOL_CALL,
    _MIN_TOOL_CALLS,
    _is_orchestration_chain,
)
from agentfluent.traces.discovery import discover_session_subagents
from agentfluent.traces.linker import link_traces
from agentfluent.traces.parser import parse_subagent_trace

PROJECTS = Path.home() / ".claude" / "projects"
DIRS = [
    "-home-fdpearce-Documents-Projects-git-agentfluent",
    "-home-fdpearce-Documents-Projects-git-codefluent-codefluent",
]


def link(invocations, session_path):
    session_dir = session_path.parent / session_path.stem
    path_map = {i.agent_id: i.path for i in discover_session_subagents(session_dir)}

    def loader(agent_id):
        fp = path_map.get(agent_id)
        if fp is None:
            return None
        try:
            return parse_subagent_trace(fp)
        except (FileNotFoundError, ValueError):
            return None

    return link_traces(invocations, loader)


def trace_tools(trace):
    """Distinct tool names + count from a linked subagent trace, if any."""
    if trace is None:
        return None
    names: dict[str, int] = {}
    for msg in getattr(trace, "messages", []) or []:
        content = getattr(getattr(msg, "message", None), "content", None)
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                names[block.get("name", "?")] = names.get(block.get("name", "?"), 0) + 1
    return names


rows = []
for d in DIRS:
    proj = PROJECTS / d
    if not proj.exists():
        print(f"MISSING: {proj}")
        continue
    for session_path in sorted(proj.glob("*.jsonl")):
        try:
            messages = parse_session(session_path)
        except Exception as e:  # noqa: BLE001
            print(f"parse fail {session_path.name}: {e}")
            continue
        invs = extract_agent_invocations(messages)
        if not invs:
            continue
        invs = link(invs, session_path)
        for inv in invs:
            if _is_orchestration_chain(inv):
                rows.append((d, session_path.stem, inv))

print(f"\nThresholds: tool_calls>={_MIN_TOOL_CALLS}, tokens/call>{_MIN_TOKENS_PER_TOOL_CALL}")
print(f"Total matching invocations: {len(rows)}\n")

# Aggregate per agent_type (how the signal actually gates: >=3 to emit)
by_type: dict[str, int] = {}
for _, _, inv in rows:
    by_type[inv.agent_type.lower()] = by_type.get(inv.agent_type.lower(), 0) + 1
print("Matching invocations per agent_type (>=3 emits a signal):")
for t, n in sorted(by_type.items(), key=lambda x: -x[1]):
    flag = "  <-- EMITS" if n >= 3 else ""
    print(f"  {t:20s} {n}{flag}")

out = Path("/tmp/orchestration_407.tsv")
with out.open("w") as f:
    f.write("idx\tcorpus\tsession\tagent_type\ttool_uses\ttotal_tokens\ttokens_per_call\thas_trace\tturns\tdescription\n")
    for i, (corpus, session, inv) in enumerate(rows, 1):
        c = "af" if "agentfluent" in corpus else "cf"
        turns = inv.model_turns if inv.model_turns is not None else ""
        desc = (inv.description or "").replace("\t", " ").replace("\n", " ")[:90]
        f.write(
            f"{i}\t{c}\t{session[:8]}\t{inv.agent_type}\t{inv.tool_uses}\t"
            f"{inv.total_tokens}\t{inv.tokens_per_tool_use:.0f}\t"
            f"{(inv.trace is not None)}\t{turns}\t{desc}\n"
        )
print(f"\nWrote {out}")

# Dump rich per-detection context for classification
ctx = Path("/tmp/orchestration_407_context.json")
detail = []
for i, (corpus, session, inv) in enumerate(rows, 1):
    detail.append({
        "idx": i,
        "corpus": "af" if "agentfluent" in corpus else "cf",
        "session": session[:8],
        "agent_type": inv.agent_type,
        "tool_uses": inv.tool_uses,
        "total_tokens": inv.total_tokens,
        "tokens_per_call": round(inv.tokens_per_tool_use, 0),
        "has_trace": (inv.trace is not None),
        "model_turns": inv.model_turns,
        "trace_tools": trace_tools(inv.trace),
        "description": inv.description,
        "prompt_head": (inv.prompt or "")[:500],
    })
ctx.write_text(json.dumps(detail, indent=2, default=str))
print(f"Wrote {ctx}")
