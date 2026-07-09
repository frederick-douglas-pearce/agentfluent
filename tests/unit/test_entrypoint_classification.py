"""Tests for #591 S1: `entrypoint` surfacing + sdk/cli/unknown classification.

Covers the classifier directly (in-memory `SessionMessage` lists) and
end-to-end via `parse_session` over fixtures for all three states (AC #6):

- sdk     -> `tests/fixtures/sdk_session/sdk-main-1.jsonl` (entrypoint "sdk-py")
- cli     -> `tests/fixtures/session_cli_entrypoint.jsonl` (entrypoint "cli")
- unknown -> `tests/fixtures/session_basic.jsonl` (no entrypoint)
"""

from __future__ import annotations

from pathlib import Path

from agentfluent.core.parser import parse_session
from agentfluent.core.session import SessionMessage, classify_session

_FIXTURES = Path(__file__).parent.parent / "fixtures"
_SDK_JSONL = _FIXTURES / "sdk_session" / "sdk-main-1.jsonl"
_SDK_CHILD_JSONL = (
    _FIXTURES / "sdk_session" / "sdk-main-1" / "subagents" / "agent-child0000001.jsonl"
)
_CLI_JSONL = _FIXTURES / "session_cli_entrypoint.jsonl"
_UNKNOWN_JSONL = _FIXTURES / "session_basic.jsonl"


def _msgs(*entrypoints: str | None) -> list[SessionMessage]:
    """Build a list of SessionMessages carrying the given entrypoints."""
    return [SessionMessage(type="user", entrypoint=e) for e in entrypoints]


class TestClassifySession:
    def test_sdk_py_classifies_as_sdk(self) -> None:
        assert classify_session(_msgs("sdk-py", "sdk-py")) == "sdk"

    def test_sdk_ts_classifies_as_sdk_forward_compat(self) -> None:
        # AC #4: startswith("sdk") means a future sdk-ts needs no TS probe.
        assert classify_session(_msgs("sdk-ts", "sdk-ts")) == "sdk"

    def test_cli_classifies_as_cli(self) -> None:
        assert classify_session(_msgs("cli", "cli")) == "cli"

    def test_missing_entrypoint_classifies_as_unknown(self) -> None:
        # AC #3: no exception, classifies unknown.
        assert classify_session(_msgs(None, None)) == "unknown"

    def test_empty_message_list_classifies_as_unknown(self) -> None:
        assert classify_session([]) == "unknown"

    def test_unrecognized_value_fails_safe_to_unknown(self) -> None:
        # Exact "cli" match => an unknown runtime is not mislabelled as CC.
        assert classify_session(_msgs("emacs-shell", "emacs-shell")) == "unknown"

    def test_mixed_sdk_and_cli_prefers_sdk(self) -> None:
        # [architect, important] sdk-before-cli precedence, order-independent.
        assert classify_session(_msgs("sdk-py", "cli")) == "sdk"

    def test_mixed_cli_and_sdk_prefers_sdk_reversed(self) -> None:
        assert classify_session(_msgs("cli", "sdk-py")) == "sdk"


class TestEntrypointSurfacedThroughParser:
    def test_sdk_fixture_surfaces_entrypoint_and_classifies_sdk(self) -> None:
        msgs = parse_session(_SDK_JSONL)
        assert msgs
        assert all(m.entrypoint == "sdk-py" for m in msgs)
        assert classify_session(msgs) == "sdk"

    def test_cli_fixture_surfaces_entrypoint_and_classifies_cli(self) -> None:
        msgs = parse_session(_CLI_JSONL)
        assert msgs
        assert all(m.entrypoint == "cli" for m in msgs)
        assert classify_session(msgs) == "cli"

    def test_unknown_fixture_has_no_entrypoint_and_classifies_unknown(self) -> None:
        msgs = parse_session(_UNKNOWN_JSONL)
        assert msgs
        assert all(m.entrypoint is None for m in msgs)
        assert classify_session(msgs) == "unknown"

    def test_entrypoint_flows_through_to_subagent_trace(self) -> None:
        # [architect] entrypoint reaches subagent traces for free via the
        # shared parse path (traces/parser delegates to parse_session).
        child = parse_session(_SDK_CHILD_JSONL)
        assert child
        assert all(m.entrypoint == "sdk-py" for m in child)
        assert classify_session(child) == "sdk"
