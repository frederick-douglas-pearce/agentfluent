"""Smoke tests to verify test infrastructure works."""

from pathlib import Path

from agentfluent import __version__


def test_version() -> None:
    """Package version is set."""
    assert __version__ == "0.1.0"


def test_fixtures_exist(fixtures_dir: Path) -> None:
    """All expected fixture files are present."""
    expected = [
        "session_basic.jsonl",
        "session_with_agent.jsonl",
        "session_with_tool_calls.jsonl",
        "session_skip_types.jsonl",
        "session_malformed.jsonl",
    ]
    for name in expected:
        path = fixtures_dir / name
        assert path.exists(), f"Missing fixture: {name}"
        if name != "session_malformed.jsonl":
            assert path.stat().st_size > 0, f"Empty fixture: {name}"


def test_fixture_paths_resolve(
    basic_session_path: Path,
    agent_session_path: Path,
    tool_calls_session_path: Path,
    skip_types_session_path: Path,
    malformed_session_path: Path,
    empty_session_path: Path,
) -> None:
    """All conftest fixtures resolve to valid paths."""
    assert basic_session_path.exists()
    assert agent_session_path.exists()
    assert tool_calls_session_path.exists()
    assert skip_types_session_path.exists()
    assert malformed_session_path.exists()
    assert empty_session_path.exists()
    assert empty_session_path.stat().st_size == 0
