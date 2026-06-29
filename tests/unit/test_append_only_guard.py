"""Tests for the append-only guard hook (#500).

Covers ``.claude/hooks/guard_append_only.py``: the subset-of-IDs detection
(drop, append, body-edit, count-preserving swap), the anchored ID regex
(prose mentions are not entries), suffix-based file matching, the fail
directions (FileNotFoundError -> allow, other read errors -> deny, malformed
event -> deny), and benign passes.

The hook lives in ``.claude/hooks/`` (maintainer-only Claude Code tooling,
outside the ``agentfluent`` package), so it is loaded here by file path via
importlib rather than imported as a module.
"""

from __future__ import annotations

import importlib.util
import io
import json
import re
from pathlib import Path
from types import ModuleType

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_HOOK_PATH = _REPO_ROOT / ".claude" / "hooks" / "guard_append_only.py"
_REAL_DECISIONS = _REPO_ROOT / ".claude" / "specs" / "decisions.md"


def _load_hook() -> ModuleType:
    spec = importlib.util.spec_from_file_location("guard_append_only", _HOOK_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


guard = _load_hook()

REGISTERED_SUFFIX = ".claude/specs/decisions.md"


def _entry(num: str, body: str = "Some decision body.") -> str:
    return f"## D{num}: a decision\n\n{body}\n"


def _doc(*nums: str, body: str = "Some decision body.") -> str:
    return "\n".join(_entry(n, body) for n in nums)


# --- evaluate(): pure detection logic -------------------------------------


def test_pure_append_is_allowed() -> None:
    existing = _doc("001", "002")
    proposed = _doc("001", "002", "003")
    blocked, _ = guard.evaluate(existing, proposed, guard.DECISION_ID_PATTERN)
    assert blocked is False


def test_dropping_an_entry_is_blocked() -> None:
    existing = _doc("001", "002")
    proposed = _doc("001")
    blocked, reason = guard.evaluate(existing, proposed, guard.DECISION_ID_PATTERN)
    assert blocked is True
    assert "D002" in reason


def test_editing_existing_body_is_allowed() -> None:
    existing = _doc("001", "002", body="Original rationale.")
    proposed = _doc("001", "002", body="Rewritten, clearer rationale.")
    blocked, _ = guard.evaluate(existing, proposed, guard.DECISION_ID_PATTERN)
    assert blocked is False


def test_count_preserving_id_swap_is_blocked() -> None:
    existing = _doc("001", "002", "003")
    proposed = _doc("001", "002", "099")  # same count, D003 silently dropped
    blocked, reason = guard.evaluate(existing, proposed, guard.DECISION_ID_PATTERN)
    assert blocked is True
    assert "D003" in reason


def test_empty_proposed_content_is_blocked() -> None:
    existing = _doc("001", "002")
    blocked, reason = guard.evaluate(existing, "", guard.DECISION_ID_PATTERN)
    assert blocked is True
    assert "D001" in reason and "D002" in reason


def test_existing_with_no_ids_is_allowed() -> None:
    existing = "# Decision log\n\nNo entries yet.\n"
    blocked, _ = guard.evaluate(existing, "anything", guard.DECISION_ID_PATTERN)
    assert blocked is False


def test_suffixed_id_is_distinct_from_numeric_sibling() -> None:
    # Regression: `## D038-A:` must not collapse onto `## D038:`. Dropping the
    # suffixed entry while keeping the numeric one must be detected.
    existing = _doc("038", "038-A")
    proposed = _doc("038")  # D038-A silently dropped
    blocked, reason = guard.evaluate(existing, proposed, guard.DECISION_ID_PATTERN)
    assert blocked is True
    assert "D038-A" in reason


def test_suffixed_and_numeric_ids_both_extracted() -> None:
    ids = guard.extract_ids(_doc("038", "038-A"), guard.DECISION_ID_PATTERN)
    assert ids == {"D038", "D038-A"}


# --- extract_ids(): anchored regex ----------------------------------------


def test_prose_mentions_are_not_counted_as_entries() -> None:
    text = (
        "## D001: real entry\n\nThis decision supersedes D012 and relates to "
        "D999 mentioned inline.\n"
    )
    ids = guard.extract_ids(text, guard.DECISION_ID_PATTERN)
    assert ids == {"D001"}


# --- match_registered_file(): suffix matching -----------------------------


def test_registered_suffix_matches() -> None:
    path = f"/home/u/project/{REGISTERED_SUFFIX}"
    assert guard.match_registered_file(path) is guard.DECISION_ID_PATTERN


def test_unrelated_decisions_md_does_not_match() -> None:
    assert guard.match_registered_file("/home/u/notes/decisions.md") is None


def test_empty_path_does_not_match() -> None:
    assert guard.match_registered_file("") is None


def test_relative_registered_path_matches() -> None:
    assert guard.match_registered_file(REGISTERED_SUFFIX) is guard.DECISION_ID_PATTERN


def test_suffix_requires_path_boundary() -> None:
    # Regression: a tail that merely ends in the suffix STRING, without a `/`
    # boundary before `.claude`, must NOT be treated as the protected log.
    assert guard.match_registered_file(f"/repo/vendor{REGISTERED_SUFFIX}") is None


# --- check(): event-level behavior + I/O ----------------------------------


def _write_event(path: str, content: str) -> dict:
    return {"tool_name": "Write", "tool_input": {"file_path": path, "content": content}}


def _registered_path(tmp_path: Path) -> Path:
    return tmp_path / ".claude" / "specs" / "decisions.md"


def test_check_ignores_non_write_tools() -> None:
    event = {"tool_name": "Edit", "tool_input": {"file_path": "x/decisions.md"}}
    assert guard.check(event) == (False, "")


def test_check_ignores_unregistered_files(tmp_path: Path) -> None:
    target = tmp_path / "decisions.md"
    target.write_text(_doc("001"), encoding="utf-8")
    blocked, _ = guard.check(_write_event(str(target), ""))
    assert blocked is False


def test_check_allows_new_file(tmp_path: Path) -> None:
    target = _registered_path(tmp_path)  # parent dirs absent; file does not exist
    blocked, _ = guard.check(_write_event(str(target), _doc("001")))
    assert blocked is False


def test_check_blocks_dropping_entries_on_existing_file(tmp_path: Path) -> None:
    target = _registered_path(tmp_path)
    target.parent.mkdir(parents=True)
    target.write_text(_doc("001", "002", "003"), encoding="utf-8")
    blocked, reason = guard.check(_write_event(str(target), _doc("001")))
    assert blocked is True
    assert "D002" in reason and "D003" in reason


def test_check_allows_valid_append_on_existing_file(tmp_path: Path) -> None:
    target = _registered_path(tmp_path)
    target.parent.mkdir(parents=True)
    target.write_text(_doc("001", "002"), encoding="utf-8")
    blocked, _ = guard.check(_write_event(str(target), _doc("001", "002", "003")))
    assert blocked is False


def test_check_fails_closed_on_read_error(tmp_path: Path) -> None:
    # A directory at the target path makes read_text raise IsADirectoryError
    # (an OSError that is not FileNotFoundError) -> deny.
    target = _registered_path(tmp_path)
    target.mkdir(parents=True)
    blocked, reason = guard.check(_write_event(str(target), _doc("001")))
    assert blocked is True
    assert "could not read" in reason


# --- main(): stdin parsing + decision emission ----------------------------


def test_main_fails_closed_on_malformed_event(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("not json"))
    assert guard.main() == 2


def test_main_emits_deny_decision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    target = _registered_path(tmp_path)
    target.parent.mkdir(parents=True)
    target.write_text(_doc("001", "002"), encoding="utf-8")
    event = _write_event(str(target), _doc("001"))
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(event)))

    rc = guard.main()
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_main_allows_valid_append(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    target = _registered_path(tmp_path)
    target.parent.mkdir(parents=True)
    target.write_text(_doc("001"), encoding="utf-8")
    event = _write_event(str(target), _doc("001", "002"))
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(event)))

    rc = guard.main()
    assert rc == 0
    assert capsys.readouterr().out == ""  # no decision emitted == allow


# --- drift guard against the real decisions.md ----------------------------


def test_pattern_tracks_the_real_decision_log() -> None:
    # Couples the guard to the live file: if decisions.md's heading style ever
    # drifts away from `## Dxxx`, the pattern would silently stop matching and
    # the guard would degrade to a no-op (the #500 hazard, reintroduced
    # quietly). This test turns that silent failure into a red build.
    assert _REAL_DECISIONS.exists(), "real decisions.md not found at expected path"
    text = _REAL_DECISIONS.read_text(encoding="utf-8")

    ids = list(guard.DECISION_ID_PATTERN.findall(text))
    heading_lines = re.findall(r"^##\s+D\d", text, re.MULTILINE)

    assert ids, "pattern captured no decision IDs from the real log"
    # Every decision-shaped heading is captured exactly once (no drift, no
    # collision such as D038-A collapsing onto D038).
    assert len(ids) == len(heading_lines)
    assert len(ids) == len(set(ids))
    # The known suffixed/numeric sibling pair stays distinct.
    assert {"D038", "D038-A"} <= set(ids)
