"""Tests for the subagent trace linker."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from agentfluent.agents.models import AgentInvocation
from agentfluent.analytics.pipeline import analyze_session
from agentfluent.traces.linker import link_traces
from agentfluent.traces.models import UNKNOWN_AGENT_TYPE, SubagentTrace
from agentfluent.traces.parser import parse_subagent_trace

WriteJSONL = Callable[[str, list[dict[str, Any]]], Path]


def _invocation(
    agent_type: str = "plan",
    *,
    agent_id: str | None = "aid-1",
    tool_use_id: str = "toolu_1",
) -> AgentInvocation:
    return AgentInvocation(
        agent_type=agent_type,
        description="",
        prompt="",
        tool_use_id=tool_use_id,
        agent_id=agent_id,
    )


def _trace(agent_id: str = "aid-1") -> SubagentTrace:
    """A trace with the parser's default UNKNOWN_AGENT_TYPE."""
    return SubagentTrace(
        agent_id=agent_id,
        delegation_prompt="do work",
    )


class TestLinkTraces:
    def test_empty_invocations_returns_empty(self) -> None:
        calls: list[str] = []

        def loader(aid: str) -> SubagentTrace | None:
            calls.append(aid)
            return _trace(aid)

        result = link_traces([], loader)
        assert result == []
        assert calls == []

    def test_agent_id_none_skips_loader(self) -> None:
        calls: list[str] = []

        def loader(aid: str) -> SubagentTrace | None:
            calls.append(aid)
            return _trace(aid)

        inv = _invocation(agent_id=None)
        result = link_traces([inv], loader)

        assert calls == []
        assert result[0].trace is None

    def test_match_links_trace_and_overwrites_agent_type(self) -> None:
        inv = _invocation(agent_type="plan", agent_id="aid-1")
        trace = _trace("aid-1")
        assert trace.agent_type == UNKNOWN_AGENT_TYPE

        def loader(aid: str) -> SubagentTrace | None:
            return trace if aid == "aid-1" else None

        result = link_traces([inv], loader)

        assert result[0].trace is trace
        # Parent's agent_type is authoritative.
        assert result[0].trace.agent_type == "plan"

    def test_no_match_leaves_trace_none(self) -> None:
        def loader(aid: str) -> SubagentTrace | None:
            return None

        inv = _invocation(agent_id="aid-1")
        result = link_traces([inv], loader)
        assert result[0].trace is None

    def test_idempotent_overwrite_when_agent_type_matches(self) -> None:
        # Trace already carries the same agent_type as the parent — still gets
        # assigned without any error.
        inv = _invocation(agent_type="pm", agent_id="aid-1")
        trace = _trace("aid-1")
        trace.agent_type = "pm"

        def loader(aid: str) -> SubagentTrace | None:
            return trace

        result = link_traces([inv], loader)
        assert result[0].trace.agent_type == "pm"

    def test_returns_same_list_object(self) -> None:
        """Mutation semantics: the linker modifies the input list in place."""
        def loader(aid: str) -> SubagentTrace | None:
            return None

        invs = [_invocation()]
        result = link_traces(invs, loader)
        assert result is invs

    def test_loader_receives_exact_agent_id(self) -> None:
        received: list[str] = []

        def loader(aid: str) -> SubagentTrace | None:
            received.append(aid)
            return None

        invs = [
            _invocation(agent_id="aid-x"),
            _invocation(agent_id="aid-y"),
            _invocation(agent_id=None),  # not called
        ]
        link_traces(invs, loader)
        assert received == ["aid-x", "aid-y"]

    def test_multiple_invocations_same_agent_id_call_loader_each_time(
        self,
    ) -> None:
        # Each invocation is matched independently. Caching is the caller's
        # concern (the closure around the path map); the linker doesn't dedup.
        call_count = [0]

        def loader(aid: str) -> SubagentTrace | None:
            call_count[0] += 1
            return _trace(aid)

        invs = [_invocation(agent_id="shared"), _invocation(agent_id="shared")]
        link_traces(invs, loader)
        assert call_count[0] == 2
        assert invs[0].trace is not None
        assert invs[1].trace is not None

    def test_mixed_matches_and_misses(self) -> None:
        def loader(aid: str) -> SubagentTrace | None:
            return _trace(aid) if aid == "match" else None

        invs = [
            _invocation(agent_id="match"),
            _invocation(agent_id="miss"),
            _invocation(agent_id=None),
            _invocation(agent_id="match"),
        ]
        link_traces(invs, loader)
        assert invs[0].trace is not None
        assert invs[1].trace is None
        assert invs[2].trace is None
        assert invs[3].trace is not None

    def test_unknown_to_known_overwrite(self) -> None:
        """Trace loaded with UNKNOWN_AGENT_TYPE gets the parent's type."""
        inv = _invocation(agent_type="custom-agent", agent_id="aid-1")
        trace = _trace("aid-1")
        assert trace.agent_type == UNKNOWN_AGENT_TYPE

        def loader(aid: str) -> SubagentTrace | None:
            return trace

        link_traces([inv], loader)
        assert inv.trace is trace
        assert inv.trace.agent_type == "custom-agent"
        assert trace.agent_type == "custom-agent"


def _build_project(
    tmp_path: Path,
    session_uuid: str,
    agent_id: str,
    *,
    include_trace_file: bool,
) -> Path:
    """Create a minimal project layout with optional subagent trace."""
    session_path = tmp_path / f"{session_uuid}.jsonl"
    session_path.write_text(
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "id": "msg_1",
                    "role": "assistant",
                    "model": "claude-opus-4-6",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_1",
                            "name": "Agent",
                            "input": {
                                "subagent_type": "plan",
                                "description": "Plan work",
                                "prompt": "Plan the work",
                            },
                        },
                    ],
                },
                "timestamp": "2026-04-21T10:00:00.000Z",
            },
        )
        + "\n"
        + json.dumps(
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_1",
                            "content": "plan complete",
                        },
                    ],
                },
                "toolUseResult": {
                    "agentId": agent_id,
                    "agentType": "plan",
                    "totalTokens": 100,
                },
                "timestamp": "2026-04-21T10:00:01.000Z",
            },
        )
        + "\n",
    )

    if include_trace_file:
        subagents_dir = tmp_path / session_uuid / "subagents"
        subagents_dir.mkdir(parents=True)
        (subagents_dir / f"agent-{agent_id}.jsonl").write_text(
            json.dumps(
                {
                    "type": "user",
                    "message": {"role": "user", "content": "Plan the work"},
                    "timestamp": "2026-04-21T10:00:00.100Z",
                },
            )
            + "\n"
            + json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "id": "msg_inner",
                        "role": "assistant",
                        "model": "claude-opus-4-6",
                        "content": [{"type": "text", "text": "done"}],
                        "usage": {"input_tokens": 5, "output_tokens": 2},
                    },
                    "timestamp": "2026-04-21T10:00:00.500Z",
                },
            )
            + "\n",
        )

    return session_path


class TestPipelineIntegration:
    """End-to-end wiring: analyze_session discovers and links subagent traces."""

    def test_matching_trace_gets_linked(self, tmp_path: Path) -> None:
        session_path = _build_project(
            tmp_path, "uuid-1", "aid-1", include_trace_file=True,
        )

        result = analyze_session(session_path)

        assert len(result.invocations) == 1
        inv = result.invocations[0]
        assert inv.agent_id == "aid-1"
        assert inv.trace is not None
        # Parent's agent_type ("plan") overwrites the parser's UNKNOWN default.
        assert inv.trace.agent_type == "plan"
        assert inv.trace.agent_id == "aid-1"

    def test_no_matching_trace_leaves_trace_none(self, tmp_path: Path) -> None:
        session_path = _build_project(
            tmp_path, "uuid-1", "aid-1", include_trace_file=False,
        )

        result = analyze_session(session_path)

        assert len(result.invocations) == 1
        assert result.invocations[0].trace is None

    def test_orphan_trace_file_is_ignored(self, tmp_path: Path) -> None:
        # Subagent file exists, but its agent_id doesn't match any invocation.
        session_path = _build_project(
            tmp_path, "uuid-1", "aid-1", include_trace_file=True,
        )
        # Drop the real trace file, add an orphan instead.
        (tmp_path / "uuid-1" / "subagents" / "agent-aid-1.jsonl").unlink()
        (tmp_path / "uuid-1" / "subagents" / "agent-orphan.jsonl").write_text("")

        result = analyze_session(session_path)

        assert result.invocations[0].trace is None

    def test_roundtrip_of_real_parser_preserves_agent_type_overwrite(
        self, tmp_path: Path,
    ) -> None:
        """Read the trace back via parse_subagent_trace and confirm the
        overwrite sticks on the object analyze_session returns."""
        session_path = _build_project(
            tmp_path, "uuid-1", "aid-1", include_trace_file=True,
        )
        trace_path = tmp_path / "uuid-1" / "subagents" / "agent-aid-1.jsonl"

        # Baseline: parser produces UNKNOWN (no linker in the picture).
        baseline_trace = parse_subagent_trace(trace_path)
        assert baseline_trace.agent_type == UNKNOWN_AGENT_TYPE

        # analyze_session applies the linker and the invocation carries an
        # overwritten trace.
        result = analyze_session(session_path)
        assert result.invocations[0].trace is not None
        assert result.invocations[0].trace.agent_type == "plan"
