"""Fixture-locks the ``totalTokens`` semantics ratified in D056 (#595 AC#3).

Two independent facts, both measured against the live corpus at plan time
(~1036 trace files / 683 rollups) and encoded here so a future change that
violates either one fails loudly:

**(A) exclusive of children** -- a delegating agent's ``totalTokens`` does not
contain the tokens of agents it spawned. This is what AC#3 asked, and it is why
the multi-level linker introduces no parent/child double-counting.

**(B) not cumulative spend** -- ``totalTokens`` equals the agent's *final
assistant turn* usage, not the sum over its turns (575/683 exact final-turn
matches; **0/683** sum-of-turns matches). It is a final-turn context-size proxy.

The ``nested_session`` per-turn usage values mirror the live capture this
fixture anonymizes, so both properties hold on the bytes rather than by
assertion alone. Guarding (B) matters more than guarding (A): the name
``totalTokens`` invites the cumulative reading, so the inequality is the fact a
future contributor is most likely to "fix" in the wrong direction.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

_FIXTURES = Path(__file__).parent.parent / "fixtures"
_NESTED = _FIXTURES / "nested_session"
_NESTED_MAIN = _NESTED / "nested-session-1.jsonl"
_NESTED_SUBAGENTS = _NESTED / "nested-session-1" / "subagents"
_SDK_MAIN = _FIXTURES / "sdk_session" / "sdk-main-1.jsonl"

_USAGE_KEYS = (
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
)
_TRAILER_TOKENS = re.compile(r"subagent_tokens:\s*(\d+)")


def _lines(path: Path) -> list[dict[str, Any]]:
    return [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()]


def _turn_totals(trace: Path) -> list[int]:
    """Per-assistant-turn usage totals, in file order."""
    totals = []
    for line in _lines(trace):
        usage = (line.get("message") or {}).get("usage")
        if usage:
            totals.append(sum(usage.get(k, 0) or 0 for k in _USAGE_KEYS))
    return totals


def _rollups(main: Path) -> dict[str, dict[str, Any]]:
    """``agentId -> toolUseResult`` for every rollup in a main session."""
    out = {}
    for line in _lines(main):
        result = line.get("toolUseResult")
        if isinstance(result, dict) and result.get("agentId"):
            out[result["agentId"]] = result
    return out


def _trailer_tokens(trace: Path) -> list[int]:
    """``subagent_tokens:`` values from inline prose trailers in a trace.

    The depth->=2 channel: such a ``tool_result`` carries no ``toolUseResult``,
    only this prose. Parsed here to assert on the fixture -- production code
    deliberately does not read it (see ``traces/sidecar`` module docstring).
    """
    found = []
    for line in _lines(trace):
        content = (line.get("message") or {}).get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            for inner in block.get("content") or []:
                if isinstance(inner, dict):
                    found += [int(m) for m in _TRAILER_TOKENS.findall(inner.get("text") or "")]
    return found


class TestExclusiveOfChildren:
    """(A) -- the property AC#3 named."""

    def test_parent_rollup_excludes_child_tokens(self) -> None:
        worker = _NESTED_SUBAGENTS / "agent-worker001.jsonl"
        parent_total = _rollups(_NESTED_MAIN)["worker001"]["totalTokens"]
        child_total = _trailer_tokens(worker)[0]
        parent_final_turn = _turn_totals(worker)[-1]

        # The property, computed rather than pinned: the parent's reported total
        # IS its own final turn, and is NOT that turn plus the child's tokens.
        # An inclusive rollup would satisfy the second, not the first -- so the
        # pair distinguishes the two hypotheses instead of restating a literal.
        assert parent_total == parent_final_turn, "parent total == its OWN final turn"
        assert parent_total != parent_final_turn + child_total, (
            "an inclusive rollup would read own-final-turn + child tokens; "
            "this one does not, so children are excluded"
        )
        assert child_total > 0, "the child must have spent tokens for this to bite"

    def test_depth2_result_carries_no_rollup(self) -> None:
        """The asymmetry forcing (A) to be settled from the child's own trace."""
        worker = _NESTED_SUBAGENTS / "agent-worker001.jsonl"
        assert not any("toolUseResult" in line for line in _lines(worker)), (
            "a depth->=2 tool_result carries no toolUseResult -- only prose"
        )
        assert _trailer_tokens(worker), "...so the tokens live in the prose trailer"


class TestNotCumulativeSpend:
    """(B) -- the larger finding: final-turn proxy, never sum-of-turns."""

    @pytest.mark.parametrize(
        ("trace_name", "expected"),
        [("agent-worker001.jsonl", 5495), ("agent-leaf0001.jsonl", 3925)],
    )
    def test_total_equals_final_turn_not_sum(self, trace_name: str, expected: int) -> None:
        turns = _turn_totals(_NESTED_SUBAGENTS / trace_name)

        assert len(turns) >= 2, "need >1 turn for the inequality to have meaning"
        assert turns[-1] == expected, "totalTokens == the FINAL turn's usage"
        assert sum(turns) != expected, "totalTokens is NOT the sum over turns"
        assert sum(turns) > expected, "the sum runs well above the reported total"

    def test_sdk_fixture_rollup_matches_child_final_turn(self) -> None:
        """Same property on the depth-1 fixture, via the rollup channel.

        AC#4's ``sdk_session`` clause. Requires a multi-turn child trace: on a
        single-turn trace ``final == sum`` and the property is untestable.
        """
        rollup = _rollups(_SDK_MAIN)["child0000001"]
        turns = _turn_totals(
            _FIXTURES / "sdk_session" / "sdk-main-1" / "subagents" / "agent-child0000001.jsonl"
        )
        assert len(turns) >= 2, "single-turn trace cannot distinguish final from sum"
        assert rollup["totalTokens"] == turns[-1], "rollup == the child's FINAL turn"
        assert rollup["totalTokens"] != sum(turns), "rollup is NOT the sum of turns"
        assert sum(turns) > rollup["totalTokens"]

    def test_cache_read_is_recounted_each_turn(self) -> None:
        """Why summing turns overstates: cache_read repeats across turns."""
        reads = []
        for line in _lines(_NESTED_SUBAGENTS / "agent-worker001.jsonl"):
            usage = (line.get("message") or {}).get("usage")
            if usage:
                reads.append(usage.get("cache_read_input_tokens", 0) or 0)
        assert any(r > 0 for r in reads), "fixture must exercise cache_read"
