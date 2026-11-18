"""Cross-file API-turn deduplication for subagent spend (#701).

Fork agents replay the spawning conversation, so sibling fork traces -- and the
main session -- can carry the same ``message.id``. Summing per-file usage
counted those turns more than once in both the session fold and per-agent
spend. These tests pin the guard: first occurrence wins, main session first,
and the fold and per-agent pricing read the same deduplicated usage.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from rich.console import Console

import agentfluent.analytics.pipeline as pipeline_mod
from agentfluent.analytics.pipeline import analyze_session
from agentfluent.analytics.tokens import (
    counted_parent_message_ids,
    dedupe_trace_turns,
    price_trace,
    session_price_timestamp,
    trace_spend_usage,
)
from agentfluent.cli.formatters.table import format_analysis_table
from agentfluent.core.parser import parse_session
from agentfluent.core.session import Usage
from agentfluent.traces.discovery import discover_session_subagents
from agentfluent.traces.lineage import resolve_lineage
from agentfluent.traces.models import SubagentTrace, TraceTurnUsage
from agentfluent.traces.parser import parse_subagent_trace

_MODEL = "claude-haiku-4-5-20251001"
_NESTED_MAIN = (
    Path(__file__).parent.parent / "fixtures" / "nested_session" / "nested-session-1.jsonl"
)


def _usage(inp: int, out: int, cc: int = 0, cr: int = 0) -> dict[str, int]:
    return {
        "input_tokens": inp,
        "output_tokens": out,
        "cache_creation_input_tokens": cc,
        "cache_read_input_tokens": cr,
    }


def _assistant(
    msg_id: str | None, usage: dict[str, int], content: list[dict[str, Any]], ts: str,
    *, sidechain: bool = False,
) -> dict[str, Any]:
    message: dict[str, Any] = {
        "model": _MODEL, "role": "assistant", "content": content, "usage": usage,
    }
    if msg_id is not None:
        message["id"] = msg_id
    line: dict[str, Any] = {"type": "assistant", "message": message, "timestamp": ts}
    if sidechain:
        line["isSidechain"] = True
    return line


def _text(text: str) -> list[dict[str, Any]]:
    return [{"type": "text", "text": text}]


def _user(text: str, ts: str, *, sidechain: bool = False) -> dict[str, Any]:
    line: dict[str, Any] = {
        "type": "user", "message": {"role": "user", "content": text}, "timestamp": ts,
    }
    if sidechain:
        line["isSidechain"] = True
    return line


def _agent_result(tool_use_id: str, agent_id: str, ts: str) -> dict[str, Any]:
    return {
        "type": "user",
        "message": {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": tool_use_id, "content": "ok"}],
        },
        "toolUseResult": {
            "status": "completed", "agentId": agent_id, "agentType": "fork",
            "totalTokens": 1, "totalToolUseCount": 0, "totalDurationMs": 1,
        },
        "timestamp": ts,
    }


def _write(path: Path, lines: list[dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(line) + "\n" for line in lines))


# Per-turn usage, chosen so every distinct turn has a distinct total and a
# double count of any one of them shows up in the sums below.
_MAIN_1 = _usage(10, 20)
_MAIN_2 = _usage(11, 21)
_SHARED = _usage(100, 200, cc=300, cr=400)  # replayed in both forks
_Z_OWN = _usage(5, 7)
_A2_OWN = _usage(13, 17)


def _build_fork_session(root: Path) -> Path:
    """Main session spawning two fork agents that replay shared context.

    * ``forkZ`` replays ``msg_shared`` and also the main session's ``msg_main1``,
      then does its own turn ``msg_z_own`` -- written as two block-per-line
      fragments sharing one ``message.id`` (#696), which must still count once.
    * ``forkA`` replays ``msg_shared`` and does its own turn ``msg_a2_own``.
    """
    main = root / "fork-session.jsonl"
    _write(main, [
        _user("go", "2026-11-01T00:00:00Z"),
        _assistant("msg_main1", _MAIN_1, [
            {"type": "tool_use", "id": "toolu_a", "name": "Agent",
             "input": {"subagent_type": "fork", "description": "a", "prompt": "a"}},
        ], "2026-11-01T00:00:01Z"),
        _agent_result("toolu_a", "forkZ", "2026-11-01T00:00:05Z"),
        _assistant("msg_main2", _MAIN_2, [
            {"type": "tool_use", "id": "toolu_b", "name": "Agent",
             "input": {"subagent_type": "fork", "description": "b", "prompt": "b"}},
        ], "2026-11-01T00:00:06Z"),
        _agent_result("toolu_b", "forkA", "2026-11-01T00:00:09Z"),
    ])
    subagents = root / "fork-session" / "subagents"
    subagents.mkdir(parents=True)
    _write(subagents / "agent-forkZ.jsonl", [
        _user("a", "2026-11-01T00:00:02Z", sidechain=True),
        _assistant("msg_shared", _SHARED, _text("ctx"), "2026-11-01T00:00:02Z", sidechain=True),
        _assistant("msg_main1", _MAIN_1, _text("replay"), "2026-11-01T00:00:03Z", sidechain=True),
        _assistant("msg_z_own", _Z_OWN, [{"type": "thinking", "thinking": "t"}],
                   "2026-11-01T00:00:04Z", sidechain=True),
        _assistant("msg_z_own", _Z_OWN, _text("own"), "2026-11-01T00:00:04Z", sidechain=True),
    ])
    _write(subagents / "agent-forkA.jsonl", [
        _user("b", "2026-11-01T00:00:07Z", sidechain=True),
        _assistant("msg_shared", _SHARED, _text("ctx"), "2026-11-01T00:00:07Z", sidechain=True),
        _assistant("msg_a2_own", _A2_OWN, _text("own"), "2026-11-01T00:00:08Z", sidechain=True),
    ])
    for agent_id, tool_use_id in (("forkZ", "toolu_a"), ("forkA", "toolu_b")):
        (subagents / f"agent-{agent_id}.meta.json").write_text(json.dumps({
            "agentType": "fork", "description": agent_id, "toolUseId": tool_use_id,
            "isFork": True, "spawnDepth": 1,
        }))
    return main


def _total(u: dict[str, int]) -> int:
    return sum(u.values())


# What the subagent side should spend: the shared turn once, each fork's own
# turn once, and nothing of main's replayed turn (main counted it first).
_EXPECTED_SUBAGENT_TOKENS = _total(_SHARED) + _total(_Z_OWN) + _total(_A2_OWN)
_NAIVE_SUBAGENT_TOKENS = (
    2 * _total(_SHARED) + _total(_MAIN_1) + _total(_Z_OWN) + _total(_A2_OWN)
)


def _subagent_row_tokens(analysis: pipeline_mod.SessionAnalysis) -> int:
    return sum(
        r.input_tokens + r.output_tokens
        + r.cache_creation_input_tokens + r.cache_read_input_tokens
        for r in analysis.token_metrics.by_model
        if r.origin == "subagent"
    )


class TestForkSessionFold:
    def test_fixture_really_double_counts_without_the_guard(self, tmp_path: Path) -> None:
        """The fixture is only a test of the guard if per-file sums overlap."""
        main = _build_fork_session(tmp_path)
        files = {f.agent_id: f.path for f in discover_session_subagents(main.with_suffix(""))}
        per_file = sum(
            parse_subagent_trace(path).usage.total_tokens for path in files.values()
        )
        assert per_file == _NAIVE_SUBAGENT_TOKENS
        assert per_file > _EXPECTED_SUBAGENT_TOKENS

    def test_session_fold_counts_each_api_turn_once(self, tmp_path: Path) -> None:
        analysis = analyze_session(_build_fork_session(tmp_path))
        assert _subagent_row_tokens(analysis) == _EXPECTED_SUBAGENT_TOKENS

    def test_top_line_is_main_plus_unique_subagent_turns(self, tmp_path: Path) -> None:
        analysis = analyze_session(_build_fork_session(tmp_path))
        tm = analysis.token_metrics
        top = (
            tm.input_tokens + tm.output_tokens
            + tm.cache_creation_input_tokens + tm.cache_read_input_tokens
        )
        assert top == _total(_MAIN_1) + _total(_MAIN_2) + _EXPECTED_SUBAGENT_TOKENS

    def test_guard_removed_double_counts(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Mutation in-suite: with the dedup pass disabled the fold over-counts,
        so the assertions above are what hold the guard in place."""
        monkeypatch.setattr(pipeline_mod, "dedupe_trace_turns", lambda *_a, **_k: None)
        analysis = analyze_session(_build_fork_session(tmp_path))
        assert _subagent_row_tokens(analysis) == _NAIVE_SUBAGENT_TOKENS

    def test_per_agent_spend_reconciles_with_subagent_rows(self, tmp_path: Path) -> None:
        """#646's reconciliation survives the dedup: per-agent spend and the
        session's subagent rows read the same deduplicated usage."""
        analysis = analyze_session(_build_fork_session(tmp_path))
        per_agent_tokens = sum(
            m.spend_tokens for m in analysis.agent_metrics.by_agent_type.values()
        )
        per_agent_cost = sum(
            m.estimated_total_cost_usd for m in analysis.agent_metrics.by_agent_type.values()
        )
        row_cost = sum(
            r.cost for r in analysis.token_metrics.by_model if r.origin == "subagent"
        )
        assert per_agent_tokens == _EXPECTED_SUBAGENT_TOKENS
        assert per_agent_cost == pytest.approx(row_cost)
        assert row_cost > 0

    def test_first_occurrence_wins_in_invocation_order(self, tmp_path: Path) -> None:
        """forkZ (first invocation) keeps the shared turn; forkA loses it; forkZ
        loses the turn main already counted. forkZ sorts *after* forkA by
        ``agent_id``, so an id-ordered (rather than invocation-ordered) pass
        would hand the shared turn to forkA and fail this."""
        analysis = analyze_session(_build_fork_session(tmp_path))
        traces = {inv.agent_id: inv.trace for inv in analysis.invocations}
        fork_z, fork_a = traces["forkZ"], traces["forkA"]
        assert fork_z is not None and fork_a is not None
        assert trace_spend_usage(fork_z).total_tokens == _total(_SHARED) + _total(_Z_OWN)
        assert trace_spend_usage(fork_a).total_tokens == _total(_A2_OWN)

    def test_public_usage_and_json_are_unchanged(self, tmp_path: Path) -> None:
        """``trace.usage`` stays the per-file sum, and neither new field leaks
        into JSON output (D055)."""
        analysis = analyze_session(_build_fork_session(tmp_path))
        fork_a = next(inv.trace for inv in analysis.invocations if inv.agent_id == "forkA")
        assert fork_a is not None
        assert fork_a.usage.total_tokens == _total(_SHARED) + _total(_A2_OWN)
        dumped = json.loads(fork_a.model_dump_json())
        assert "turn_usages" not in dumped
        assert "spend_usage" not in dumped


class TestParserTurnUsages:
    def test_fragments_merge_to_one_turn_and_sum_matches_usage(self, tmp_path: Path) -> None:
        main = _build_fork_session(tmp_path)
        trace = parse_subagent_trace(main.with_suffix("") / "subagents" / "agent-forkZ.jsonl")
        ids = [t.message_id for t in trace.turn_usages]
        assert ids == ["msg_shared", "msg_main1", "msg_z_own"]
        assert sum((t.usage for t in trace.turn_usages), Usage()) == trace.usage

    def test_turns_without_message_id_always_count(self) -> None:
        """The nested fixture carries no ``message.id`` -- nothing can match, so
        spend equals the per-file usage for every folded trace."""
        analysis = analyze_session(_NESTED_MAIN)
        for inv in analysis.invocations:
            assert inv.trace is not None
            assert inv.trace.turn_usages
            assert all(t.message_id is None for t in inv.trace.turn_usages)
            assert trace_spend_usage(inv.trace) == inv.trace.usage


def _trace(agent_id: str, turns: list[tuple[str | None, int]]) -> SubagentTrace:
    turn_usages = [TraceTurnUsage(message_id=mid, usage=Usage(output_tokens=n)) for mid, n in turns]
    return SubagentTrace(
        agent_id=agent_id,
        delegation_prompt="",
        turn_usages=turn_usages,
        usage=sum((t.usage for t in turn_usages), Usage()),
        model=_MODEL,
    )


class TestDedupeTraceTurns:
    def test_counted_ids_and_earlier_traces_take_precedence(self) -> None:
        first = _trace("a", [("m1", 1), ("s", 10), ("a1", 100)])
        second = _trace("b", [("s", 10), ("b1", 1000)])
        dedupe_trace_turns({"m1"}, [first, second])
        assert trace_spend_usage(first).output_tokens == 110
        assert trace_spend_usage(second).output_tokens == 1000

    def test_depth_one_order_is_the_callers(self) -> None:
        first = _trace("a", [("s", 10)])
        second = _trace("b", [("s", 10), ("b1", 1)])
        dedupe_trace_turns(set(), [second, first])
        assert trace_spend_usage(second).output_tokens == 11
        assert trace_spend_usage(first).output_tokens == 0

    def test_none_ids_always_count(self) -> None:
        a = _trace("a", [(None, 3)])
        b = _trace("b", [(None, 3)])
        dedupe_trace_turns(set(), [a, b])
        assert trace_spend_usage(a).output_tokens == 3
        assert trace_spend_usage(b).output_tokens == 3

    def test_same_trace_twice_is_processed_once(self) -> None:
        a = _trace("a", [("x", 4)])
        dedupe_trace_turns(set(), [a, a])
        assert trace_spend_usage(a).output_tokens == 4

    def test_trace_without_turns_spends_its_usage(self) -> None:
        bare = SubagentTrace(
            agent_id="bare", delegation_prompt="", usage=Usage(output_tokens=9), model=_MODEL,
        )
        dedupe_trace_turns({"anything"}, [bare])
        assert bare.spend_usage is None
        assert trace_spend_usage(bare).output_tokens == 9

    def test_public_usage_is_not_mutated(self) -> None:
        a = _trace("a", [("m", 5), ("own", 1)])
        dedupe_trace_turns({"m"}, [a])
        assert a.usage.output_tokens == 6
        assert trace_spend_usage(a).output_tokens == 1


class TestSidecarWithoutToolUseIdInLineage:
    def test_agent_type_survives_and_trace_gets_no_edge(self, tmp_path: Path) -> None:
        """#701 AC1: a skill-spawned sidecar (no ``toolUseId``) used to be
        rejected whole, so lineage reported its type as ``""``."""
        subagents = tmp_path / "s" / "subagents"
        subagents.mkdir(parents=True)
        (subagents / "agent-cr1.jsonl").write_text("{}\n")
        (subagents / "agent-cr1.meta.json").write_text(json.dumps({
            "agentType": "general-purpose", "name": "code-review",
            "spawnDepth": 1, "requestShape": "background",
        }))
        lineages = resolve_lineage(discover_session_subagents(tmp_path / "s"), set())
        assert lineages["cr1"].agent_type == "general-purpose"
        assert lineages["cr1"].depth == 1
        assert lineages["cr1"].parent_invocation_id is None


def _price_of(usage_dicts: list[dict[str, int]], main: Path) -> float:
    """Price a token total independently of the dedup path: a bare trace (no
    ``turn_usages``) carrying the expected usage, at the session's price date."""
    usage = sum((Usage(**u) for u in usage_dicts), Usage())
    reference = SubagentTrace(
        agent_id="reference", delegation_prompt="", usage=usage, model=_MODEL,
    )
    cost = price_trace(reference, timestamp=session_price_timestamp(parse_session(main)))
    assert cost is not None and cost > 0
    return cost


class TestForkSessionCost:
    """Cost, not just tokens, is deduplicated (round-1 H1): the reconciliation
    test compares Σ price_trace to Σ price_trace, so it cannot see a
    ``price_trace`` that reverts to per-file ``usage``. These assert the dollar
    figure against an independent price of the expected unique turns."""

    def test_subagent_row_cost_prices_unique_turns_only(self, tmp_path: Path) -> None:
        main = _build_fork_session(tmp_path)
        analysis = analyze_session(main)
        row_cost = sum(
            r.cost for r in analysis.token_metrics.by_model if r.origin == "subagent"
        )
        assert row_cost == pytest.approx(_price_of([_SHARED, _Z_OWN, _A2_OWN], main))

    def test_per_agent_cost_prices_unique_turns_only(self, tmp_path: Path) -> None:
        main = _build_fork_session(tmp_path)
        analysis = analyze_session(main)
        per_agent_cost = sum(
            m.estimated_total_cost_usd for m in analysis.agent_metrics.by_agent_type.values()
        )
        assert per_agent_cost == pytest.approx(_price_of([_SHARED, _Z_OWN, _A2_OWN], main))

    def test_top_line_cost_is_main_plus_unique_subagent_turns(self, tmp_path: Path) -> None:
        main = _build_fork_session(tmp_path)
        analysis = analyze_session(main)
        expected = _price_of([_MAIN_1, _MAIN_2], main) + _price_of(
            [_SHARED, _Z_OWN, _A2_OWN], main,
        )
        assert analysis.token_metrics.total_cost == pytest.approx(expected)


class TestNoIdAndCountedPredicate:
    """Round-1 E: "no id" means ``None`` *or* ``""`` (the #696 merge's reading),
    and main-session ids count only for turns the parent fold counted."""

    def test_empty_string_ids_always_count(self) -> None:
        a = _trace("a", [("", 3), ("", 4)])
        dedupe_trace_turns({""}, [a])
        assert trace_spend_usage(a).output_tokens == 7

    def test_uncounted_main_turns_do_not_claim_trace_turns(self) -> None:
        from agentfluent.core.session import SessionMessage

        messages = [
            SessionMessage(type="assistant", message_id="counted", model=_MODEL,
                           usage=Usage(output_tokens=1)),
            SessionMessage(type="assistant", message_id="synthetic", model="<synthetic>",
                           usage=Usage()),
            SessionMessage(type="assistant", message_id="no-usage", model=_MODEL),
            SessionMessage(type="assistant", message_id="", model=_MODEL,
                           usage=Usage(output_tokens=1)),
            SessionMessage(type="user", message_id="user-line"),
        ]
        assert counted_parent_message_ids(messages) == {"counted"}

        trace = _trace("t", [("counted", 1), ("synthetic", 10), ("no-usage", 100)])
        dedupe_trace_turns(counted_parent_message_ids(messages), [trace])
        assert trace_spend_usage(trace).output_tokens == 110


class TestRestPrecedence:
    """Round-1 G / AC3 option (a): the ``(depth, agent_id)`` order for traces
    after depth 1 is owned by ``dedupe_trace_turns``, not by its caller."""

    @staticmethod
    def _at_depth(agent_id: str, depth: int, turns: list[tuple[str | None, int]]) -> SubagentTrace:
        trace = _trace(agent_id, turns)
        trace.depth = depth
        return trace

    def test_depth_one_before_rest_and_rest_sorted(self) -> None:
        parent = _trace("p", [("p1", 1)])
        late_d2 = self._at_depth("b", 2, [("x", 10), ("p1", 1)])
        early_d2 = self._at_depth("a", 2, [("x", 10), ("y", 100)])
        d3 = self._at_depth("0", 3, [("y", 100), ("z", 1000)])
        # Rest is handed over in an order that disagrees with (depth, agent_id).
        dedupe_trace_turns(set(), [parent], [d3, late_d2, early_d2])
        assert trace_spend_usage(parent).output_tokens == 1
        assert trace_spend_usage(early_d2).output_tokens == 110  # sorts first at depth 2
        assert trace_spend_usage(late_d2).output_tokens == 0  # x taken by "a", p1 by parent
        assert trace_spend_usage(d3).output_tokens == 1000  # depth 3 after every depth 2

    def test_rest_never_outranks_depth_one(self) -> None:
        d1 = _trace("z", [("s", 5)])
        child = self._at_depth("a", 2, [("s", 5)])
        dedupe_trace_turns(set(), [d1], [child])
        assert trace_spend_usage(d1).output_tokens == 5
        assert trace_spend_usage(child).output_tokens == 0


class TestVerboseInvocationTable:
    def test_tokens_column_shows_deduplicated_spend(self, tmp_path: Path) -> None:
        """Round-1 D: the Per-Invocation Detail Tokens column reads
        ``trace_spend_usage``, matching the agent table, not per-file usage."""
        from agentfluent.analytics.pipeline import AnalysisResult
        from agentfluent.cli.formatters.helpers import format_tokens

        analysis = analyze_session(_build_fork_session(tmp_path))
        result = AnalysisResult(
            sessions=[analysis],
            token_metrics=analysis.token_metrics,
            agent_metrics=analysis.agent_metrics,
            session_count=1,
        )
        console = Console(record=True, width=240, force_terminal=False)
        format_analysis_table(console, result, verbose=True)
        text = console.export_text()
        detail = text[text.index("Per-Invocation Detail"):]
        assert format_tokens(_total(_A2_OWN)) in detail
        assert format_tokens(_total(_SHARED) + _total(_A2_OWN)) not in detail


class TestNonSpendTracesClaimNothing:
    """Round-2 r2.recheck.1: a trace every spend reader excludes must not claim
    a message id, or a turn it shares with a later trace is counted nowhere."""

    @pytest.mark.parametrize("model", [None, "<synthetic>"])
    def test_excluded_trace_leaves_shared_turn_to_the_next(self, model: str | None) -> None:
        excluded = _trace("a", [("s", 1000)])
        excluded.model = model
        kept = _trace("b", [("s", 1000), ("own", 1)])
        dedupe_trace_turns(set(), [excluded, kept])
        assert excluded.spend_usage is None
        assert trace_spend_usage(kept).output_tokens == 1001

    def test_excluded_trace_contributes_nothing_to_the_fold(self) -> None:
        from agentfluent.analytics.tokens import compute_subagent_token_metrics

        excluded = _trace("a", [("s", 1000)])
        excluded.model = "<synthetic>"
        kept = _trace("b", [("s", 1000), ("own", 1)])
        dedupe_trace_turns(set(), [excluded, kept])
        rows = compute_subagent_token_metrics([excluded, kept])
        assert sum(r.output_tokens for r in rows) == 1001


class TestPipelineHandsChildrenToDedup:
    """Acceptance-gate Class B survivor P02: ``_dedupe_session_trace_turns``
    must pass attached children to the dedup, or a depth-2 child keeps per-file
    usage including turns its parent already counted."""

    def test_child_loses_turn_its_parent_counted(self) -> None:
        from agentfluent.agents.models import AgentInvocation

        parent = _trace("parent", [("x", 10)])
        child = _trace("child", [("x", 10), ("c", 1)])
        child.depth = 2
        parent.children.append(child)
        invocation = AgentInvocation(
            agent_type="worker", description="d", prompt="p", tool_use_id="toolu_1",
            trace=parent,
        )
        pipeline_mod._dedupe_session_trace_turns([], [invocation])
        assert trace_spend_usage(parent).output_tokens == 10
        assert child.spend_usage is not None
        assert trace_spend_usage(child).output_tokens == 1


class TestLineageSkipsIdlessEdges:
    """Acceptance-gate Class B survivor L01: a sidecar with no ``toolUseId``
    contributes no edge, so it must not send ``resolve_lineage`` down the
    depth->=2 escalation path -- which reads every sibling trace file line by
    line (``build_emitter_index``) to resolve an edge that does not exist."""

    def test_idless_sidecar_does_not_trigger_the_emitter_scan(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import agentfluent.traces.lineage as lineage_mod

        subagents = tmp_path / "s" / "subagents"
        subagents.mkdir(parents=True)
        for agent_id, sidecar in (
            ("d1", {"agentType": "worker", "toolUseId": "toolu_main"}),
            ("cr1", {"agentType": "general-purpose", "name": "code-review"}),
        ):
            (subagents / f"agent-{agent_id}.jsonl").write_text("{}\n")
            (subagents / f"agent-{agent_id}.meta.json").write_text(json.dumps(sidecar))

        def _no_scan(*_args: object, **_kwargs: object) -> dict[str, str]:
            raise AssertionError("emitter index built for an edge-less sidecar")

        monkeypatch.setattr(lineage_mod, "build_emitter_index", _no_scan)
        lineages = resolve_lineage(discover_session_subagents(tmp_path / "s"), {"toolu_main"})
        assert lineages["d1"].depth == 1
        assert lineages["cr1"].depth == 1
        assert lineages["cr1"].parent_invocation_id is None
        assert lineages["cr1"].agent_type == "general-purpose"
