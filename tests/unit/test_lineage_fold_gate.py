"""Lineage resolution and the non-folding gate (#595 PR B).

Two things are locked here:

* **Lineage** -- ``depth`` and ``parent_invocation_id`` resolve from *data*
  (the cross-file ``toolUseId`` join), never from path shape, and degrade to
  ``depth=1``/``parent_invocation_id=None`` rather than guessing a parent.
* **The non-folding gate** (``analytics/pipeline.py``) -- depth->=2 usage must
  NOT reach ``fold_subagent_metrics_in``. Folding it in is #648 AC2's
  deliberate act; PR B pins the gate closed so it cannot happen as an
  implementation side effect.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

import agentfluent.analytics.pipeline as pipeline_mod
import agentfluent.traces.lineage as lineage_mod
from agentfluent.analytics.pipeline import analyze_session
from agentfluent.traces.discovery import discover_session_subagents
from agentfluent.traces.lineage import (
    MAX_DELEGATION_DEPTH,
    TraceLineage,
    _walk_depth,
    build_emitter_index,
    emitted_tool_use_ids,
    resolve_lineage,
)

_FIXTURES = Path(__file__).parent.parent / "fixtures"
_NESTED = _FIXTURES / "nested_session"
_NESTED_MAIN = _NESTED / "nested-session-1.jsonl"
_NESTED_DIR = _NESTED / "nested-session-1"
_SDK_MAIN = _FIXTURES / "sdk_session" / "sdk-main-1.jsonl"

# The main session's only Agent tool_use, and the invocation it belongs to.
_MAIN_TOOL_USE = "toolu_main_to_worker"


def _nested_lineages() -> dict[str, TraceLineage]:
    files = discover_session_subagents(_NESTED_DIR)
    return resolve_lineage(files, {_MAIN_TOOL_USE})


class TestLineageOnTheNestedFixture:
    """The committed multi-level fixture: worker001 -> leaf0001."""

    def test_depth_one_agent_has_depth_1_and_no_parent(self) -> None:
        lineages = _nested_lineages()
        assert lineages["worker001"].depth == 1
        # None, not a "main"/"root" sentinel: a sentinel would look like an id
        # and join to nothing, forcing every consumer to special-case it.
        assert lineages["worker001"].parent_invocation_id is None

    def test_depth_two_agent_resolves_parent_and_depth(self) -> None:
        lineages = _nested_lineages()
        assert lineages["leaf0001"].depth == 2
        assert lineages["leaf0001"].parent_invocation_id == "worker001"

    def test_depth_is_not_readable_from_path_shape(self) -> None:
        """Both traces are flat siblings, so depth came from data alone."""
        paths = sorted(p.name for p in (_NESTED_DIR / "subagents").glob("*.jsonl"))
        assert paths == ["agent-leaf0001.jsonl", "agent-worker001.jsonl"]
        # Same directory, same nesting on disk -- different resolved depth.
        lineages = _nested_lineages()
        assert lineages["worker001"].depth != lineages["leaf0001"].depth

    def test_emitter_index_maps_tool_use_to_emitting_agent(self) -> None:
        index = build_emitter_index(discover_session_subagents(_NESTED_DIR))
        # The worker emitted the tool_use that spawned the leaf...
        assert index["toolu_worker_to_leaf"] == "worker001"
        # ...and the main session's tool_use is absent by construction, which
        # is what makes membership here the depth->=2 test.
        assert _MAIN_TOOL_USE not in index


class TestEndToEndThroughAnalyze:
    """The fields reach the public surface, and children hang off the parent."""

    def test_parent_trace_carries_the_child(self) -> None:
        analysis = analyze_session(_NESTED_MAIN)
        traces = [inv.trace for inv in analysis.invocations if inv.trace is not None]
        assert len(traces) == 1
        parent = traces[0]
        assert parent.depth == 1
        assert parent.parent_invocation_id is None
        assert len(parent.children) == 1
        assert parent.children[0].depth == 2
        assert parent.children[0].parent_invocation_id == "worker001"

    def test_depth_two_agent_does_not_become_an_invocation_row(self) -> None:
        """Ruling 2: synthesizing rows would move every agent aggregate."""
        analysis = analyze_session(_NESTED_MAIN)
        assert len(analysis.invocations) == 1
        assert analysis.agent_metrics.total_invocations == 1

    def test_new_fields_serialize_as_public_json(self) -> None:
        analysis = analyze_session(_NESTED_MAIN)
        payload = json.loads(analysis.invocations[0].trace.model_dump_json())
        assert payload["depth"] == 1
        assert payload["parent_invocation_id"] is None
        assert payload["children"][0]["depth"] == 2


class TestNonFoldingGate:
    """Depth->=2 usage must not enter token metrics. Blocking concern #2.

    Named by the gate comment in ``analytics/pipeline.py``; that comment
    asserts this class exists, so it must.
    """

    def test_depth_two_traces_never_reach_the_folding_function(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Assert the MECHANISM: what the gate hands to the folder.

        Asserting a total instead would pass for every implementation that
        happens to reach that total -- including the broken one this guards
        against. Only observing *which traces are passed* distinguishes them.
        """
        captured: list[list[object]] = []
        real = pipeline_mod.compute_subagent_token_metrics

        def spy(traces, **kwargs):  # type: ignore[no-untyped-def]
            captured.append(list(traces))
            return real(traces, **kwargs)

        monkeypatch.setattr(pipeline_mod, "compute_subagent_token_metrics", spy)
        analysis = analyze_session(_NESTED_MAIN)

        assert captured, "the folding path never ran -- test proves nothing"
        folded = captured[0]
        # The depth-2 child exists and is non-empty, so its exclusion here is
        # a real exclusion rather than an empty-set vacuity.
        child = analysis.invocations[0].trace.children[0]
        assert child.depth == 2
        assert child.usage.input_tokens + child.usage.output_tokens > 0
        assert child.agent_id not in {t.agent_id for t in folded}
        assert all(t.depth == 1 for t in folded)

    def test_the_child_carries_spend_the_gate_is_withholding(self) -> None:
        """The gate is load-bearing, not decorative.

        If the depth-2 child ever carried zero usage, the test above would go
        vacuous -- excluding an empty trace proves nothing. This pins the
        precondition that makes that exclusion meaningful.
        """
        analysis = analyze_session(_NESTED_MAIN)
        child = analysis.invocations[0].trace.children[0]
        assert child.usage.input_tokens + child.usage.output_tokens > 0


class TestDegradationPaths:
    """Absence must never produce a *wrong* parent. Architect concern #2."""

    def test_missing_sidecar_degrades_to_depth_1_no_parent(
        self, tmp_path: Path,
    ) -> None:
        """A trace whose sidecar is absent (older sessions predate it).

        Measured prevalence in the current corpus is 0/1333 -- this is a
        *degradation* path, not a detection path, so 0 today is evidence about
        one machine's session history, never that the path is unreachable.
        """
        session_dir = tmp_path / "nested-session-1"
        shutil.copytree(_NESTED_DIR, session_dir)
        (session_dir / "subagents" / "agent-leaf0001.meta.json").unlink()

        lineages = resolve_lineage(
            discover_session_subagents(session_dir), {_MAIN_TOOL_USE},
        )
        # Unattributable -- NOT a guessed parent.
        assert lineages["leaf0001"].depth == 1
        assert lineages["leaf0001"].parent_invocation_id is None
        # The depth-1 sibling is unaffected.
        assert lineages["worker001"].depth == 1

    def test_orphan_sidecar_tool_use_id_emitted_by_no_file(
        self, tmp_path: Path,
    ) -> None:
        """A truncated/rotated parent: the toolUseId resolves to nothing."""
        session_dir = tmp_path / "nested-session-1"
        shutil.copytree(_NESTED_DIR, session_dir)
        sidecar = session_dir / "subagents" / "agent-leaf0001.meta.json"
        meta = json.loads(sidecar.read_text())
        meta["toolUseId"] = "toolu_emitted_by_nobody"
        sidecar.write_text(json.dumps(meta))

        lineages = resolve_lineage(
            discover_session_subagents(session_dir), {_MAIN_TOOL_USE},
        )
        assert lineages["leaf0001"].parent_invocation_id is None
        assert lineages["leaf0001"].depth == 1

    def test_unreadable_trace_yields_no_emitters_rather_than_raising(
        self, tmp_path: Path,
    ) -> None:
        broken = tmp_path / "agent-broken.jsonl"
        broken.write_text('{"type":"assistant" NOT JSON\n')
        assert emitted_tool_use_ids(broken) == set()

    def test_absent_trace_file_yields_no_emitters(self, tmp_path: Path) -> None:
        assert emitted_tool_use_ids(tmp_path / "does-not-exist.jsonl") == set()


class TestCycleGuard:
    """Synthetic graphs only -- a cycle is unrepresentable in this format.

    Ruling 5: hand-crafting a cyclic JSONL fixture would encode a false claim
    about the format, which is exactly what the findings doc exists to
    prevent. So the guard is tested against in-memory graphs.
    """

    def test_self_parent_is_caught(self) -> None:
        """The degenerate cycle a real corruption could plausibly produce."""
        assert _walk_depth("a", {"a": "a"}, set()) is None

    def test_two_node_cycle_is_caught(self) -> None:
        assert _walk_depth("a", {"a": "b", "b": "a"}, set()) is None

    def test_chain_with_no_depth_1_root_is_unresolvable(self) -> None:
        assert _walk_depth("a", {"a": "b"}, set()) is None

    def test_long_chain_exceeding_max_depth_is_caught(self) -> None:
        """Isolates MAX_DELEGATION_DEPTH, and only it.

        The chain is ROOTED -- its last link is a depth-1 agent -- so the
        missing-parent branch cannot fire and the visited-set cannot fire.
        An unrooted chain would return None either way and prove nothing about
        the guard.
        """
        depth = MAX_DELEGATION_DEPTH + 5
        chain = {f"a{i}": f"a{i + 1}" for i in range(depth)}
        root = f"a{depth}"
        assert _walk_depth("a0", chain, {root}) is None

    def test_chain_just_inside_max_depth_still_resolves(self) -> None:
        """The other side of the boundary -- the guard must not over-trigger."""
        chain = {f"b{i}": f"b{i + 1}" for i in range(MAX_DELEGATION_DEPTH - 2)}
        root = f"b{MAX_DELEGATION_DEPTH - 2}"
        assert _walk_depth("b0", chain, {root}) == MAX_DELEGATION_DEPTH - 1

    def test_depth_three_chain_resolves_to_3(self) -> None:
        """Guards the attachment ordering: depth 3 must not silently drop.

        The live corpus bottoms out at depth 2 (1317/16/0), so nothing in the
        fixtures or the corpus would catch a regression here. Nothing in the
        format caps depth, so this is tested rather than assumed.
        """
        assert _walk_depth("c", {"c": "b", "b": "a"}, {"a"}) == 3

    def test_legitimate_deep_chain_still_resolves(self) -> None:
        """The guard must not reject depth that is merely deep."""
        chain = {"d4": "d3", "d3": "d2", "d2": "d1"}
        assert _walk_depth("d4", chain, {"d1"}) == 4


class TestLevelOneRegression:
    """The depth-1 rollup path is untouched. Fixture-locked per the AC."""

    def test_sdk_session_level_1_trace_is_depth_1(self) -> None:
        analysis = analyze_session(_SDK_MAIN)
        traces = [inv.trace for inv in analysis.invocations if inv.trace is not None]
        assert traces
        for trace in traces:
            assert trace.depth == 1
            assert trace.parent_invocation_id is None
            assert trace.children == []


class TestReviewFindings:
    """Regressions for the round-1 code-review findings (PR #658)."""

    def test_null_message_line_does_not_raise(self, tmp_path: Path) -> None:
        """Finding 1: totality is a contract, and it was not being kept.

        The line must contain the literal `"tool_use"` to reach the parse at
        all -- that is what made this reachable rather than theoretical.
        """
        trace = tmp_path / "agent-x.jsonl"
        trace.write_text(
            '{"type":"assistant","message":null,"note":"tool_use"}\n'
            '{"type":"assistant","message":{"content":'
            '[{"type":"tool_use","id":"toolu_ok"}]}}\n'
        )
        # Does not raise, and still recovers the good line.
        assert emitted_tool_use_ids(trace) == {"toolu_ok"}

    def test_non_dict_message_shapes_are_tolerated(self, tmp_path: Path) -> None:
        trace = tmp_path / "agent-y.jsonl"
        trace.write_text(
            '{"type":"assistant","message":"tool_use"}\n'
            '{"type":"assistant","message":[1,2],"k":"tool_use"}\n'
        )
        assert emitted_tool_use_ids(trace) == set()

    def test_depth_two_child_gets_agent_type_from_its_sidecar(self) -> None:
        """Finding 2: a depth->=2 agent has no invocation row to inherit from.

        Ruling 1 called the sidecar its only source of `agent_type`. Left
        unset, every child this PR exposes would serialize as `unknown`.
        """
        analysis = analyze_session(_NESTED_MAIN)
        child = analysis.invocations[0].trace.children[0]
        sidecar = json.loads(
            (_NESTED_DIR / "subagents" / "agent-leaf0001.meta.json").read_text()
        )
        assert child.agent_type == sidecar["agentType"]
        assert child.agent_type != "unknown"

    def test_missing_parent_sidecar_preserves_the_childs_edge(
        self, tmp_path: Path,
    ) -> None:
        """Finding 3: the depth-1 fact survives a missing sidecar.

        Distinct from the leaf-sidecar case above, which cannot tell these two
        implementations apart. Here the data still proves the edge via the
        emitter index, so severing it would be a loss, not a degradation.
        """
        session_dir = tmp_path / "nested-session-1"
        shutil.copytree(_NESTED_DIR, session_dir)
        (session_dir / "subagents" / "agent-worker001.meta.json").unlink()

        lineages = resolve_lineage(
            discover_session_subagents(session_dir),
            {_MAIN_TOOL_USE},
            linked_agent_ids={"worker001"},
        )
        assert lineages["worker001"].depth == 1
        assert lineages["leaf0001"].depth == 2
        assert lineages["leaf0001"].parent_invocation_id == "worker001"

    def test_no_depth_1_root_skips_the_emitter_scan(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Finding 4: don't read every trace to produce a result that can't change."""
        session_dir = tmp_path / "nested-session-1"
        shutil.copytree(_NESTED_DIR, session_dir)

        calls: list[Path] = []
        real = lineage_mod.emitted_tool_use_ids
        monkeypatch.setattr(
            lineage_mod,
            "emitted_tool_use_ids",
            lambda p: (calls.append(p), real(p))[1],
        )
        # No parent-session tool_use ids and no invocation rows => no root.
        lineages = resolve_lineage(discover_session_subagents(session_dir), set())

        assert calls == [], "scanned trace files despite no depth-1 root"
        assert all(lin.depth == 1 for lin in lineages.values())
        assert all(lin.parent_invocation_id is None for lin in lineages.values())

    def test_linked_trace_is_never_attached_twice(self) -> None:
        """Finding 5: an agent owning an invocation row is not also a child."""
        analysis = analyze_session(_NESTED_MAIN)
        linked = {
            inv.agent_id for inv in analysis.invocations if inv.trace is not None
        }
        seen: list[str] = []

        def walk(trace: object) -> None:
            for child in trace.children:
                seen.append(child.agent_id)
                walk(child)

        for inv in analysis.invocations:
            if inv.trace is not None:
                walk(inv.trace)

        assert not (set(seen) & linked), "a linked trace was also attached as a child"
        assert len(seen) == len(set(seen)), "a trace was attached more than once"
