"""Tests for the dogfood-runner snapshot pathing (tools/dogfood_runner/paths.py)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.dogfood_runner import paths


def test_state_dir_honors_xdg_state_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(paths.XDG_STATE_HOME_ENV_VAR, str(tmp_path / "state"))
    assert paths.dogfood_state_dir() == tmp_path / "state" / "agentfluent" / "dogfood"


def test_state_dir_falls_back_to_local_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(paths.XDG_STATE_HOME_ENV_VAR, raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path("/home/tester")))
    assert paths.dogfood_state_dir() == Path("/home/tester/.local/state/agentfluent/dogfood")


@pytest.mark.parametrize("bad", ["../escape", "/abs/path", "a/b", "we ird", ".."])
def test_slug_dir_rejects_unsafe_slug(bad: str, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unsafe project-slug"):
        paths.slug_dir(bad, root=tmp_path)


def test_new_snapshot_path_creates_dir_and_names_by_runstamp(tmp_path: Path) -> None:
    p = paths.new_snapshot_path("-home-user-proj", "20260101T000000Z", root=tmp_path)
    assert p.parent.is_dir()
    assert p.name == "snapshot-20260101T000000Z.json"


def test_new_snapshot_path_rejects_unsafe_runstamp(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unsafe runstamp"):
        paths.new_snapshot_path("slug", "../evil", root=tmp_path)


def test_latest_snapshot_none_when_empty(tmp_path: Path) -> None:
    assert paths.latest_snapshot("slug", root=tmp_path) is None


def test_latest_snapshot_returns_most_recent_by_lexical_sort(tmp_path: Path) -> None:
    slug = "slug"
    older = paths.new_snapshot_path(slug, "20260101T000000Z", root=tmp_path)
    older.write_text("{}")
    newer = paths.new_snapshot_path(slug, "20260102T000000Z", root=tmp_path)
    newer.write_text("{}")
    assert paths.latest_snapshot(slug, root=tmp_path) == newer


def test_prune_snapshots_keeps_n_most_recent(tmp_path: Path) -> None:
    slug = "slug"
    stamps = [f"2026010{i}T000000Z" for i in range(1, 6)]  # 5 snapshots
    for stamp in stamps:
        paths.new_snapshot_path(slug, stamp, root=tmp_path).write_text("{}")
    deleted = paths.prune_snapshots(slug, keep=2, root=tmp_path)
    remaining = sorted(p.name for p in paths.slug_dir(slug, root=tmp_path).iterdir())
    assert len(deleted) == 3
    assert remaining == ["snapshot-20260104T000000Z.json", "snapshot-20260105T000000Z.json"]


def test_prune_snapshots_never_wipes_below_one(tmp_path: Path) -> None:
    slug = "slug"
    paths.new_snapshot_path(slug, "20260101T000000Z", root=tmp_path).write_text("{}")
    # keep=0 must be coerced to 1 so the next run always has a diff baseline.
    deleted = paths.prune_snapshots(slug, keep=0, root=tmp_path)
    assert deleted == []
    assert paths.latest_snapshot(slug, root=tmp_path) is not None


def test_runs_manifest_path_at_state_root(tmp_path: Path) -> None:
    assert paths.runs_manifest_path(root=tmp_path) == tmp_path / "runs.jsonl"


def test_record_run_appends_one_json_line(tmp_path: Path) -> None:
    path = paths.record_run(
        runstamp="20260715T120000Z",
        main_model="claude-opus-4-8",
        subagent_model="claude-haiku-4-5",
        session_id="sess-1",
        session_jsonl="/corpus/sess-1.jsonl",
        root=tmp_path,
    )
    assert path == tmp_path / "runs.jsonl"
    record = json.loads(path.read_text().strip())
    assert record == {
        "runstamp": "20260715T120000Z",
        "main_model": "claude-opus-4-8",
        "subagent_model": "claude-haiku-4-5",
        "session_id": "sess-1",
        "session_jsonl": "/corpus/sess-1.jsonl",
    }


def test_record_run_appends_across_calls(tmp_path: Path) -> None:
    for i, model in enumerate(("claude-opus-4-8", "claude-sonnet-4-6")):
        paths.record_run(
            runstamp=f"2026071{i}T120000Z",
            main_model=model,
            subagent_model="claude-haiku-4-5",
            session_id=f"sess-{i}",
            session_jsonl=f"/corpus/sess-{i}.jsonl",
            root=tmp_path,
        )
    lines = (tmp_path / "runs.jsonl").read_text().splitlines()
    assert len(lines) == 2
    assert [json.loads(line)["main_model"] for line in lines] == [
        "claude-opus-4-8",
        "claude-sonnet-4-6",
    ]


def test_record_run_creates_state_root_if_absent(tmp_path: Path) -> None:
    root = tmp_path / "nonexistent" / "dogfood"
    paths.record_run(
        runstamp="20260715T120000Z",
        main_model="claude-haiku-4-5",
        subagent_model="claude-haiku-4-5",
        session_id="s",
        session_jsonl="/c/s.jsonl",
        root=root,
    )
    assert (root / "runs.jsonl").is_file()
