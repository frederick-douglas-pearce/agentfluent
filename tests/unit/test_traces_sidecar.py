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

    def test_deeply_nested_json_returns_none(self, tmp_path: Path) -> None:
        """Regression: ``json.loads`` raises ``RecursionError`` (a
        ``RuntimeError``, so it matched no typed except clause)."""
        trace = tmp_path / "agent-deep.jsonl"
        trace.write_text("{}\n")
        (tmp_path / "agent-deep.meta.json").write_text("[" * 60000 + "]" * 60000)
        assert read_subagent_sidecar(trace) is None

    @pytest.mark.parametrize(
        "obj",
        [
            {"agentType": "a", "description": "d"},  # no toolUseId
            {"agentType": "a"},
            {},
        ],
    )
    def test_missing_tool_use_id_keeps_the_sidecar(
        self, tmp_path: Path, obj: dict[str, str]
    ) -> None:
        """#701: a sidecar without ``toolUseId`` is a real shape (skill- and
        workflow-spawned agents), not a malformed one -- it parses with no edge
        and keeps its descriptive fields."""
        trace = tmp_path / "agent-partial.jsonl"
        trace.write_text("{}\n")
        (tmp_path / "agent-partial.meta.json").write_text(json.dumps(obj))
        sidecar = read_subagent_sidecar(trace)
        assert sidecar is not None
        assert sidecar.tool_use_id is None
        assert sidecar.agent_type == obj.get("agentType", "")


class TestSkillSpawnedSidecar:
    """#701: the live shape Claude Code writes for a skill-run background agent."""

    LIVE_SHAPE = {
        "agentType": "general-purpose",
        "isBuiltIn": True,
        "description": "/code-review xhigh 237",
        "name": "code-review",
        "spawnDepth": 1,
        "requestShape": "background",
        "requestNonInteractive": True,
        "finished": True,
    }

    def _read(self, tmp_path: Path, obj: object) -> SubagentSidecar | None:
        trace = tmp_path / "agent-skill.jsonl"
        trace.write_text("{}\n")
        (tmp_path / "agent-skill.meta.json").write_text(json.dumps(obj))
        return read_subagent_sidecar(trace)

    def test_live_shape_parses_with_name_and_type(self, tmp_path: Path) -> None:
        sidecar = self._read(tmp_path, self.LIVE_SHAPE)
        assert sidecar is not None
        assert sidecar.tool_use_id is None
        assert sidecar.agent_type == "general-purpose"
        assert sidecar.name == "code-review"
        assert sidecar.spawn_depth == 1
        assert sidecar.request_shape == "background"
        assert sidecar.is_fork is False

    def test_fork_and_parent_fields(self, tmp_path: Path) -> None:
        sidecar = self._read(
            tmp_path,
            {"agentType": "fork", "toolUseId": "toolu_F", "isFork": True,
             "spawnDepth": 2, "parentAgentId": "a1"},
        )
        assert sidecar is not None
        assert sidecar.tool_use_id == "toolu_F"
        assert sidecar.is_fork is True
        assert sidecar.spawn_depth == 2
        assert sidecar.parent_agent_id == "a1"

    @pytest.mark.parametrize(
        "bad",
        [
            {"toolUseId": 7},
            {"name": ["x"]},
            {"spawnDepth": "1"},
            {"spawnDepth": True},
            {"parentAgentId": {}},
            {"isFork": "yes"},
            {"requestShape": 3},
        ],
    )
    def test_malformed_optional_field_never_discards_the_sidecar(
        self, tmp_path: Path, bad: dict[str, object]
    ) -> None:
        sidecar = self._read(tmp_path, {"agentType": "keep", **bad})
        assert sidecar is not None
        assert sidecar.agent_type == "keep"
        assert sidecar.tool_use_id is None
        assert sidecar.name is None
        assert sidecar.spawn_depth is None
        assert sidecar.parent_agent_id is None
        assert sidecar.is_fork is False
        assert sidecar.request_shape is None


class TestDescriptiveFieldsNeverCostTheEdge:
    """A cosmetic field's shape must not discard a real parent edge."""

    @pytest.mark.parametrize(
        "obj",
        [
            {"toolUseId": "toolu_KEEP"},  # both descriptive fields absent
            {"toolUseId": "toolu_KEEP", "description": None},
            {"toolUseId": "toolu_KEEP", "agentType": None},
            {"toolUseId": "toolu_KEEP", "agentType": None, "description": None},
            {"toolUseId": "toolu_KEEP", "description": "d"},  # no agentType
        ],
    )
    def test_edge_survives_missing_or_null_descriptive_fields(
        self, tmp_path: Path, obj: dict[str, str | None]
    ) -> None:
        trace = tmp_path / "agent-edge.jsonl"
        trace.write_text("{}\n")
        (tmp_path / "agent-edge.meta.json").write_text(json.dumps(obj))
        meta = read_subagent_sidecar(trace)
        assert meta is not None, "a cosmetic field must not cost the edge"
        assert meta.tool_use_id == "toolu_KEEP"
        assert isinstance(meta.agent_type, str)
        assert isinstance(meta.description, str)

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
