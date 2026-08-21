"""Resolve the delegation lineage of subagent traces (#595 PR B).

The on-disk layout is **flat at all depths**: every trace in a session is a
sibling under ``subagents/``, whatever spawned it. So "which agent spawned
this one" is not recoverable from path shape -- only from data. This module
computes that edge, and the ``depth`` it implies, for every trace in a
session.

**The join, and why the sidecar carries it.** Each trace has an
``agent-<agentId>.meta.json`` sidecar whose ``toolUseId`` names the ``Agent``
``tool_use`` block that spawned it. Resolving that label to an *emitter*
answers the parent question:

* the emitter is the **main session** -> the trace is depth 1, and its parent
  is the ``AgentInvocation`` carrying that ``tool_use_id``;
* the emitter is a **sibling trace** -> the trace is depth >= 2, and its
  parent is that trace's agent.

At depth 1 the same edge is also recoverable from ``toolUseResult.agentId``,
but a depth->=2 ``tool_result`` carries no ``toolUseResult`` at all (only an
inline prose trailer this package deliberately does not parse), which is what
makes the sidecar the only *structured* child-to-parent edge below depth 1.

**Cost.** The sidecar check is the cheap path and resolves every depth-1
trace, which is the overwhelming majority (measured on a real corpus:
16/1333 traces, 1.2%, are depth >= 2). Sibling traces are opened to build the
emitter index only when **both** hold: some sidecar ``toolUseId`` failed to
resolve against the parent session, **and** at least one depth-1 root exists
to walk back to. With no root every lineage degrades to ``(None, 1)``
whatever the index says, so building it would read every trace file to
produce a result that cannot change. Sessions with no nesting never pay it,
and the index is built once per session, not once per unresolved id.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from agentfluent.traces.discovery import SubagentFileInfo
from agentfluent.traces.sidecar import read_subagent_sidecar

logger = logging.getLogger(__name__)

MAX_DELEGATION_DEPTH = 64
"""Hard ceiling on the parent walk.

A cycle is *structurally* unrepresentable in this format -- a ``tool_use.id``
is emitted once by one file, and a child file is created after the
``tool_use`` that spawned it, so the emitter relation is acyclic by
construction. This guard is therefore not defending against a state the
format can produce; it defends against **corrupt input**, where an unbounded
walk would be an infinite loop inside ``analyze``. Paired with the visited-set
check in ``_walk_depth``, which catches a true cycle in fewer steps.
"""


@dataclass(frozen=True)
class TraceLineage:
    """Resolved lineage for one subagent trace."""

    parent_invocation_id: str | None
    depth: int
    agent_type: str = ""
    """Agent type as named by the spawning side, from the sidecar.

    Empty when there is no sidecar or it carried none. Load-bearing only at
    depth >= 2: a depth-1 trace inherits the authoritative value from its
    ``AgentInvocation`` via ``link_traces``, but a deeper agent **has no
    invocation row to inherit from**, so the sidecar is its only source. Left
    empty, every depth->=2 trace this exposes would serialize as ``unknown``
    -- the one field that makes the ``children`` array usable."""


def emitted_tool_use_ids(trace_path: Path) -> set[str]:
    """Return the ``tool_use`` block ids emitted by one trace file.

    Scans for ``type == "assistant"`` lines and collects ``tool_use`` block
    ids. Deliberately a narrow scan rather than a full ``parse_subagent_trace``
    call: this runs only on the depth->=2 escalation path, and building a
    whole ``SubagentTrace`` to read a handful of ids would reintroduce the
    eager parse the sidecar probe exists to avoid.

    Total by contract -- an unreadable or malformed file yields an empty set
    rather than raising, matching ``read_subagent_sidecar``'s posture. A trace
    we cannot read simply emits nothing, which degrades a child to
    unattributable instead of failing the session.
    """
    ids: set[str] = set()
    try:
        with trace_path.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                # Cheap reject before paying for json.loads: the vast majority
                # of lines in a trace carry no tool_use block at all.
                if '"tool_use"' not in line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(obj, dict) or obj.get("type") != "assistant":
                    continue
                message = obj.get("message")
                if not isinstance(message, dict):
                    # `"message": null` is real in the corpus and would raise
                    # AttributeError on .get() -- which `except OSError` below
                    # does not catch, so one bad line in one trace file would
                    # take down `analyze` for the whole project. Totality is
                    # this function's contract; check the type, do not assume.
                    continue
                content = message.get("content", [])
                if not isinstance(content, list):
                    continue
                for block in content:
                    if (
                        isinstance(block, dict)
                        and block.get("type") == "tool_use"
                        and isinstance(block.get("id"), str)
                    ):
                        ids.add(block["id"])
    except OSError as exc:
        logger.debug("Unreadable subagent trace while indexing emitters %s: %s",
                     trace_path, exc)
    return ids


def build_emitter_index(files: Iterable[SubagentFileInfo]) -> dict[str, str]:
    """Map ``tool_use.id -> agent_id of the trace that emitted it``.

    Only trace-emitted ids appear. An id emitted by the **main session** is
    absent by construction, which is exactly what makes membership here the
    depth->=2 test: resolve against the parent session first, and anything
    left that hits this index was spawned by another agent.

    Exposed as a reusable unit rather than an internal of
    ``resolve_lineage``: #648 AC1/AC4's coverage counter needs the same
    emitter resolution to split structurally-recoverable depth->=2 traces from
    true orphans, and two implementations of this would drift.
    """
    index: dict[str, str] = {}
    for info in files:
        for tool_use_id in emitted_tool_use_ids(info.path):
            index[tool_use_id] = info.agent_id
    return index


def _walk_depth(
    agent_id: str,
    parent_agent_of: dict[str, str],
    depth_1_agents: set[str],
) -> int | None:
    """Depth of ``agent_id`` by walking parent edges, or ``None`` if unresolvable.

    ``None`` means the chain ran into a cycle, exceeded
    ``MAX_DELEGATION_DEPTH``, or reached an agent with no known parent that is
    not itself depth 1. Callers degrade to ``depth=1`` /
    ``parent_invocation_id=None`` on ``None`` rather than guessing a number.
    """
    visited: set[str] = set()
    current = agent_id
    depth = 1
    while current not in depth_1_agents:
        if current in visited:
            # A true cycle, including the degenerate self-parent case where a
            # sidecar's toolUseId is emitted by its own trace.
            logger.debug("Cycle in delegation chain at agent_id=%s", current)
            return None
        visited.add(current)
        parent = parent_agent_of.get(current)
        if parent is None:
            # Chain ends at an agent that is neither depth 1 nor has a
            # resolvable parent -- an orphaned subtree (rotated or truncated
            # parent session).
            return None
        depth += 1
        if depth > MAX_DELEGATION_DEPTH:
            logger.debug(
                "Delegation chain exceeded MAX_DELEGATION_DEPTH at agent_id=%s",
                agent_id,
            )
            return None
        current = parent
    return depth


def resolve_lineage(
    files: list[SubagentFileInfo],
    main_session_tool_use_ids: set[str],
    linked_agent_ids: set[str] | None = None,
) -> dict[str, TraceLineage]:
    """Resolve ``agent_id -> TraceLineage`` for every trace in a session.

    ``main_session_tool_use_ids`` is the set of ``Agent`` ``tool_use`` ids
    emitted by the **main session** -- the depth-1 spawn set. Membership is all
    this needs: an id in it means the main session spawned that agent, which is
    depth 1 by definition. Passed in rather than derived so this module stays
    independent of ``agents.models``.

    ``linked_agent_ids`` is a **co-equal second source** for the same
    decision: an agent owning an ``AgentInvocation`` row was spawned by the
    main session, which is the depth-1 fact itself and survives a missing
    sidecar. Without it, deleting a *parent's* sidecar severs its child's edge
    even though the emitter index still proves it. ``None`` is accepted and
    means "no second source", not "none exist".

    A depth-1 trace gets ``parent_invocation_id = None``. Its parent is the
    main session, which has no invocation id -- and a ``"main"``/``"root"``
    sentinel would be a string that looks like an id and joins to nothing.

    This function computes depth at **any** level. Capping *attachment* at
    depth 2 is the pipeline's decision (#595 AC2 as amended, #659), not this
    module's.

    Degradation is deliberate and uniform: a trace with **no sidecar**, a
    sidecar whose ``toolUseId`` resolves to no emitter, or a chain that hits
    the cycle guard all yield ``TraceLineage(None, 1)``. That biases toward
    under-reporting nesting rather than asserting a parent that may be wrong,
    and leaves the residual for #648 AC3 to disclose as an orphan cohort.
    """
    # Sidecars are read here, NOT in discovery. `discover_session_subagents`
    # is contracted to "only walk directories and name files", and
    # `discover_subagent_files` applies it across an entire project -- on a
    # real corpus that is 1333 traces, so folding per-file JSON reads into
    # discovery would charge every path-only caller for I/O it never asked
    # for. The linker is the one caller that needs the edge, so it pays.
    sidecars = {info.agent_id: read_subagent_sidecar(info.path) for info in files}
    tool_use_id_of: dict[str, str] = {
        agent_id: sidecar.tool_use_id
        for agent_id, sidecar in sidecars.items()
        if sidecar is not None
    }

    # A trace is depth 1 if its sidecar names a parent-session tool_use, OR if
    # it already owns an invocation row -- an invocation exists only for a
    # main-session delegation, so that IS the depth-1 fact, and it survives a
    # missing sidecar. Without the second source, deleting a *parent's* sidecar
    # severs its child's edge even though the emitter index still proves it.
    depth_1_agents = {
        agent_id
        for agent_id, tool_use_id in tool_use_id_of.items()
        if tool_use_id in main_session_tool_use_ids
    }
    depth_1_agents |= linked_agent_ids or set()

    unresolved = {
        agent_id
        for agent_id in tool_use_id_of
        if agent_id not in depth_1_agents
    }
    # Requiring a non-empty depth_1_agents is not an optimization. With no
    # depth-1 root, `_walk_depth` can only terminate via cycle/missing-parent/
    # max-depth -- every lineage degrades to (None, 1) whatever the index says
    # -- so building it would read every trace file line by line (some >1MB)
    # to produce a result that cannot change. Reachable on the #468
    # trace-missing cohort, where a session yields no extractable invocations.
    emitter_index = (
        build_emitter_index(files) if unresolved and depth_1_agents else {}
    )
    parent_agent_of: dict[str, str] = {}
    for agent_id in unresolved:
        emitter = emitter_index.get(tool_use_id_of[agent_id])
        if emitter is not None:
            parent_agent_of[agent_id] = emitter

    lineages: dict[str, TraceLineage] = {}
    for info in files:
        agent_id = info.agent_id
        sidecar = sidecars.get(agent_id)
        agent_type = sidecar.agent_type if sidecar is not None else ""

        if agent_id in depth_1_agents:
            lineages[agent_id] = TraceLineage(
                parent_invocation_id=None, depth=1, agent_type=agent_type,
            )
            continue

        depth = _walk_depth(agent_id, parent_agent_of, depth_1_agents)
        parent_agent = parent_agent_of.get(agent_id)
        if depth is None or parent_agent is None:
            # Unattributable: no sidecar, an emitter we could not resolve, or a
            # cycle. Never a guessed parent.
            lineages[agent_id] = TraceLineage(
                parent_invocation_id=None, depth=1, agent_type=agent_type,
            )
            continue
        lineages[agent_id] = TraceLineage(
            parent_invocation_id=parent_agent, depth=depth, agent_type=agent_type,
        )
    return lineages
