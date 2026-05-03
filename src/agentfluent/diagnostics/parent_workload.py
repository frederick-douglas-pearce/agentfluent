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

# Burst-shape filters. Calibrated by sub-issue F (#189) against the
# agentfluent dogfood dataset; defaults are conservative starting points.
MIN_BURST_TOOLS = 2
"""Drop 1-tool bursts. A solitary Read or Bash isn't a delegation candidate."""

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
    """Text of the most recent real user message before the burst opened."""

    assistant_text: str
    """Concatenated text content from every assistant message contributing
    to the burst. Joined with newlines."""

    tool_use_blocks: list[ToolUseBlock]
    """Tool calls in original order across the burst's constituent assistant
    messages. NOT de-duplicated — repeated tool use IS a discriminative
    signal that sub-issue D's TF-IDF clustering will weight."""

    usage: Usage = field(default_factory=Usage)
    """Summed token usage across the burst's constituent assistant messages."""

    model: str = ""
    """Parent-thread model id from the first contributing assistant message
    (e.g., ``claude-opus-4-7``). All messages in a single tool loop should
    share a model in practice; if they don't, we keep the first and log."""


def _is_real_user_text(msg: SessionMessage) -> bool:
    """A 'real' user turn vs a tool-result wrapper.

    Claude Code emits tool-result responses as user-typed messages with no
    text and a ``tool_result`` content block. Those don't break a burst.
    A real user turn has actual text AND no tool_result block.
    """
    if msg.type != "user":
        return False
    has_tool_result = any(b.type == "tool_result" for b in msg.content_blocks)
    if has_tool_result:
        return False
    return bool(msg.text.strip())


def _sum_usage(usages: list[Usage]) -> Usage:
    """Field-wise sum of token usage across messages."""
    total = Usage()
    for u in usages:
        total.input_tokens += u.input_tokens
        total.output_tokens += u.output_tokens
        total.cache_creation_input_tokens += u.cache_creation_input_tokens
        total.cache_read_input_tokens += u.cache_read_input_tokens
    return total


def extract_bursts(messages: list[SessionMessage]) -> list[ToolBurst]:
    """Group consecutive parent-thread tool calls into ``ToolBurst`` records.

    See module docstring for the boundary rule. No filtering applied here —
    every burst observed in the message stream is returned. Use
    :func:`filter_bursts` to drop too-small / too-large / too-short bursts
    before clustering.
    """
    bursts: list[ToolBurst] = []
    last_real_user_text = ""

    # Open-burst accumulators. None = no burst currently open.
    cur_assistant_texts: list[str] | None = None
    cur_tool_blocks: list[ToolUseBlock] | None = None
    cur_usages: list[Usage] | None = None
    cur_model: str | None = None
    cur_preceding_user_text: str = ""

    def close_burst() -> None:
        nonlocal cur_assistant_texts, cur_tool_blocks, cur_usages, cur_model
        if cur_tool_blocks is None or not cur_tool_blocks:
            cur_assistant_texts = cur_tool_blocks = cur_usages = None
            cur_model = None
            return
        bursts.append(
            ToolBurst(
                preceding_user_text=cur_preceding_user_text,
                assistant_text="\n".join(t for t in (cur_assistant_texts or []) if t),
                tool_use_blocks=list(cur_tool_blocks),
                usage=_sum_usage(cur_usages or []),
                model=cur_model or "",
            ),
        )
        cur_assistant_texts = cur_tool_blocks = cur_usages = None
        cur_model = None

    for msg in messages:
        if _is_real_user_text(msg):
            close_burst()
            last_real_user_text = msg.text
            continue

        if msg.type == "assistant":
            tool_blocks = msg.tool_use_blocks
            if not tool_blocks:
                # Pure-text assistant turn (e.g., final answer) — doesn't
                # extend a burst, but doesn't close one either. Some tool
                # loops emit a text-only "I'll now do X" turn between two
                # tool_use turns; folding that text into the burst keeps
                # the surrounding-context signal that sub-issue D wants.
                if cur_assistant_texts is not None and msg.text:
                    cur_assistant_texts.append(msg.text)
                continue

            if cur_tool_blocks is None:
                cur_assistant_texts = []
                cur_tool_blocks = []
                cur_usages = []
                cur_preceding_user_text = last_real_user_text
                cur_model = msg.model or ""
            elif msg.model and cur_model and msg.model != cur_model:
                logger.debug(
                    "Burst spans assistant messages with differing models "
                    "(%r vs %r); keeping the first.",
                    cur_model, msg.model,
                )

            if msg.text:
                cur_assistant_texts.append(msg.text)  # type: ignore[union-attr]
            cur_tool_blocks.extend(tool_blocks)
            if msg.usage is not None:
                cur_usages.append(msg.usage)  # type: ignore[union-attr]

    close_burst()
    return bursts


def burst_text(burst: ToolBurst) -> str:
    """Compose the text representation a burst contributes to TF-IDF clustering.

    Joins the preceding user prompt + concatenated assistant text + the
    flat tool-name sequence. Tool names are NOT de-duplicated: the
    duplicate ``Read`` in ``"Bash Read Read Edit"`` is a discriminative
    pattern signal that sub-issue D's vectorizer should be free to weight.
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
    typical workflow and shouldn't be folded into one. See sub-issue F
    for the calibration sweep.
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
