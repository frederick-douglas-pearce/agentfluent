"""Tests for the home-dir scrub + leak guard in scripts/generate_readme_screenshots.py (#652)."""

from __future__ import annotations

import importlib.util
import io
import sys
from pathlib import Path
from types import ModuleType

import pytest
from rich.console import Console
from rich.table import Table

SCRIPT = (
    Path(__file__).resolve().parents[2] / "scripts" / "generate_readme_screenshots.py"
)


def _set_home(monkeypatch: pytest.MonkeyPatch, home: str) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path(home)))


@pytest.fixture
def script(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location("readme_screenshots", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "readme_screenshots", module)
    spec.loader.exec_module(module)
    _set_home(monkeypatch, "/home/alice")
    monkeypatch.setattr(module, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(module, "IMAGES_DIR", tmp_path)
    return module


def _console_printing(*renderables: object) -> Console:
    console = Console(record=True, width=100, force_terminal=True, file=io.StringIO())
    for renderable in renderables:
        console.print(renderable)
    return console


class TestScrubHome:
    def test_replaces_home_prefix_in_nested_strings(self, script: ModuleType) -> None:
        data = {
            "recommendations": [
                {"message": "Agent 'pm' is defined in /home/alice/proj/.claude/agents/pm.md"},
            ],
            "nested": {"paths": ["/home/alice/a", "/home/alice"]},
        }
        assert script._scrub_home(data) == {
            "recommendations": [
                {"message": "Agent 'pm' is defined in ~/proj/.claude/agents/pm.md"},
            ],
            "nested": {"paths": ["~/a", "~"]},
        }

    def test_sibling_prefix_path_left_alone(
        self, script: ModuleType, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _set_home(monkeypatch, "/home/al")
        assert script._scrub_home("/home/alice/x and /home/al/y") == (
            "/home/alice/x and ~/y"
        )

    def test_root_home_scrubs_nothing(
        self, script: ModuleType, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _set_home(monkeypatch, "/")
        assert script._scrub_home("/opt/x/y") == "/opt/x/y"
        assert script._scrub_home("a/ b and x/") == "a/ b and x/"

    def test_non_strings_and_other_paths_pass_through(self, script: ModuleType) -> None:
        data = {"count": 3, "cost": 1.5, "flag": True, "none": None, "p": "/opt/x"}
        assert script._scrub_home(data) == data

    def test_does_not_mutate_input(self, script: ModuleType) -> None:
        data = {"m": ["/home/alice/x"]}
        script._scrub_home(data)
        assert data == {"m": ["/home/alice/x"]}


class TestSaveLeakGuard:
    @pytest.mark.parametrize(
        "text",
        [
            "defined in /home/alice/proj/agents/pm.md",
            "project -home-alice-proj",
            "defined in /Users/bob/x",
        ],
    )
    def test_refuses_home_paths(
        self, script: ModuleType, tmp_path: Path, text: str,
    ) -> None:
        with pytest.raises(SystemExit, match="home-dir path"):
            script.save(_console_printing(text), "leak.svg", "t")
        assert not (tmp_path / "leak.svg").exists()

    def test_refuses_truncated_path(self, script: ModuleType) -> None:
        table = Table(width=24)
        table.add_column("Message", no_wrap=True)
        table.add_row("/home/averylongusername/proj/agents/pm.md")
        console = _console_printing(table)
        assert "…" in console.export_text(clear=False)
        with pytest.raises(SystemExit, match="/home/a"):
            script.save(console, "trunc.svg", "t")

    def test_refuses_style_split_path(self, script: ModuleType) -> None:
        console = _console_printing("defined in /home/[red]alice[/]/x")
        with pytest.raises(SystemExit, match="/home/alice"):
            script.save(console, "split.svg", "t")

    def test_refuses_home_with_space(
        self, script: ModuleType, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _set_home(monkeypatch, "/opt/John Smith")
        with pytest.raises(SystemExit, match="/opt/John Smith"):
            script.save(_console_printing("in /opt/John Smith/x"), "space.svg", "t")

    def test_refusal_leaves_existing_file_untouched(
        self, script: ModuleType, tmp_path: Path,
    ) -> None:
        existing = tmp_path / "keep.svg"
        existing.write_text("committed", encoding="utf-8")
        with pytest.raises(SystemExit):
            script.save(_console_printing("in /home/alice/x"), "keep.svg", "t")
        assert existing.read_text(encoding="utf-8") == "committed"

    def test_root_home_does_not_refuse_ordinary_paths(
        self, script: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        _set_home(monkeypatch, "/")
        script.save(_console_printing("see a/b and /opt/x"), "root.svg", "t")
        assert (tmp_path / "root.svg").exists()

    def test_writes_clean_svg(self, script: ModuleType, tmp_path: Path) -> None:
        script.save(_console_printing("defined in ~/proj/agents/pm.md"), "clean.svg", "t")
        svg = (tmp_path / "clean.svg").read_text(encoding="utf-8")
        assert svg.startswith("<svg") and "pm.md" in svg


class TestGenerateDiffScrubs:
    def test_each_side_scrubbed_from_its_own_slice(
        self, script: ModuleType, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        class _Result:
            def __init__(self, n: int) -> None:
                self.n = n

            def model_dump(self, mode: str) -> dict[str, object]:
                return {"message": f"defined in /home/alice/n{self.n}"}

        captured: list[object] = []
        monkeypatch.setattr(
            script, "_analyze_with_diagnostics", lambda paths: _Result(len(paths)),
        )
        monkeypatch.setattr(
            script, "compute_diff", lambda b, c: captured.extend([b, c]) or object(),
        )
        monkeypatch.setattr(script, "format_diff_table", lambda *a, **k: None)
        monkeypatch.setattr(script, "save", lambda *a, **k: None)

        script.generate_diff([Path(f"{i}.jsonl") for i in range(4)])

        assert captured == [
            {"message": "defined in ~/n2"},
            {"message": "defined in ~/n4"},
        ]
