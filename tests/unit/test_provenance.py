"""Tests for ``core.provenance.is_machine_injected`` (#667)."""

from __future__ import annotations

from typing import Any

import pytest

from agentfluent.core.parser import _parse_user_message
from agentfluent.core.provenance import (
    HUMAN_ORIGIN_KINDS,
    HUMAN_PROMPT_SOURCES,
    INJECTED_TEXT_PREFIXES,
    is_machine_injected,
)
from agentfluent.core.session import ContentBlock, SessionMessage


def _user(text: str = "please fix it", **fields: Any) -> SessionMessage:
    return SessionMessage(
        type="user",
        content_blocks=[ContentBlock(type="text", text=text)],
        **fields,
    )


class TestStructuralProvenance:
    def test_is_meta_is_injected(self) -> None:
        assert is_machine_injected(_user(is_meta=True))

    def test_compact_summary_is_injected(self) -> None:
        assert is_machine_injected(_user(is_compact_summary=True))

    @pytest.mark.parametrize("source", sorted(HUMAN_PROMPT_SOURCES))
    def test_human_prompt_sources_are_not_injected(self, source: str) -> None:
        assert not is_machine_injected(_user(prompt_source=source))

    @pytest.mark.parametrize("source", ["sdk", "system"])
    def test_machine_prompt_sources_are_injected(self, source: str) -> None:
        assert is_machine_injected(_user(prompt_source=source))

    def test_unknown_prompt_source_is_injected(self) -> None:
        """Fail-safe: a value never seen before is not read as a person."""
        assert is_machine_injected(_user(prompt_source="some-future-source"))

    def test_human_origin_kind_is_not_injected(self) -> None:
        assert HUMAN_ORIGIN_KINDS == frozenset({"human"})
        assert not is_machine_injected(_user(origin_kind="human"))

    @pytest.mark.parametrize(
        "kind", ["peer", "task-notification", "auto-continuation", "future-kind"],
    )
    def test_non_human_origin_kinds_are_injected(self, kind: str) -> None:
        assert is_machine_injected(_user(origin_kind=kind))

    def test_typed_line_with_machine_origin_is_injected(self) -> None:
        """Each field is checked on its own; one machine value is enough."""
        assert is_machine_injected(_user(prompt_source="typed", origin_kind="peer"))

    def test_typed_human_line_is_not_injected(self) -> None:
        assert not is_machine_injected(
            _user(prompt_source="typed", origin_kind="human"),
        )

    def test_assistant_message_is_never_injected(self) -> None:
        msg = SessionMessage(
            type="assistant",
            content_blocks=[ContentBlock(type="text", text=INJECTED_TEXT_PREFIXES[0])],
        )
        assert not is_machine_injected(msg)


class TestLegacyTextMarkerFallback:
    @pytest.mark.parametrize("prefix", INJECTED_TEXT_PREFIXES)
    def test_marker_prefix_without_structural_fields_is_injected(
        self, prefix: str,
    ) -> None:
        assert is_machine_injected(_user(f"{prefix} revert it"))

    def test_marker_after_leading_whitespace_is_injected(self) -> None:
        assert is_machine_injected(_user("\n  [Subagent hand-back] report"))

    def test_marker_mid_message_is_not_injected(self) -> None:
        """Anchored: quoting a marker inside human prose is still human."""
        assert not is_machine_injected(
            _user("why did the [Subagent hand-back] say revert?"),
        )

    def test_structure_wins_over_text(self) -> None:
        """A human-typed line that starts with a marker stays human."""
        assert not is_machine_injected(
            _user("[Subagent hand-back] pasted by me", prompt_source="typed"),
        )

    def test_plain_legacy_line_is_not_injected(self) -> None:
        assert not is_machine_injected(_user("no, use the other file"))


class TestParserCapturesProvenance:
    def _parse(self, **top: Any) -> SessionMessage:
        return _parse_user_message(
            {"type": "user", "message": {"role": "user", "content": "hi"}, **top},
        )

    def test_fields_populated(self) -> None:
        msg = self._parse(
            isMeta=True,
            promptSource="system",
            origin={"kind": "peer", "from": "a1"},
            isCompactSummary=True,
        )
        assert msg.is_meta is True
        assert msg.prompt_source == "system"
        assert msg.origin_kind == "peer"
        assert msg.is_compact_summary is True

    def test_absent_fields_default(self) -> None:
        msg = self._parse()
        assert msg.is_meta is False
        assert msg.prompt_source is None
        assert msg.origin_kind is None
        assert msg.is_compact_summary is False

    @pytest.mark.parametrize(
        "top",
        [
            {"isMeta": "true"},
            {"isMeta": 1},
            {"isCompactSummary": "yes"},
            {"promptSource": 3},
            {"origin": "human"},
            {"origin": {"kind": 7}},
            {"origin": ["peer"]},
        ],
    )
    def test_malformed_values_are_neutral(self, top: dict[str, Any]) -> None:
        msg = self._parse(**top)
        assert msg.is_meta is False
        assert msg.is_compact_summary is False
        assert msg.prompt_source is None
        assert msg.origin_kind is None
        assert not is_machine_injected(msg)


class TestProvenanceStaysOutOfTheEnvelope:
    def test_analysis_json_carries_no_provenance_fields(self) -> None:
        """The new SessionMessage fields are internal: no serialized model
        embeds SessionMessage, so they never become public JSON API (D055)."""
        from pathlib import Path

        from agentfluent.analytics.pipeline import analyze_sessions

        fixture = (
            Path(__file__).parent.parent
            / "fixtures"
            / "session_injected_user_messages.jsonl"
        )
        dumped = analyze_sessions([fixture]).model_dump_json()
        for name in ("is_meta", "prompt_source", "origin_kind", "is_compact_summary"):
            assert f'"{name}"' not in dumped
