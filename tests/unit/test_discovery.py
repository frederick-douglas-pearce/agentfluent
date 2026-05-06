"""Tests for project and session discovery."""

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from agentfluent.core import discovery as discovery_mod
from agentfluent.core.discovery import (
    discover_projects,
    discover_sessions,
    find_project,
    slug_to_display_name,
)
from tests._builders import user_message

WriteJSONL = Callable[[str, list[dict[str, Any]]], Path]

# Anchor timestamp + parsed equivalent shared across the
# first-message-timestamp tests so the test intent ("first ts → field
# value") is not buried in JSONL string noise.
_FIRST_TS_ISO = "2026-04-10T10:00:00.000Z"
_FIRST_TS = datetime(2026, 4, 10, 10, 0, 0, tzinfo=UTC)


class TestSlugToDisplayName:
    def test_standard_path(self) -> None:
        slug = "-home-fdpearce-Documents-Projects-git-codefluent"
        assert slug_to_display_name(slug) == "codefluent"

    def test_short_path(self) -> None:
        assert slug_to_display_name("-home-user-myproject") == "myproject"

    def test_single_segment(self) -> None:
        assert slug_to_display_name("-myproject") == "myproject"

    def test_empty_string(self) -> None:
        assert slug_to_display_name("") == ""


class TestDiscoverSessions:
    def test_finds_jsonl_files(self, tmp_path: Path) -> None:
        (tmp_path / "session-1.jsonl").write_text('{"type": "user"}\n')
        (tmp_path / "session-2.jsonl").write_text('{"type": "user"}\n')
        (tmp_path / "not-a-session.txt").write_text("ignore me")

        sessions = discover_sessions(tmp_path)
        assert len(sessions) == 2
        assert all(s.filename.endswith(".jsonl") for s in sessions)

    def test_sorted_newest_first(self, tmp_path: Path) -> None:
        import time

        (tmp_path / "old.jsonl").write_text("old")
        time.sleep(0.05)
        (tmp_path / "new.jsonl").write_text("new")

        sessions = discover_sessions(tmp_path)
        assert sessions[0].filename == "new.jsonl"
        assert sessions[1].filename == "old.jsonl"

    def test_captures_metadata(self, tmp_path: Path) -> None:
        content = '{"type": "user"}\n{"type": "assistant"}\n'
        (tmp_path / "test.jsonl").write_text(content)

        sessions = discover_sessions(tmp_path)
        assert len(sessions) == 1
        assert sessions[0].size_bytes == len(content)
        assert sessions[0].modified is not None

    def test_counts_subagent_files(self, tmp_path: Path) -> None:
        (tmp_path / "session-abc.jsonl").write_text('{"type": "user"}\n')
        subagents_dir = tmp_path / "session-abc" / "subagents"
        subagents_dir.mkdir(parents=True)
        (subagents_dir / "agent-001.jsonl").write_text('{"type": "user"}\n')
        (subagents_dir / "agent-002.jsonl").write_text('{"type": "user"}\n')
        (subagents_dir / "not-jsonl.txt").write_text("ignore")

        sessions = discover_sessions(tmp_path)
        assert len(sessions) == 1
        assert sessions[0].subagent_count == 2

    def test_no_subagent_dir(self, tmp_path: Path) -> None:
        (tmp_path / "session-abc.jsonl").write_text('{"type": "user"}\n')

        sessions = discover_sessions(tmp_path)
        assert sessions[0].subagent_count == 0

    def test_empty_directory(self, tmp_path: Path) -> None:
        sessions = discover_sessions(tmp_path)
        assert sessions == []

    def test_nonexistent_directory(self, tmp_path: Path) -> None:
        sessions = discover_sessions(tmp_path / "does-not-exist")
        assert sessions == []


class TestFirstMessageTimestamp:
    """``SessionInfo.first_message_timestamp`` -- foundation of the
    v0.6 date-range filter. Reads the first analytical message's
    timestamp in file order so it survives mtime churn from cloud sync
    or backup restores."""

    def test_populated_from_first_message(
        self, tmp_path: Path, write_jsonl: WriteJSONL,
    ) -> None:
        write_jsonl(
            "session.jsonl",
            [
                user_message(content="hello", timestamp=_FIRST_TS_ISO),
                user_message(
                    content="follow up",
                    timestamp="2026-04-10T10:00:05.000Z",
                ),
            ],
        )
        sessions = discover_sessions(tmp_path)
        assert sessions[0].first_message_timestamp == _FIRST_TS

    def test_none_for_empty_file(self, tmp_path: Path) -> None:
        (tmp_path / "empty.jsonl").write_text("")
        sessions = discover_sessions(tmp_path)
        assert sessions[0].first_message_timestamp is None

    def test_none_when_no_parseable_timestamps(
        self, tmp_path: Path, write_jsonl: WriteJSONL,
    ) -> None:
        write_jsonl("no-ts.jsonl", [user_message(content="x", timestamp=None)])
        sessions = discover_sessions(tmp_path)
        assert sessions[0].first_message_timestamp is None

    def test_skips_until_analytical_message(
        self, tmp_path: Path, write_jsonl: WriteJSONL,
    ) -> None:
        """Non-analytical types (file-history-snapshot, system, progress)
        are filtered by ``iter_raw_messages``; the helper picks up the
        first analytical message after them."""
        write_jsonl(
            "skip-then-real.jsonl",
            [
                {
                    "type": "file-history-snapshot",
                    "timestamp": "2026-04-09T00:00:00.000Z",
                },
                {
                    "type": "system",
                    "timestamp": "2026-04-09T01:00:00.000Z",
                },
                user_message(content="hello", timestamp=_FIRST_TS_ISO),
            ],
        )
        sessions = discover_sessions(tmp_path)
        assert sessions[0].first_message_timestamp == _FIRST_TS

    def test_none_when_only_malformed_lines(self, tmp_path: Path) -> None:
        (tmp_path / "malformed.jsonl").write_text(
            "this is not json\n{also not json\n",
        )
        sessions = discover_sessions(tmp_path)
        assert sessions[0].first_message_timestamp is None

    def test_oserror_on_one_file_does_not_abort_discovery(
        self,
        tmp_path: Path,
        write_jsonl: WriteJSONL,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A single corrupted/locked file must not prevent discovery
        of the other sessions in the project. Cloud-synced directories
        may have partially-written or permission-restricted files;
        chmod-based tests are platform-fragile, so we stub the I/O
        layer to raise.

        ``bad.jsonl`` is written as VALID JSONL with a parseable
        timestamp -- if the OSError catch were removed, the assertion
        ``bad_info.first_message_timestamp is None`` would fail because
        the file would parse successfully. That makes the monkeypatch
        load-bearing."""
        write_jsonl(
            "good.jsonl",
            [user_message(content="hi", timestamp=_FIRST_TS_ISO)],
        )
        bad = write_jsonl(
            "bad.jsonl",
            [user_message(content="hi", timestamp="2026-04-11T10:00:00.000Z")],
        )

        original = discovery_mod.iter_raw_messages

        def _maybe_raise(path: Path):  # type: ignore[no-untyped-def]
            if path == bad:
                raise PermissionError("simulated lock")
            yield from original(path)

        monkeypatch.setattr(discovery_mod, "iter_raw_messages", _maybe_raise)

        sessions = discover_sessions(tmp_path)

        assert {s.filename for s in sessions} == {"good.jsonl", "bad.jsonl"}
        bad_info = next(s for s in sessions if s.filename == "bad.jsonl")
        good_info = next(s for s in sessions if s.filename == "good.jsonl")
        assert bad_info.first_message_timestamp is None
        assert good_info.first_message_timestamp == _FIRST_TS


class TestDiscoverProjects:
    def test_finds_project_directories(self, tmp_path: Path) -> None:
        proj1 = tmp_path / "-home-user-project-alpha"
        proj1.mkdir()
        (proj1 / "session-1.jsonl").write_text('{"type": "user"}\n')

        proj2 = tmp_path / "-home-user-project-beta"
        proj2.mkdir()

        projects = discover_projects(tmp_path)
        assert len(projects) == 2

        names = {p.display_name for p in projects}
        assert names == {"alpha", "beta"}

    def test_project_metadata(self, tmp_path: Path) -> None:
        proj = tmp_path / "-home-user-myproject"
        proj.mkdir()
        (proj / "s1.jsonl").write_text('{"type": "user"}\n')
        (proj / "s2.jsonl").write_text('{"type": "user"}\n')

        projects = discover_projects(tmp_path)
        assert len(projects) == 1
        assert projects[0].session_count == 2
        assert projects[0].total_size_bytes > 0
        assert projects[0].earliest_session is not None
        assert projects[0].latest_session is not None

    def test_empty_project_included(self, tmp_path: Path) -> None:
        (tmp_path / "-home-user-empty").mkdir()

        projects = discover_projects(tmp_path)
        assert len(projects) == 1
        assert projects[0].session_count == 0
        assert projects[0].earliest_session is None

    def test_skips_hidden_dirs(self, tmp_path: Path) -> None:
        (tmp_path / ".hidden").mkdir()
        (tmp_path / "-home-user-visible").mkdir()

        projects = discover_projects(tmp_path)
        assert len(projects) == 1
        assert projects[0].display_name == "visible"

    def test_nonexistent_base_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match="Projects directory not found"):
            discover_projects(tmp_path / "nope")

    def test_skips_files_in_base(self, tmp_path: Path) -> None:
        (tmp_path / "stray-file.txt").write_text("not a project")
        (tmp_path / "-home-user-real").mkdir()

        projects = discover_projects(tmp_path)
        assert len(projects) == 1


class TestFindProject:
    def test_find_by_slug(self, tmp_path: Path) -> None:
        slug = "-home-user-myproject"
        (tmp_path / slug).mkdir()

        result = find_project(slug, tmp_path)
        assert result is not None
        assert result.slug == slug

    def test_find_by_display_name(self, tmp_path: Path) -> None:
        (tmp_path / "-home-user-myproject").mkdir()

        result = find_project("myproject", tmp_path)
        assert result is not None
        assert result.display_name == "myproject"

    def test_find_case_insensitive(self, tmp_path: Path) -> None:
        (tmp_path / "-home-user-MyProject").mkdir()

        result = find_project("myproject", tmp_path)
        assert result is not None

    def test_not_found(self, tmp_path: Path) -> None:
        (tmp_path / "-home-user-other").mkdir()

        result = find_project("nonexistent", tmp_path)
        assert result is None
