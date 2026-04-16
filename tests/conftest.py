"""Shared test fixtures for AgentFluent."""

from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture()
def fixtures_dir() -> Path:
    """Path to the test fixtures directory."""
    return FIXTURES_DIR


@pytest.fixture()
def basic_session_path() -> Path:
    """Path to a basic session JSONL file (user + assistant messages, both content formats)."""
    return FIXTURES_DIR / "session_basic.jsonl"


@pytest.fixture()
def agent_session_path() -> Path:
    """Path to a session with Agent tool_use blocks and tool_result metadata."""
    return FIXTURES_DIR / "session_with_agent.jsonl"


@pytest.fixture()
def tool_calls_session_path() -> Path:
    """Path to a session with regular tool calls (Read, Edit)."""
    return FIXTURES_DIR / "session_with_tool_calls.jsonl"


@pytest.fixture()
def skip_types_session_path() -> Path:
    """Path to a session with message types that should be skipped."""
    return FIXTURES_DIR / "session_skip_types.jsonl"


@pytest.fixture()
def malformed_session_path() -> Path:
    """Path to a session with a malformed JSON line."""
    return FIXTURES_DIR / "session_malformed.jsonl"


@pytest.fixture()
def streaming_dupes_session_path() -> Path:
    """Path to a session with duplicate streaming snapshot assistant messages."""
    return FIXTURES_DIR / "session_streaming_dupes.jsonl"


@pytest.fixture()
def empty_session_path(tmp_path: Path) -> Path:
    """Path to an empty JSONL file."""
    p = tmp_path / "empty.jsonl"
    p.touch()
    return p
