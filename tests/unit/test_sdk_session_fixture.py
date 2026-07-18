"""SDK main-session fixture — locks the #521 findings for #112's consumers.

The fixture ``tests/fixtures/sdk_session/`` is a hand-crafted, secret-free Agent
SDK **main session** (verified against SDK 0.2.106 / CLI 2.1.185). It isolates
the signals a main-session model-routing consumer (#112) keys on, which no other
fixture carries together:

* **Discriminator** — ``entrypoint == "sdk-py"`` on every user/assistant line
  (the D013 SDK-vs-CC-interactive marker), corroborated by ``promptSource ==
  "sdk"`` on the prompt line.
* **Main-session model** — ``assistant.message.model`` == the configured
  ``ClaudeAgentOptions.model`` (``claude-sonnet-4-6``), exposed by the production
  parser as ``SessionMessage.model``.
* **Model divergence ("the #112 artifact")** — a sonnet main delegates to a haiku
  child; ``toolUseResult.resolvedModel`` reports the *child's* model. The parser
  now surfaces it as ``ToolResultMetadata.resolved_model`` (#593 S3), so #112 can
  verify a configured subagent model with no cross-file join; asserted against both
  the raw bytes (source of truth) and the parsed metadata.

These tests assert the fixture parses cleanly, that discovery finds the child,
and that the load-bearing fields are present. The parser/router that consumes
these signals is downstream of the discovery epic (#112).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agentfluent.core.parser import parse_session
from agentfluent.core.session import ToolResultMetadata, classify_session
from agentfluent.traces.discovery import discover_session_subagents

_FIXTURE = Path(__file__).parent.parent / "fixtures" / "sdk_session"
_MAIN_JSONL = _FIXTURE / "sdk-main-1.jsonl"
_SESSION_DIR = _FIXTURE / "sdk-main-1"
_CHILD_JSONL = _SESSION_DIR / "subagents" / "agent-child0000001.jsonl"

_MAIN_MODEL = "claude-sonnet-4-6"
_CHILD_MODEL = "claude-haiku-4-5-20251001"


def _raw(path: Path) -> list[dict[str, Any]]:
    return [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()]


class TestParsesCleanly:
    def test_main_session_parses(self) -> None:
        msgs = parse_session(_MAIN_JSONL)
        assert len(msgs) == 4
        assert [m.type for m in msgs] == ["user", "assistant", "user", "assistant"]

    def test_main_session_model_is_the_configured_model(self) -> None:
        msgs = parse_session(_MAIN_JSONL)
        assistant_models = [m.model for m in msgs if m.type == "assistant"]
        assert assistant_models == [_MAIN_MODEL, _MAIN_MODEL]

    def test_tool_use_result_metadata_is_attached(self) -> None:
        msgs = parse_session(_MAIN_JSONL)
        metas = [m.metadata for m in msgs if m.metadata is not None]
        assert len(metas) == 1
        assert metas[0].agent_id == "child0000001"
        assert metas[0].total_tokens == 3925
        assert metas[0].tool_uses == 2


class TestDiscriminator:
    def test_entrypoint_is_sdk_py_on_every_line(self) -> None:
        # `entrypoint` is a top-level line field carrying the D013 marker.
        # Assert on the raw bytes...
        assert all(obj.get("entrypoint") == "sdk-py" for obj in _raw(_MAIN_JSONL))
        assert all(obj.get("entrypoint") == "sdk-py" for obj in _raw(_CHILD_JSONL))
        # ...and that the parser now surfaces it on SessionMessage and the
        # session classifies as `sdk` (#591 S1).
        main_msgs = parse_session(_MAIN_JSONL)
        assert all(m.entrypoint == "sdk-py" for m in main_msgs)
        assert classify_session(main_msgs) == "sdk"

    def test_prompt_source_marks_the_prompt_line(self) -> None:
        prompt_line = _raw(_MAIN_JSONL)[0]
        assert prompt_line["type"] == "user"
        assert prompt_line["promptSource"] == "sdk"


class TestModelDivergence:
    def test_resolved_model_reports_the_child_model(self) -> None:
        # `resolvedModel` reports the *child's* concrete model — the #112 artifact.
        # Assert against the raw bytes (source of truth)...
        result_lines = [obj for obj in _raw(_MAIN_JSONL) if "toolUseResult" in obj]
        assert len(result_lines) == 1
        tur = result_lines[0]["toolUseResult"]
        assert tur["resolvedModel"] == _CHILD_MODEL
        assert tur["resolvedModel"] != _MAIN_MODEL
        assert tur["status"] == "completed"

    def test_parser_surfaces_resolved_model_on_metadata(self) -> None:
        # ...and that the parser now surfaces it (#593 S3) so #112 needs no
        # cross-file join into the child trace.
        msgs = parse_session(_MAIN_JSONL)
        metas = [m.metadata for m in msgs if m.metadata is not None]
        assert len(metas) == 1
        assert metas[0].resolved_model == _CHILD_MODEL
        assert metas[0].resolved_model != _MAIN_MODEL

    def test_child_trace_ran_the_resolved_model(self) -> None:
        child_models = [
            obj["message"]["model"]
            for obj in _raw(_CHILD_JSONL)
            if obj.get("type") == "assistant"
        ]
        # Every turn ran the resolved model. Asserted per-element rather than
        # against a fixed-length list: the child trace became multi-turn when
        # #595 brought its per-turn usage in line with the live capture, and
        # this test's subject is the MODEL, not the turn count.
        assert child_models, "child trace must have assistant turns"
        assert all(m == _CHILD_MODEL for m in child_models)


class TestResolvedModelField:
    """Unit-level locks for the #593 field, independent of the fixture."""

    def test_alias_populates_resolved_model(self) -> None:
        meta = ToolResultMetadata.model_validate({"resolvedModel": _CHILD_MODEL})
        assert meta.resolved_model == _CHILD_MODEL

    def test_absent_resolved_model_is_none(self) -> None:
        # A tool result with no `resolvedModel` parses without crashing.
        meta = ToolResultMetadata.model_validate({"agentId": "x", "totalTokens": 1})
        assert meta.resolved_model is None


class TestDiscoveryAndLinkage:
    def test_discovery_finds_the_child_trace(self) -> None:
        infos = discover_session_subagents(_SESSION_DIR)
        assert sorted(i.agent_id for i in infos) == ["child0000001"]
        assert all(i.path.suffix == ".jsonl" for i in infos)

    def test_four_way_linkage_holds(self) -> None:
        main = _raw(_MAIN_JSONL)
        tool_use_id = next(
            b["id"]
            for obj in main
            if obj["type"] == "assistant"
            for b in obj["message"]["content"]
            if isinstance(b, dict) and b.get("type") == "tool_use"
        )
        result_line = next(obj for obj in main if "toolUseResult" in obj)
        tool_result_id = result_line["message"]["content"][0]["tool_use_id"]
        agent_id = result_line["toolUseResult"]["agentId"]
        sidecar = json.loads(
            (_SESSION_DIR / "subagents" / "agent-child0000001.meta.json").read_text()
        )
        child_top_level_agent_id = _raw(_CHILD_JSONL)[0]["agentId"]

        assert tool_use_id == tool_result_id == sidecar["toolUseId"]
        assert agent_id == "child0000001" == child_top_level_agent_id
