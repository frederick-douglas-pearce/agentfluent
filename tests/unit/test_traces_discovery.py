"""Tests for subagent trace file discovery."""

from __future__ import annotations

from pathlib import Path

from agentfluent.traces.discovery import (
    AGENT_FILENAME_PATTERN,
    SubagentFileInfo,
    discover_session_subagents,
    discover_subagent_files,
)


def _make_session_with_subagents(
    project_root: Path, session_id: str, agent_ids: list[str],
) -> Path:
    """Create a session dir with a subagents/ subdir containing agent-*.jsonl files."""
    session_dir = project_root / session_id
    (session_dir / "subagents").mkdir(parents=True)
    for agent_id in agent_ids:
        (session_dir / "subagents" / f"agent-{agent_id}.jsonl").write_text("{}\n")
    return session_dir


class TestAgentFilenamePattern:
    def test_matches_uuid_filename(self) -> None:
        m = AGENT_FILENAME_PATTERN.match("agent-abc-123.jsonl")
        assert m is not None
        assert m.group(1) == "abc-123"

    def test_rejects_non_agent_filename(self) -> None:
        assert AGENT_FILENAME_PATTERN.match("foo.jsonl") is None
        assert AGENT_FILENAME_PATTERN.match("agent.jsonl") is None
        assert AGENT_FILENAME_PATTERN.match("agent-abc.txt") is None

    def test_rejects_empty_agent_id(self) -> None:
        # `.+` in the pattern requires at least one character between
        # "agent-" and ".jsonl", so an empty UUID is rejected.
        assert AGENT_FILENAME_PATTERN.match("agent-.jsonl") is None

    def test_rejects_leading_dot(self) -> None:
        # The `^agent-` anchor keeps dotfiles out even if the rest
        # of the filename would otherwise match.
        assert AGENT_FILENAME_PATTERN.match(".agent-abc.jsonl") is None


class TestDiscoverSessionSubagents:
    def test_missing_subagents_dir_returns_empty(self, tmp_path: Path) -> None:
        session_dir = tmp_path / "session-1"
        session_dir.mkdir()
        assert discover_session_subagents(session_dir) == []

    def test_empty_subagents_dir_returns_empty(self, tmp_path: Path) -> None:
        session_dir = tmp_path / "session-1"
        (session_dir / "subagents").mkdir(parents=True)
        assert discover_session_subagents(session_dir) == []

    def test_returns_matching_files_sorted(self, tmp_path: Path) -> None:
        session_dir = _make_session_with_subagents(
            tmp_path, "s1", ["bbb", "aaa", "ccc"],
        )
        files = discover_session_subagents(session_dir)
        # Sorted by filename -> agent-aaa, agent-bbb, agent-ccc
        assert [f.agent_id for f in files] == ["aaa", "bbb", "ccc"]
        for f in files:
            assert f.path.parent.name == "subagents"

    def test_skips_non_matching_files(self, tmp_path: Path) -> None:
        session_dir = tmp_path / "s1"
        (session_dir / "subagents").mkdir(parents=True)
        (session_dir / "subagents" / "agent-keep.jsonl").write_text("{}\n")
        (session_dir / "subagents" / "readme.txt").write_text("hi")
        (session_dir / "subagents" / "other.jsonl").write_text("{}\n")
        (session_dir / "subagents" / "agent-bad.log").write_text("")

        files = discover_session_subagents(session_dir)
        assert [f.agent_id for f in files] == ["keep"]

    def test_skips_nested_directories(self, tmp_path: Path) -> None:
        session_dir = tmp_path / "s1"
        (session_dir / "subagents" / "agent-sub.jsonl").mkdir(parents=True)
        (session_dir / "subagents" / "agent-real.jsonl").write_text("{}\n")

        files = discover_session_subagents(session_dir)
        assert [f.agent_id for f in files] == ["real"]

    def test_skips_dotfiles_in_subagents_dir(self, tmp_path: Path) -> None:
        session_dir = tmp_path / "s1"
        (session_dir / "subagents").mkdir(parents=True)
        (session_dir / "subagents" / ".agent-hidden.jsonl").write_text("{}\n")
        (session_dir / "subagents" / ".DS_Store").write_text("")
        (session_dir / "subagents" / "agent-real.jsonl").write_text("{}\n")

        files = discover_session_subagents(session_dir)
        assert [f.agent_id for f in files] == ["real"]


class TestDiscoverSubagentFiles:
    def test_missing_project_path_returns_empty(self, tmp_path: Path) -> None:
        assert discover_subagent_files(tmp_path / "nope") == {}

    def test_project_is_file_returns_empty(self, tmp_path: Path) -> None:
        f = tmp_path / "file.txt"
        f.write_text("")
        assert discover_subagent_files(f) == {}

    def test_project_with_no_sessions_returns_empty(self, tmp_path: Path) -> None:
        assert discover_subagent_files(tmp_path) == {}

    def test_sessions_without_subagents_omitted(self, tmp_path: Path) -> None:
        # One session with subagents, one without.
        _make_session_with_subagents(tmp_path, "s1", ["a1"])
        (tmp_path / "s2").mkdir()

        result = discover_subagent_files(tmp_path)
        assert list(result.keys()) == ["s1"]

    def test_multiple_sessions_mapped(self, tmp_path: Path) -> None:
        _make_session_with_subagents(tmp_path, "s1", ["a1", "a2"])
        _make_session_with_subagents(tmp_path, "s2", ["b1"])

        result = discover_subagent_files(tmp_path)
        assert set(result.keys()) == {"s1", "s2"}
        assert [f.agent_id for f in result["s1"]] == ["a1", "a2"]
        assert [f.agent_id for f in result["s2"]] == ["b1"]

    def test_ignores_files_at_project_level(self, tmp_path: Path) -> None:
        # Session JSONL files themselves live alongside the session dirs;
        # they should not be mistaken for session directories.
        (tmp_path / "s1.jsonl").write_text("{}\n")
        _make_session_with_subagents(tmp_path, "s1", ["a1"])

        result = discover_subagent_files(tmp_path)
        assert list(result.keys()) == ["s1"]

    def test_subagent_file_info_shape(self, tmp_path: Path) -> None:
        _make_session_with_subagents(tmp_path, "s1", ["agent-uuid-123"])
        result = discover_subagent_files(tmp_path)
        [info] = result["s1"]
        assert isinstance(info, SubagentFileInfo)
        assert info.agent_id == "agent-uuid-123"
        assert info.path.name == "agent-agent-uuid-123.jsonl"
