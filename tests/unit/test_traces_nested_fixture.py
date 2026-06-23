"""Nested (multi-level) subagent fixture — locks the #530 discovery findings.

The fixture ``tests/fixtures/nested_session/`` encodes an
``agent -> worker -> leaf`` chain exactly as the Agent SDK records it (verified
empirically in #530 against SDK 0.2.106 / CLI 2.1.185):

* **Flat layout** — every subagent at every depth is a sibling under one
  ``<session>/subagents/`` dir; there are no nested ``subagents/<id>/subagents/``
  directories.
* **``.meta.json`` sidecars** next to each trace.
* **By-data parent linkage** — a subagent's ``meta.toolUseId`` is the ``Agent``
  ``tool_use`` emitted in its *parent's* trace.
* **Rollup metadata is top-level only** — the rich ``toolUseResult`` rollup is
  attached only on the main session's level-1 result; a depth>=2 spawn's
  ``tool_result`` carries none.

These tests assert that current discovery handles the flat layout and that the
linkage is reconstructable from the bytes. The production *multi-level linker*
that consumes this reconstruction is downstream of this discovery story.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agentfluent.traces.discovery import discover_session_subagents

_FIXTURE = Path(__file__).parent.parent / "fixtures" / "nested_session"
_SESSION_DIR = _FIXTURE / "nested-session-1"
_MAIN_JSONL = _FIXTURE / "nested-session-1.jsonl"
_SUBAGENTS = _SESSION_DIR / "subagents"


def _lines(path: Path) -> list[dict[str, Any]]:
    return [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()]


def _emitter_of_tool_uses(*paths: Path) -> dict[str, str | None]:
    """Map ``tool_use.id -> agentId of the trace that emitted it`` (None = main)."""
    index: dict[str, str | None] = {}
    for path in paths:
        for obj in _lines(path):
            if obj.get("type") != "assistant":
                continue
            content = obj.get("message", {}).get("content", [])
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        index[block["id"]] = obj.get("agentId")
    return index


def _tool_result_has_rollup(path: Path) -> dict[str, bool]:
    """Map ``tool_result.tool_use_id -> whether its line carries toolUseResult``."""
    out: dict[str, bool] = {}
    for obj in _lines(path):
        if obj.get("type") != "user":
            continue
        content = obj.get("message", {}).get("content", [])
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    out[block["tool_use_id"]] = "toolUseResult" in obj
    return out


class TestFlatDiscovery:
    def test_both_levels_are_flat_siblings(self) -> None:
        infos = discover_session_subagents(_SESSION_DIR)
        assert sorted(i.agent_id for i in infos) == ["leaf0001", "worker001"]

    def test_no_nested_subagents_directory_exists(self) -> None:
        assert list(_SESSION_DIR.glob("subagents/*/subagents")) == []

    def test_meta_json_sidecars_are_not_treated_as_traces(self) -> None:
        # Both sidecars exist on disk...
        assert len(list(_SUBAGENTS.glob("*.meta.json"))) == 2
        # ...but discovery returns only .jsonl traces (AGENT_FILENAME_PATTERN
        # excludes `.meta.json` — guards the architect's D(i) concern).
        infos = discover_session_subagents(_SESSION_DIR)
        assert all(i.path.suffix == ".jsonl" for i in infos)


class TestByDataParentLinkage:
    def test_meta_tooluseid_resolves_to_the_emitting_parent_trace(self) -> None:
        index = _emitter_of_tool_uses(
            _MAIN_JSONL,
            _SUBAGENTS / "agent-worker001.jsonl",
            _SUBAGENTS / "agent-leaf0001.jsonl",
        )
        worker_meta = json.loads((_SUBAGENTS / "agent-worker001.meta.json").read_text())
        leaf_meta = json.loads((_SUBAGENTS / "agent-leaf0001.meta.json").read_text())

        # Level-1 worker was spawned by the main session (emitter agentId is None).
        assert index[worker_meta["toolUseId"]] is None
        # Level-2 leaf was spawned by the worker — the cross-file edge that
        # proves depth-2 nesting cannot be read from path shape, only from data.
        assert index[leaf_meta["toolUseId"]] == "worker001"


class TestRollupIsTopLevelOnly:
    def test_top_level_spawn_carries_rollup(self) -> None:
        results = _tool_result_has_rollup(_MAIN_JSONL)
        assert results["toolu_main_to_worker"] is True

    def test_depth_two_spawn_carries_no_rollup(self) -> None:
        results = _tool_result_has_rollup(_SUBAGENTS / "agent-worker001.jsonl")
        assert results["toolu_worker_to_leaf"] is False
