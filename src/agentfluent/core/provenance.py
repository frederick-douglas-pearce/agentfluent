"""Provenance of ``user``-role messages: typed by a human, or injected.

Claude Code and the Agent SDK write a lot of non-human content into
``type: "user"`` lines: skill bodies, command caveats, system reminders,
background-task notifications, cross-session relay and subagent
hand-backs, compaction summaries, and programmatic SDK prompts. Any
signal that means "a human said this" must exclude them first.

``is_machine_injected`` is that shared gate (#667). It reads the raw
provenance fields the parser captures on ``SessionMessage``
(``is_meta``, ``prompt_source``, ``origin_kind``, ``turn_origin``,
``is_compact_summary``) and decides in this order:

1. **Structural fields** (current formats). ``isMeta`` or
   ``isCompactSummary`` mark a line injected on their own.
   ``promptSource``, ``origin.kind`` and ``turnOrigin`` are checked
   against the values known to mean *human* -- anything else that is
   present, including a value this module has never seen, counts as
   injected. Several of these values are not documented upstream, so an
   unknown one must not be read as a person (fail-safe: an uncertain
   line does not feed a human-behavior signal).
2. **Text-marker fallback**, only when the line carries none of the
   structural fields (older formats): an anchored prefix match for the
   relay / hand-back shapes only. Structure always wins over text, so a
   human-typed line that quotes a marker is still human.

An Agent SDK prompt is written by a program, so it counts as injected
-- but only when its line says so, through ``promptSource: "sdk"`` or
``turnOrigin: "sdk"`` (the only marker on SDK-issued slash-command
lines). An SDK prompt line carrying neither field is not recognized:
the text fallback covers relay / hand-back shapes, not SDK prompts. An
``entrypoint``-based fallback was considered and deferred (#667 scope
ruling).

The value sets are module-level so calibration can sweep them.
"""

from __future__ import annotations

from agentfluent.core.session import SessionMessage

HUMAN_PROMPT_SOURCES: frozenset[str] = frozenset(
    {"typed", "queued", "suggestion_accepted"},
)
"""``promptSource`` values written for a prompt a human entered."""

HUMAN_ORIGIN_KINDS: frozenset[str] = frozenset({"human"})
"""``origin.kind`` values written for a prompt a human entered."""

HUMAN_TURN_ORIGINS: frozenset[str] = frozenset({"human"})
"""``turnOrigin`` values written for a prompt a human entered."""

INJECTED_TEXT_PREFIXES: tuple[str, ...] = (
    "Another Claude session sent a message:",
    "<agent-message from=",
    "[Subagent hand-back]",
)
"""Leading text of relay / hand-back messages, used only for lines that
carry no structural provenance field."""


def _has_structural_provenance(message: SessionMessage) -> bool:
    return (
        message.is_meta
        or message.is_compact_summary
        or message.prompt_source is not None
        or message.origin_kind is not None
        or message.turn_origin is not None
    )


def is_machine_injected(message: SessionMessage) -> bool:
    """Return ``True`` when a ``user`` message was not typed by a human.

    Non-``user`` messages return ``False`` (the question does not apply).
    See the module docstring for the decision order.
    """
    if message.type != "user":
        return False
    if message.is_meta or message.is_compact_summary:
        return True
    if (
        message.prompt_source is not None
        and message.prompt_source not in HUMAN_PROMPT_SOURCES
    ):
        return True
    if (
        message.origin_kind is not None
        and message.origin_kind not in HUMAN_ORIGIN_KINDS
    ):
        return True
    if (
        message.turn_origin is not None
        and message.turn_origin not in HUMAN_TURN_ORIGINS
    ):
        return True
    if _has_structural_provenance(message):
        return False
    return message.text.lstrip().startswith(INJECTED_TEXT_PREFIXES)
