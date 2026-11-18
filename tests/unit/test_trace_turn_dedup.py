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

import agentfluent.analytics.pipeline as pipeline_mod
from agentfluent.analytics.pipeline import analyze_session
from agentfluent.analytics.tokens import dedupe_trace_turns, trace_spend_usage
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
_A_OWN = _usage(5, 7)
_B_OWN = _usage(13, 17)


def _build_fork_session(root: Path) -> Path:
    """Main session spawning two fork agents that replay shared context.

    * ``forkA`` replays ``msg_shared`` and also the main session's ``msg_main1``,
      then does its own turn ``msg_a_own`` -- written as two block-per-line
      fragments sharing one ``message.id`` (#696), which must still count once.
    * ``forkB`` replays ``msg_shared`` and does its own turn ``msg_b_own``.
    """
    main = root / "fork-session.jsonl"
    _write(main, [
        _user("go", "2026-11-01T00:00:00Z"),
        _assistant("msg_main1", _MAIN_1, [
            {"type": "tool_use", "id": "toolu_a", "name": "Agent",
             "input": {"subagent_type": "fork", "description": "a", "prompt": "a"}},
        ], "2026-11-01T00:00:01Z"),
        _agent_result("toolu_a", "forkA", "2026-11-01T00:00:05Z"),
        _assistant("msg_main2", _MAIN_2, [
            {"type": "tool_use", "id": "toolu_b", "name": "Agent",
             "input": {"subagent_type": "fork", "description": "b", "prompt": "b"}},
        ], "2026-11-01T00:00:06Z"),
        _agent_result("toolu_b", "forkB", "2026-11-01T00:00:09Z"),
    ])
    subagents = root / "fork-session" / "subagents"
    subagents.mkdir(parents=True)
    _write(subagents / "agent-forkA.jsonl", [
        _user("a", "2026-11-01T00:00:02Z", sidechain=True),
        _assistant("msg_shared", _SHARED, _text("ctx"), "2026-11-01T00:00:02Z", sidechain=True),
        _assistant("msg_main1", _MAIN_1, _text("replay"), "2026-11-01T00:00:03Z", sidechain=True),
        _assistant("msg_a_own", _A_OWN, [{"type": "thinking", "thinking": "t"}],
                   "2026-11-01T00:00:04Z", sidechain=True),
        _assistant("msg_a_own", _A_OWN, _text("own"), "2026-11-01T00:00:04Z", sidechain=True),
    ])
    _write(subagents / "agent-forkB.jsonl", [
        _user("b", "2026-11-01T00:00:07Z", sidechain=True),
        _assistant("msg_shared", _SHARED, _text("ctx"), "2026-11-01T00:00:07Z", sidechain=True),
        _assistant("msg_b_own", _B_OWN, _text("own"), "2026-11-01T00:00:08Z", sidechain=True),
    ])
    for agent_id, tool_use_id in (("forkA", "toolu_a"), ("forkB", "toolu_b")):
        (subagents / f"agent-{agent_id}.meta.json").write_text(json.dumps({
            "agentType": "fork", "description": agent_id, "toolUseId": tool_use_id,
            "isFork": True, "spawnDepth": 1,
        }))
    return main


def _total(u: dict[str, int]) -> int:
    return sum(u.values())


# What the subagent side should spend: the shared turn once, each fork's own
# turn once, and nothing of main's replayed turn (main counted it first).
_EXPECTED_SUBAGENT_TOKENS = _total(_SHARED) + _total(_A_OWN) + _total(_B_OWN)
_NAIVE_SUBAGENT_TOKENS = (
    2 * _total(_SHARED) + _total(_MAIN_1) + _total(_A_OWN) + _total(_B_OWN)
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
        """forkA (first invocation) keeps the shared turn; forkB loses it; forkA
        loses the turn main already counted."""
        analysis = analyze_session(_build_fork_session(tmp_path))
        traces = {inv.agent_id: inv.trace for inv in analysis.invocations}
        fork_a, fork_b = traces["forkA"], traces["forkB"]
        assert fork_a is not None and fork_b is not None
        assert trace_spend_usage(fork_a).total_tokens == _total(_SHARED) + _total(_A_OWN)
        assert trace_spend_usage(fork_b).total_tokens == _total(_B_OWN)

    def test_public_usage_and_json_are_unchanged(self, tmp_path: Path) -> None:
        """``trace.usage`` stays the per-file sum, and neither new field leaks
        into JSON output (D055)."""
        analysis = analyze_session(_build_fork_session(tmp_path))
        fork_b = next(inv.trace for inv in analysis.invocations if inv.agent_id == "forkB")
        assert fork_b is not None
        assert fork_b.usage.total_tokens == _total(_SHARED) + _total(_B_OWN)
        dumped = json.loads(fork_b.model_dump_json())
        assert "turn_usages" not in dumped
        assert "spend_usage" not in dumped


class TestParserTurnUsages:
    def test_fragments_merge_to_one_turn_and_sum_matches_usage(self, tmp_path: Path) -> None:
        main = _build_fork_session(tmp_path)
        trace = parse_subagent_trace(main.with_suffix("") / "subagents" / "agent-forkA.jsonl")
        ids = [t.message_id for t in trace.turn_usages]
        assert ids == ["msg_shared", "msg_main1", "msg_a_own"]
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
        dedupe_trace_turns([first, second], {"m1"})
        assert trace_spend_usage(first).output_tokens == 110
        assert trace_spend_usage(second).output_tokens == 1000

    def test_order_is_the_callers(self) -> None:
        first = _trace("a", [("s", 10)])
        second = _trace("b", [("s", 10), ("b1", 1)])
        dedupe_trace_turns([second, first], set())
        assert trace_spend_usage(second).output_tokens == 11
        assert trace_spend_usage(first).output_tokens == 0

    def test_none_ids_always_count(self) -> None:
        a = _trace("a", [(None, 3)])
        b = _trace("b", [(None, 3)])
        dedupe_trace_turns([a, b], set())
        assert trace_spend_usage(a).output_tokens == 3
        assert trace_spend_usage(b).output_tokens == 3

    def test_same_trace_twice_is_processed_once(self) -> None:
        a = _trace("a", [("x", 4)])
        dedupe_trace_turns([a, a], set())
        assert trace_spend_usage(a).output_tokens == 4

    def test_trace_without_turns_spends_its_usage(self) -> None:
        bare = SubagentTrace(
            agent_id="bare", delegation_prompt="", usage=Usage(output_tokens=9), model=_MODEL,
        )
        dedupe_trace_turns([bare], {"anything"})
        assert bare.spend_usage is None
        assert trace_spend_usage(bare).output_tokens == 9

    def test_public_usage_is_not_mutated(self) -> None:
        a = _trace("a", [("m", 5), ("own", 1)])
        dedupe_trace_turns([a], {"m"})
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
