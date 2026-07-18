"""Tests for the ``agent-<agentId>.meta.json`` sidecar reader (#595 PR A).

The sidecar is the only *structured* child-to-parent edge at depth >= 2, so the
happy path matters -- but so does total degradation: sidecars are a Claude Code
format evolution, and older sessions have trace files without them. Every
failure mode must yield ``None``, never an exception.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentfluent.traces.sidecar import (
    SubagentSidecar,
    read_subagent_sidecar,
    sidecar_path_for,
)

_NESTED_SUBAGENTS = (
    Path(__file__).parent.parent
    / "fixtures"
    / "nested_session"
    / "nested-session-1"
    / "subagents"
)


class TestSidecarPathFor:
    def test_derives_meta_json_beside_trace(self) -> None:
        assert sidecar_path_for(Path("/x/subagents/agent-abc.jsonl")) == Path(
            "/x/subagents/agent-abc.meta.json"
        )

    def test_agent_id_containing_a_dot_is_not_truncated(self) -> None:
        """Regression: chained ``with_suffix`` would yield ``agent-a.meta.json``."""
        assert sidecar_path_for(Path("/x/agent-a.b.jsonl")) == Path("/x/agent-a.b.meta.json")


class TestReadSidecarFixtures:
    """Against the committed fixture -- the real on-disk shape."""

    def test_reads_level1_sidecar(self) -> None:
        meta = read_subagent_sidecar(_NESTED_SUBAGENTS / "agent-worker001.jsonl")
        assert meta is not None
        assert meta.agent_type == "worker"
        assert meta.tool_use_id == "toolu_main_to_worker"
        assert meta.description

    def test_reads_depth2_sidecar_and_its_edge_points_into_the_parent_trace(self) -> None:
        """The load-bearing property: the depth-2 edge label names a tool_use
        emitted in the *worker's* trace, not in the main session."""
        meta = read_subagent_sidecar(_NESTED_SUBAGENTS / "agent-leaf0001.jsonl")
        assert meta is not None
        assert meta.agent_type == "leaf-summarizer"
        assert meta.tool_use_id == "toolu_worker_to_leaf"

        worker_bytes = (_NESTED_SUBAGENTS / "agent-worker001.jsonl").read_text()
        assert meta.tool_use_id in worker_bytes, (
            "depth-2 edge must resolve into the parent trace, proving the join "
            "is cross-file and unavailable from the main session alone"
        )


class TestDegradation:
    """Every failure mode returns None rather than raising."""

    def test_missing_sidecar_returns_none(self, tmp_path: Path) -> None:
        trace = tmp_path / "agent-nosidecar.jsonl"
        trace.write_text("{}\n")
        assert read_subagent_sidecar(trace) is None

    def test_missing_trace_entirely_returns_none(self, tmp_path: Path) -> None:
        assert read_subagent_sidecar(tmp_path / "agent-ghost.jsonl") is None

    @pytest.mark.parametrize(
        "payload",
        [
            "not json at all",
            "",
            "[1, 2, 3]",  # valid JSON, wrong shape
            '"a string"',
            "null",
        ],
    )
    def test_malformed_payload_returns_none(self, tmp_path: Path, payload: str) -> None:
        trace = tmp_path / "agent-bad.jsonl"
        trace.write_text("{}\n")
        (tmp_path / "agent-bad.meta.json").write_text(payload)
        assert read_subagent_sidecar(trace) is None

    def test_non_utf8_payload_returns_none(self, tmp_path: Path) -> None:
        """Regression: ``UnicodeDecodeError`` subclasses ``ValueError``, not
        ``OSError``, so it escaped the original except clause."""
        trace = tmp_path / "agent-binary.jsonl"
        trace.write_text("{}\n")
        (tmp_path / "agent-binary.meta.json").write_bytes(b"\xff\xfe\x00garbage")
        assert read_subagent_sidecar(trace) is None

    @pytest.mark.parametrize(
        "obj",
        [
            {"description": "d", "toolUseId": "t"},  # no agentType
            {"agentType": "a", "description": "d"},  # no toolUseId
            {},
        ],
    )
    def test_missing_required_keys_returns_none(
        self, tmp_path: Path, obj: dict[str, str]
    ) -> None:
        trace = tmp_path / "agent-partial.jsonl"
        trace.write_text("{}\n")
        (tmp_path / "agent-partial.meta.json").write_text(json.dumps(obj))
        assert read_subagent_sidecar(trace) is None

    def test_description_defaults_when_absent(self, tmp_path: Path) -> None:
        trace = tmp_path / "agent-nodesc.jsonl"
        trace.write_text("{}\n")
        (tmp_path / "agent-nodesc.meta.json").write_text(
            json.dumps({"agentType": "a", "toolUseId": "t"})
        )
        meta = read_subagent_sidecar(trace)
        assert meta is not None
        assert meta.description == ""


class TestForwardCompatibility:
    def test_unknown_keys_are_ignored(self, tmp_path: Path) -> None:
        """Matches ToolResultMetadata's posture: upstream may add fields."""
        trace = tmp_path / "agent-future.jsonl"
        trace.write_text("{}\n")
        (tmp_path / "agent-future.meta.json").write_text(
            json.dumps(
                {
                    "agentType": "a",
                    "description": "d",
                    "toolUseId": "t",
                    "someFutureField": {"nested": True},
                }
            )
        )
        meta = read_subagent_sidecar(trace)
        assert meta is not None
        assert meta.agent_type == "a"

    def test_model_accepts_snake_case_too(self) -> None:
        """``populate_by_name`` -- construction in tests//code needn't use aliases."""
        meta = SubagentSidecar(agent_type="a", description="d", tool_use_id="t")
        assert meta.tool_use_id == "t"
