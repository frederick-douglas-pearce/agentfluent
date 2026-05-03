"""Parent-thread tool-burst extraction for offload-candidate diagnostics.

Walks a parsed parent-thread session and groups consecutive tool calls into
``ToolBurst`` records — one per "what the user asked, and what the assistant
did to answer it." Bursts are the unit of clustering for #189's offload
recommendations: cluster bursts by similarity, project the parent-thread
cost of each cluster against a cheaper alternative model, surface the
delta as an offload candidate.

This module owns extraction + filtering only. Clustering, cost
estimation, candidate synthesis, pipeline wiring, and CLI rendering land
in sub-issues C–F of #189.

**Burst boundary rule** (assistant-turn with cross-turn merging):

A burst opens at the first assistant message containing ``tool_use``
blocks after a "real" user message. It extends across subsequent
assistant messages so long as only ``tool_result``-only user messages
intervene (the standard Claude tool loop: assistant calls tools, user
message carries results, assistant calls more tools — no human turn).
A burst closes when a real user message arrives or the session ends.

A "real" user message has non-empty ``text`` AND no ``tool_result``
content block. Claude Code emits tool-result responses as user messages
with no text — that's structural, not a human turn.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from agentfluent.core.session import SessionMessage, ToolUseBlock, Usage

logger = logging.getLogger(__name__)

MIN_BURST_TOOLS = 2

MAX_BURST_TOOLS = 20
"""Cap degenerate single-message mega-bursts. Without this, a 'batch refactor'
assistant turn emitting 50 Edit calls in one message would become one
huge burst that dominates any cluster it joined and distorts cost
estimates."""

MIN_BURST_TEXT_TOKENS = 30
"""Whitespace-token floor on ``burst_text`` output. Below this the burst
lacks the semantic context needed for meaningful TF-IDF clustering in
sub-issue D."""


@dataclass
class ToolBurst:
    """A contiguous run of parent-thread tool calls anchored to one user request.

    Internal type — never serialized in JSON output, never crosses the
    diagnostics/CLI boundary. ``OffloadCandidate`` (sub-issue D, in
    ``diagnostics/models.py``) is the cross-boundary Pydantic counterpart.
    """

    preceding_user_text: str
    assistant_text: str
    tool_use_blocks: list[ToolUseBlock]
    """Tool calls in original order. NOT de-duplicated — repeated tool use
    IS a discriminative signal that sub-issue D's TF-IDF clustering will
    weight."""
    usage: Usage = field(default_factory=Usage)
    model: str = ""
    """Model id from the first contributing assistant message. All messages
    in a single tool loop should share a model in practice; if they don't,
    we keep the first and log."""


@dataclass
class _OpenBurst:
    """In-progress burst accumulator. Promoted to ``ToolBurst`` via
    :meth:`finalize` once a real user message or end-of-session closes it."""

    preceding_user_text: str
    model: str = ""
    assistant_texts: list[str] = field(default_factory=list)
    tool_blocks: list[ToolUseBlock] = field(default_factory=list)
    usages: list[Usage] = field(default_factory=list)

    def add_assistant_message(self, msg: SessionMessage) -> None:
        if msg.text:
            self.assistant_texts.append(msg.text)
        self.tool_blocks.extend(msg.tool_use_blocks)
        if msg.usage is not None:
            self.usages.append(msg.usage)

    def add_text(self, text: str) -> None:
        if text:
            self.assistant_texts.append(text)

    def finalize(self) -> ToolBurst | None:
        if not self.tool_blocks:
            return None
        return ToolBurst(
            preceding_user_text=self.preceding_user_text,
            assistant_text="\n".join(t for t in self.assistant_texts if t),
            tool_use_blocks=list(self.tool_blocks),
            usage=sum(self.usages, Usage()),
            model=self.model,
        )


def _is_real_user_text(msg: SessionMessage) -> bool:
    """A 'real' user turn vs a tool-result wrapper.

    Claude Code emits tool-result responses as user-typed messages with no
    text and a ``tool_result`` content block. Those don't break a burst.
    A real user turn has actual text AND no tool_result block.
    """
    if msg.type != "user":
        return False
    if any(b.type == "tool_result" for b in msg.content_blocks):
        return False
    return bool(msg.text.strip())


def extract_bursts(messages: list[SessionMessage]) -> list[ToolBurst]:
    """Group consecutive parent-thread tool calls into ``ToolBurst`` records.

    See module docstring for the boundary rule. No filtering applied here —
    use :func:`filter_bursts` to drop too-small / too-large / too-short
    bursts before clustering.
    """
    bursts: list[ToolBurst] = []
    last_real_user_text = ""
    cur: _OpenBurst | None = None

    for msg in messages:
        if _is_real_user_text(msg):
            if cur is not None and (b := cur.finalize()) is not None:
                bursts.append(b)
            cur = None
            last_real_user_text = msg.text
            continue

        if msg.type != "assistant":
            continue

        tool_blocks = msg.tool_use_blocks
        if not tool_blocks:
            # Text-only assistant turn (e.g., "I'll now do X" between two
            # tool_use turns) — fold its text into the open burst's context
            # without breaking the run. Doesn't open a burst on its own.
            if cur is not None:
                cur.add_text(msg.text)
            continue

        if cur is None:
            cur = _OpenBurst(
                preceding_user_text=last_real_user_text,
                model=msg.model or "",
            )
        elif msg.model and cur.model and msg.model != cur.model:
            logger.debug(
                "Burst spans assistant messages with differing models "
                "(%r vs %r); keeping the first.",
                cur.model, msg.model,
            )

        cur.add_assistant_message(msg)

    if cur is not None and (b := cur.finalize()) is not None:
        bursts.append(b)
    return bursts


def burst_text(burst: ToolBurst) -> str:
    """Compose the text representation a burst contributes to TF-IDF clustering.

    Tool names are NOT de-duplicated: the duplicate ``Read`` in ``"Bash
    Read Read Edit"`` is a discriminative pattern signal that sub-issue
    D's vectorizer should be free to weight.
    """
    parts = [
        burst.preceding_user_text,
        burst.assistant_text,
        " ".join(b.name for b in burst.tool_use_blocks),
    ]
    return " ".join(p for p in parts if p)


def filter_bursts(bursts: list[ToolBurst]) -> list[ToolBurst]:
    """Apply ``MIN_BURST_TOOLS``, ``MAX_BURST_TOOLS``, ``MIN_BURST_TEXT_TOKENS``.

    Bursts above ``MAX_BURST_TOOLS`` are dropped (with a debug log) rather
    than truncated — a 50-tool batch is structurally different from a
    typical workflow and shouldn't be folded into one.
    """
    kept: list[ToolBurst] = []
    for b in bursts:
        n_tools = len(b.tool_use_blocks)
        if n_tools < MIN_BURST_TOOLS:
            continue
        if n_tools > MAX_BURST_TOOLS:
            logger.debug(
                "Dropping burst with %d tool calls (cap: %d); "
                "preceding_user_text=%r",
                n_tools, MAX_BURST_TOOLS, b.preceding_user_text[:60],
            )
            continue
        if len(burst_text(b).split()) < MIN_BURST_TEXT_TOKENS:
            continue
        kept.append(b)
    return kept
