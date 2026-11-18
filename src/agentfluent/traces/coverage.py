"""Collect every subagent trace a session owns, and label each one (#648).

A session ``<uuid>.jsonl`` **owns** every trace file in ``<uuid>/subagents/``.
Ownership is a runtime invariant of the on-disk layout, so it decides which
traces fold into the session's token totals. This module does not price or
sum anything: it links traces to invocations, parses the traces no invocation
claims, and labels every file with exactly one cohort for disclosure. Pricing
stays in ``analytics/``.

Cohorts (``TraceCohort``):

* ``linked`` -- the trace belongs to an ``AgentInvocation`` row (depth 1).
* ``nested`` -- depth >= 2 by ``resolve_lineage``, attached as a child or not.
* ``session_spawned`` -- no invocation row and no depth->=2 lineage: skill-
  and workflow-run background agents (built-in ``/code-review``), and the
  #468-class trace whose emitting ``Agent`` call never linked.
* ``filtered`` -- excluded by ``--agent``. Never counted as a loss.
* ``unparseable`` -- the file could not be parsed into a trace.

**The fold does not depend on the label.** ``fold_traces`` is every parsed
trace except ``filtered`` ones; the labels only describe the result. The fold
also never reads ``SubagentTrace.children`` -- the child tree is a display
shape (#659 may deepen it without moving any total).

**``--agent`` rule.** A ``linked`` trace follows its invocation's type. A
``nested`` trace follows its depth-1 root: it folds only when the root is a
kept invocation, and a root that owns no invocation row counts as
``filtered``. A ``session_spawned`` trace folds only when its sidecar
``agentType`` or ``name`` matches.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from agentfluent.agents.models import AgentInvocation
from agentfluent.traces.discovery import discover_session_subagents
from agentfluent.traces.lineage import TraceLineage, resolve_lineage
from agentfluent.traces.linker import link_traces
from agentfluent.traces.models import SubagentTrace
from agentfluent.traces.parser import parse_subagent_trace

logger = logging.getLogger(__name__)

TraceCohort = Literal["linked", "nested", "session_spawned", "filtered", "unparseable"]


@dataclass
class OwnedTrace:
    """One trace file in the session's ``subagents/`` dir and its label."""

    agent_id: str
    cohort: TraceCohort
    trace: SubagentTrace | None
    """``None`` only for ``unparseable``. For ``linked`` this is the trace the
    first matching invocation carries."""

    spawned_as: str = ""
    """What spawned this agent, for the session-spawned per-type breakdown:
    the sidecar ``name`` when present (``"code-review"``), else its
    ``agentType``. Empty when neither is known."""


@dataclass
class SessionTraces:
    """Every trace a session owns, linked, labeled and ordered."""

    owned: list[OwnedTrace] = field(default_factory=list)
    """One entry per trace file, in discovery (filename) order."""

    dedup_depth_one: list[SubagentTrace] = field(default_factory=list)
    """Linked traces in invocation order, **unfiltered**: the dedup's second
    precedence tier (after the main session)."""

    dedup_rest: list[SubagentTrace] = field(default_factory=list)
    """Every other parsed trace, **unfiltered**. Dedup sorts it by
    ``(depth, agent_id)``."""

    fold_traces: list[SubagentTrace] = field(default_factory=list)
    """What the session token fold sums: kept invocations' traces (one entry
    per invocation, as before #648), then every other parsed, non-filtered
    trace."""


def _matches(value: str | None, agent_filter: str) -> bool:
    return value is not None and value.lower() == agent_filter.lower()


def _depth_one_root(agent_id: str, lineages: dict[str, TraceLineage]) -> str | None:
    """The depth-1 ancestor of ``agent_id``, or ``None`` if the walk breaks.

    ``resolve_lineage`` only gives a trace depth >= 2 once its whole chain
    reaches a depth-1 agent, so the walk terminates; the step bound guards
    against an inconsistent map rather than a state the resolver produces.
    """
    current = agent_id
    for _ in range(len(lineages) + 1):
        lineage = lineages.get(current)
        if lineage is None:
            return None
        if lineage.depth == 1:
            return current
        if lineage.parent_invocation_id is None:
            return None
        current = lineage.parent_invocation_id
    return None


def collect_session_traces(
    invocations: list[AgentInvocation],
    session_path: Path,
    *,
    agent_filter: str | None = None,
) -> SessionTraces:
    """Link, parse and label every trace ``session_path`` owns.

    ``invocations`` must be the session's **unfiltered** invocation list:
    linking and lineage run over all of them, so which traces are depth 1 --
    and so the dedup precedence -- does not change with ``--agent`` (#648, the
    #701 review's filter-invariance finding). ``agent_filter`` only decides
    the ``filtered`` label and fold membership.

    Mutates ``invocations`` in place (``link_traces``) and attaches depth-2
    traces to their linked parent as ``children``, exactly as before #648.
    Each trace file is parsed at most once per role: invocation-linked traces
    through ``link_traces`` (one parse per invocation, so two invocations
    naming one ``agent_id`` keep two objects -- #705), every other file once.
    """
    session_dir = session_path.parent / session_path.stem
    files = discover_session_subagents(session_dir)
    path_map = {info.agent_id: info.path for info in files}

    def parse(agent_id: str) -> SubagentTrace | None:
        file_path = path_map.get(agent_id)
        if file_path is None:
            return None
        try:
            return parse_subagent_trace(file_path)
        except (FileNotFoundError, ValueError):
            logger.debug("Skipping malformed or missing subagent trace: %s", file_path)
            return None

    link_traces(invocations, parse)

    linked_by_agent_id: dict[str, SubagentTrace] = {}
    for inv in invocations:
        if inv.agent_id is not None and inv.trace is not None:
            linked_by_agent_id.setdefault(inv.agent_id, inv.trace)

    # Linked agent ids are a second depth-1 source, so a depth-1 agent whose
    # sidecar is missing does not sever its child's edge. ASSUMPTION, not an
    # enforced invariant: an invocation row implies a main-session delegation.
    # Nothing filters `isSidechain`, so a session JSONL that inlined sidechain
    # lines would mint rows for nested agents. Holds for every fixture and for
    # the current corpus.
    lineages = resolve_lineage(
        files,
        {inv.tool_use_id for inv in invocations},
        set(linked_by_agent_id),
    )
    for agent_id, trace in linked_by_agent_id.items():
        lineage = lineages.get(agent_id)
        if lineage is not None:
            trace.parent_invocation_id = lineage.parent_invocation_id
            trace.depth = lineage.depth

    kept_agent_ids = {
        inv.agent_id
        for inv in invocations
        if inv.agent_id is not None
        and inv.trace is not None
        and (agent_filter is None or _matches(inv.agent_type, agent_filter))
    }

    result = SessionTraces()
    for info in files:
        agent_id = info.agent_id
        lineage = lineages.get(agent_id, TraceLineage(None, 1))
        if agent_id in linked_by_agent_id:
            cohort: TraceCohort = (
                "linked" if agent_id in kept_agent_ids else "filtered"
            )
            result.owned.append(OwnedTrace(agent_id, cohort, linked_by_agent_id[agent_id]))
            continue

        parsed = parse(agent_id)
        if parsed is None:
            result.owned.append(OwnedTrace(agent_id, "unparseable", None))
            continue
        _label_unlinked_trace(parsed, lineage, linked_by_agent_id)
        result.dedup_rest.append(parsed)

        if lineage.depth >= 2:
            root = _depth_one_root(agent_id, lineages)
            keep = agent_filter is None or root in kept_agent_ids
            cohort = "nested" if keep else "filtered"
            spawned_as = ""
        else:
            spawned_as = lineage.name or lineage.agent_type
            keep = agent_filter is None or (
                _matches(lineage.agent_type, agent_filter)
                or _matches(lineage.name, agent_filter)
            )
            cohort = "session_spawned" if keep else "filtered"
        result.owned.append(OwnedTrace(agent_id, cohort, parsed, spawned_as))

    result.dedup_depth_one = [inv.trace for inv in invocations if inv.trace is not None]
    kept_invocation_traces = [
        inv.trace
        for inv in invocations
        if inv.trace is not None and inv.agent_id in kept_agent_ids
    ]
    result.fold_traces = kept_invocation_traces + [
        owned.trace
        for owned in result.owned
        if owned.cohort in ("nested", "session_spawned") and owned.trace is not None
    ]
    return result


def _label_unlinked_trace(
    trace: SubagentTrace,
    lineage: TraceLineage,
    linked_by_agent_id: dict[str, SubagentTrace],
) -> None:
    """Give a trace no invocation claims its depth, type and (at depth 2) parent.

    The parser defaults ``agent_type`` to ``unknown`` and there is no
    invocation row to overwrite it from, so the sidecar is the only source.
    ``depth`` is set at every depth so the dedup's ``(depth, agent_id)`` order
    is real. Only a depth-2 trace whose parent owns an invocation row is
    attached as a child and given ``parent_invocation_id``: that field's
    identity domain is invocation rows (``SubagentTrace.parent_invocation_id``),
    and attachment stays capped at depth 2 (#659), so a deeper trace folds but
    is never attached -- no object with ``depth >= 3`` reaches the JSON output.
    """
    trace.depth = lineage.depth
    if lineage.agent_type:
        trace.agent_type = lineage.agent_type
    if lineage.depth != 2 or lineage.parent_invocation_id is None:
        return
    parent_trace = linked_by_agent_id.get(lineage.parent_invocation_id)
    if parent_trace is None:
        return
    trace.parent_invocation_id = lineage.parent_invocation_id
    parent_trace.children.append(trace)

