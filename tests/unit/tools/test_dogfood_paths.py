"""Tests for the dogfood-runner snapshot pathing (tools/dogfood_runner/paths.py)."""

from __future__ import annotations

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
