"""Shared test fixtures for AgentFluent."""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture()
def write_jsonl(tmp_path: Path) -> Callable[[str, list[dict[str, Any]]], Path]:
    """Return a helper that writes a list of dicts as a JSONL file under tmp_path.

    Used by parser tests that want to construct session/subagent trace
    content inline rather than maintain on-disk fixtures.
    """

    def _write(filename: str, lines: list[dict[str, Any]]) -> Path:
        path = tmp_path / filename
        with path.open("w") as f:
            for line in lines:
                f.write(json.dumps(line) + "\n")
        return path

    return _write


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
def block_per_line_session_path() -> Path:
    """Path to a session where one assistant message's content blocks are
    split across multiple JSONL lines sharing the same `message_id` and
    `output_tokens` — the shape current Claude Code emits. See #153."""
    return FIXTURES_DIR / "session_block_per_line.jsonl"


@pytest.fixture()
def empty_session_path(tmp_path: Path) -> Path:
    """Path to an empty JSONL file."""
    p = tmp_path / "empty.jsonl"
    p.touch()
    return p


# Subagent trace fixtures — realistic JSONL files under tests/fixtures/subagents/.
# The filenames match AGENT_FILENAME_PATTERN so parse_subagent_trace can load them
# directly without a filename-override helper.

SUBAGENT_FIXTURES_DIR = FIXTURES_DIR / "subagents"


@pytest.fixture()
def subagent_basic_path() -> Path:
    """Happy-path trace: 3 successful tool calls (Glob, Grep, Read)."""
    return SUBAGENT_FIXTURES_DIR / "agent-basic.jsonl"


@pytest.fixture()
def subagent_errors_path() -> Path:
    """Trace with a Write call blocked by a hook (is_error=True)."""
    return SUBAGENT_FIXTURES_DIR / "agent-errors.jsonl"


@pytest.fixture()
def subagent_retry_path() -> Path:
    """Trace with 3 consecutive identical Bash chmod retries, all failing."""
    return SUBAGENT_FIXTURES_DIR / "agent-retry.jsonl"


@pytest.fixture()
def subagent_stuck_path() -> Path:
    """Trace with 5 identical Read calls on a non-existent file (stuck pattern)."""
    return SUBAGENT_FIXTURES_DIR / "agent-stuck.jsonl"


@pytest.fixture()
def subagent_empty_path() -> Path:
    """Empty subagent trace file."""
    return SUBAGENT_FIXTURES_DIR / "agent-empty.jsonl"


@pytest.fixture()
def subagent_malformed_path() -> Path:
    """Trace with malformed JSON lines interspersed with valid ones."""
    return SUBAGENT_FIXTURES_DIR / "agent-malformed.jsonl"


@pytest.fixture()
def subagent_large_path() -> Path:
    """Trace with 22+ tool calls across Read / Grep / Glob / Bash."""
    return SUBAGENT_FIXTURES_DIR / "agent-large.jsonl"


@pytest.fixture()
def subagent_streaming_dupes_path() -> Path:
    """Trace with duplicate streaming-snapshot assistant messages (same message_id)."""
    return SUBAGENT_FIXTURES_DIR / "agent-streaming-dupes.jsonl"
