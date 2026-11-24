"""Tests for the home-dir scrub + leak guard in scripts/generate_readme_screenshots.py (#652)."""

from __future__ import annotations

import importlib.util
import io
import sys
from pathlib import Path
from types import ModuleType

import pytest
from rich.console import Console

SCRIPT = (
    Path(__file__).resolve().parents[2] / "scripts" / "generate_readme_screenshots.py"
)
FAKE_HOME = Path("/home/alice")


@pytest.fixture
def script(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location("readme_screenshots", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "readme_screenshots", module)
    spec.loader.exec_module(module)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: FAKE_HOME))
    monkeypatch.setattr(module, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(module, "IMAGES_DIR", tmp_path)
    return module


def _console_printing(text: str) -> Console:
    console = Console(record=True, width=100, force_terminal=True, file=io.StringIO())
    console.print(text)
    return console


class TestScrubHome:
    def test_replaces_home_prefix_in_nested_strings(self, script: ModuleType) -> None:
        data = {
            "recommendations": [
                {"message": "Agent 'pm' is defined in /home/alice/proj/.claude/agents/pm.md"},
            ],
            "nested": {"paths": ["/home/alice/a", "/home/alice/b"]},
        }
        assert script._scrub_home(data) == {
            "recommendations": [
                {"message": "Agent 'pm' is defined in ~/proj/.claude/agents/pm.md"},
            ],
            "nested": {"paths": ["~/a", "~/b"]},
        }

    def test_non_strings_and_other_paths_pass_through(self, script: ModuleType) -> None:
        data = {"count": 3, "cost": 1.5, "flag": True, "none": None, "p": "/opt/x"}
        assert script._scrub_home(data) == data

    def test_does_not_mutate_input(self, script: ModuleType) -> None:
        data = {"m": ["/home/alice/x"]}
        script._scrub_home(data)
        assert data == {"m": ["/home/alice/x"]}


class TestSaveLeakGuard:
    def test_refuses_svg_containing_home_path(
        self, script: ModuleType, tmp_path: Path,
    ) -> None:
        console = _console_printing("defined in /home/alice/proj/agents/pm.md")
        with pytest.raises(SystemExit, match="home-dir path"):
            script.save(console, "leak.svg", "t")
        assert not (tmp_path / "leak.svg").exists()

    def test_refuses_svg_containing_project_slug(
        self, script: ModuleType, tmp_path: Path,
    ) -> None:
        console = _console_printing("project -home-alice-proj")
        with pytest.raises(SystemExit, match="-home-alice"):
            script.save(console, "slug.svg", "t")
        assert not (tmp_path / "slug.svg").exists()

    def test_writes_clean_svg(self, script: ModuleType, tmp_path: Path) -> None:
        console = _console_printing("defined in ~/proj/agents/pm.md")
        script.save(console, "clean.svg", "t")
        assert (tmp_path / "clean.svg").exists()


class TestGenerateDiffScrubs:
    def test_both_sides_scrubbed_before_compute_diff(
        self, script: ModuleType, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        class _Result:
            def model_dump(self, mode: str) -> dict[str, object]:
                return {"message": "defined in /home/alice/proj/agents/pm.md"}

        captured: list[object] = []
        monkeypatch.setattr(script, "_analyze_with_diagnostics", lambda paths: _Result())
        monkeypatch.setattr(
            script, "compute_diff", lambda b, c: captured.extend([b, c]) or object(),
        )
        monkeypatch.setattr(script, "format_diff_table", lambda *a, **k: None)
        monkeypatch.setattr(script, "save", lambda *a, **k: None)

        script.generate_diff([Path("a.jsonl"), Path("b.jsonl")])

        scrubbed = {"message": "defined in ~/proj/agents/pm.md"}
        assert captured == [scrubbed, scrubbed]
