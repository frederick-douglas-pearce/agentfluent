"""Tests for #591 S1: `entrypoint` surfacing + sdk/cli/unknown classification.

Covers the classifier directly (in-memory `SessionMessage` lists) and
end-to-end via `parse_session` over fixtures for all three states (AC #6):

- sdk     -> `tests/fixtures/sdk_session/sdk-main-1.jsonl` (entrypoint "sdk-py")
- cli     -> `tests/fixtures/session_cli_entrypoint.jsonl` (entrypoint "cli")
- unknown -> `tests/fixtures/session_basic.jsonl` (no entrypoint)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentfluent.core.parser import parse_session
from agentfluent.core.session import (
    SessionMessage,
    classify_session,
    select_entrypoint,
)

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


class TestSelectEntrypoint:
    """#592: the raw-value selection that both published fields derive from."""

    def test_returns_none_when_no_entrypoint_present(self) -> None:
        assert select_entrypoint(_msgs(None, None)) is None
        assert select_entrypoint([]) is None

    def test_returns_the_verbatim_value(self) -> None:
        assert select_entrypoint(_msgs("sdk-py", "sdk-py")) == "sdk-py"
        assert select_entrypoint(_msgs("cli", "cli")) == "cli"

    def test_preserves_an_unrecognized_value(self) -> None:
        # The value is reported verbatim even though it classifies "unknown"
        # -- that pairing is the whole reason both fields are published.
        assert select_entrypoint(_msgs("emacs-shell")) == "emacs-shell"

    def test_sdk_wins_over_cli_on_a_mixed_session(self) -> None:
        assert select_entrypoint(_msgs("cli", "sdk-py")) == "sdk-py"

    def test_cli_wins_over_an_unrecognized_value(self) -> None:
        assert select_entrypoint(_msgs("emacs-shell", "cli")) == "cli"

    def test_selection_is_deterministic_across_orderings(self) -> None:
        assert select_entrypoint(_msgs("sdk-ts", "sdk-py")) == "sdk-py"
        assert select_entrypoint(_msgs("sdk-py", "sdk-ts")) == "sdk-py"

    def test_mixed_session_is_logged_as_an_anomaly(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level("WARNING"):
            select_entrypoint(_msgs("cli", "sdk-py"))
        assert "mixes multiple entrypoint values" in caplog.text

    def test_homogeneous_session_logs_nothing(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level("WARNING"):
            select_entrypoint(_msgs("cli", "cli"))
        assert caplog.text == ""


class TestFieldsCannotContradict:
    """#592 [architect, blocking]: the two published fields are coupled.

    ``classify_session`` is a thin function over ``select_entrypoint``, so a
    ``session_kind`` that disagrees with the reported raw ``entrypoint``
    (e.g. kind "sdk" beside entrypoint "cli") is unrepresentable. Locks that
    in against a refactor that re-derives either side independently.
    """

    @pytest.mark.parametrize(
        "entrypoints",
        [
            ("sdk-py", "sdk-py"),
            ("cli", "cli"),
            ("cli", "sdk-py"),
            ("emacs-shell",),
            ("emacs-shell", "cli"),
            (None, None),
            (None, "sdk-py"),
        ],
    )
    def test_kind_always_classifies_the_selected_entrypoint(
        self, entrypoints: tuple[str | None, ...]
    ) -> None:
        msgs = _msgs(*entrypoints)
        raw = select_entrypoint(msgs)
        kind = classify_session(msgs)

        if raw is None:
            assert kind == "unknown"
        elif raw.startswith("sdk"):
            assert kind == "sdk"
        elif raw == "cli":
            assert kind == "cli"
        else:
            assert kind == "unknown"
