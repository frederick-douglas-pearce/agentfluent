"""Parent-thread quality signal extraction.

Sibling to ``signals.py`` (metadata-level) and ``trace_signals.py``
(subagent-trace-level). This module mines the parent session's user/
assistant message stream for behavioral patterns that proxy quality
misses — moments where a review-style subagent (architect, code-reviewer,
tester, security-review) would likely have caught an issue before the
parent committed to it.

Tier 1 quality signals (per the v0.6 quality-axis epic, #268):

- ``USER_CORRECTION`` — the user interrupts or redirects the parent
  mid-flight ("no, do X instead", "wait, that's wrong", "revert"). High
  correction frequency in sessions without review subagents is strong
  evidence the parent would benefit from independent review. Detected
  here in #269.
- ``FILE_REWORK`` — same file edited N+ times within a session. Detected
  in #270.
- ``REVIEWER_CAUGHT`` — review-style subagents that ran AND produced
  substantive findings the parent acted on. Detected in #271.

The function signature accepts ``agent_invocations`` from day one so
#271 can integrate without forcing a mid-flight refactor; #269's
USER_CORRECTION detection ignores the param (messages-only).

False-positive guardrail (3-tier heuristic) for USER_CORRECTION:

1. **Strong-correction override** — high-confidence phrases (``that's
   wrong``, ``revert``, ``undo``, ``that's not what I``) fire regardless
   of the preceding-message classification. These are corrections of the
   parent's reasoning, not answers to a question.
2. **Primary gate** — when the preceding assistant message contains a
   write-style ``tool_use`` (``WRITE_TOOLS``), any pattern hit fires.
   The user is correcting an action, not answering a question.
3. **Question suppression** — when the preceding assistant message ends
   with ``?`` AND has no write tools, soft-correction patterns are
   suppressed (only strong-correction can fire, already covered by #1).

Pattern lists and tier-membership are module-level constants so #274's
calibration notebook can sweep them without function-body edits.
"""

from __future__ import annotations

import re

from agentfluent.agents.models import WRITE_TOOLS, AgentInvocation
from agentfluent.config.models import Severity
from agentfluent.core.session import SessionMessage
from agentfluent.diagnostics.models import DiagnosticSignal, SignalType

# Review-style subagents whose presence and findings drive the
# REVIEWER_CAUGHT signal in #271. Defined here (not in ``agents.models``)
# because it is quality-axis-specific and may diverge from ``BUILTIN_AGENT_TYPES``
# as users add custom review agents (e.g. project-specific ``security-review``
# variants). Custom review-agent names will be a calibration-time addition
# in #274.
REVIEW_AGENT_TYPES: frozenset[str] = frozenset(
    {"architect", "security-review", "tester", "code-reviewer"},
)


# Soft-correction pattern categories. These fire only when the primary
# gate (preceding message has a write tool) is satisfied. Suppressed when
# the preceding assistant message is question-only.
_NEGATION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bno,?\s", re.IGNORECASE),
    re.compile(r"\bno\s+don'?t\b", re.IGNORECASE),
    re.compile(r"\bthat'?s\s+not\s+what\s+I\b", re.IGNORECASE),
)
_INTERRUPTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bstop\b", re.IGNORECASE),
    re.compile(r"\bwait\b", re.IGNORECASE),
    re.compile(r"\bhold\s+on\b", re.IGNORECASE),
)
_REDIRECTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bactually,?\s", re.IGNORECASE),
    re.compile(r"\binstead,?\s", re.IGNORECASE),
    re.compile(r"\bI\s+meant\b", re.IGNORECASE),
    re.compile(r"\bwhat\s+I\s+wanted\s+was\b", re.IGNORECASE),
)
_UNDO_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bgo\s+back\s+to\b", re.IGNORECASE),
    re.compile(r"\brestore\b", re.IGNORECASE),
)

# Strong-correction overlay: a strict subset of high-confidence phrases
# that fire regardless of the preceding-message gate. ``revert`` and
# ``undo`` are intentionally promoted out of ``_UNDO_PATTERNS`` to live
# only here so a single regex match can never be claimed by both tiers.
# ``that's wrong`` and ``that's not what I`` are corrections of the
# parent's reasoning — they signal the user is overruling the system,
# not answering a question.
_STRONG_CORRECTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bthat'?s\s+wrong\b", re.IGNORECASE),
    re.compile(r"\brevert\b", re.IGNORECASE),
    re.compile(r"\bundo\b", re.IGNORECASE),
    re.compile(r"\bthat'?s\s+not\s+what\s+I\b", re.IGNORECASE),
)

# Soft-correction overlay: union of the category lists (all soft).
# Iteration order preserved so the first-match category label is
# deterministic and useful in ``detail.matched_pattern``.
_SOFT_PATTERN_CATEGORIES: tuple[tuple[str, tuple[re.Pattern[str], ...]], ...] = (
    ("negation", _NEGATION_PATTERNS),
    ("interruption", _INTERRUPTION_PATTERNS),
    ("redirection", _REDIRECTION_PATTERNS),
    ("undo", _UNDO_PATTERNS),
)

# Cap on the user-message snippet stamped onto each signal's ``detail``.
# 140 chars is enough to identify the correction in CLI output without
# spilling whole prompts into the diagnostics envelope.
_SNIPPET_MAX_CHARS = 140


def _preceding_assistant_action(
    messages: list[SessionMessage], user_idx: int,
) -> tuple[bool, bool]:
    """Classify the assistant message immediately preceding ``messages[user_idx]``.

    Returns ``(had_write_tool, is_question_only)``:

    - ``had_write_tool`` — True when the preceding assistant message
      carries any ``tool_use`` block whose ``name`` is in ``WRITE_TOOLS``.
      Indicates the assistant was actively implementing, so a follow-up
      correction is structurally a correction-of-action.
    - ``is_question_only`` — True when the preceding assistant message's
      text ends with ``?`` AND no write tool was used. Indicates the
      assistant was asking for clarification, so a "no" answer is not
      a correction.

    When no preceding assistant message exists (user is the first
    analytical message), both flags are False — strong-correction
    patterns can still fire, soft patterns cannot.
    """
    for prev_idx in range(user_idx - 1, -1, -1):
        prev = messages[prev_idx]
        if prev.type != "assistant":
            continue
        had_write_tool = any(
            block.name in WRITE_TOOLS for block in prev.tool_use_blocks
        )
        prev_text = prev.text.rstrip()
        is_question_only = (
            not had_write_tool and prev_text.endswith("?")
        )
        return had_write_tool, is_question_only
    return False, False


def _match_correction(
    user_text: str,
    *,
    had_write_tool: bool,
    is_question_only: bool,
) -> tuple[str, str] | None:
    """Apply the 3-tier heuristic and return ``(category, matched_phrase)``
    when the user message is a correction; ``None`` otherwise.

    Tier order:
    1. Strong-correction override — fires unconditionally on hit.
    2. Primary gate — soft patterns fire when ``had_write_tool``.
    3. Question suppression — soft patterns suppressed when
       ``is_question_only`` (already implied by ``not had_write_tool``
       in this code path; the explicit check is kept for clarity).

    Returns the first matching category and the matched substring so
    callers can stamp it on the signal's ``detail.matched_pattern`` for
    calibration analysis in #274.
    """
    for pattern in _STRONG_CORRECTION_PATTERNS:
        if match := pattern.search(user_text):
            return "strong", match.group(0)

    if not had_write_tool or is_question_only:
        return None

    for category, patterns in _SOFT_PATTERN_CATEGORIES:
        for pattern in patterns:
            if match := pattern.search(user_text):
                return category, match.group(0)
    return None


def _user_message_text(message: SessionMessage) -> str:
    """Concatenated text content from a user message.

    Mirrors ``SessionMessage.text`` but drops to the empty string for
    messages whose content is purely tool_result blocks (no text). The
    parent thread carries both kinds — agent tool_result envelopes are
    not user prose and must not be scanned for correction patterns.
    """
    return message.text


def extract_quality_signals(
    messages: list[SessionMessage],
    agent_invocations: list[AgentInvocation] | None = None,
) -> list[DiagnosticSignal]:
    """Extract quality-axis signals from parent-thread messages.

    Currently emits ``USER_CORRECTION`` only; ``FILE_REWORK`` (#270) and
    ``REVIEWER_CAUGHT`` (#271) will land here. ``agent_invocations`` is
    accepted but unused — locked in this signature so #271 can plug in
    without breaking #270 mid-flight.

    Returns an empty list when ``messages`` is empty or contains no
    user messages. ``agent_type`` is ``None`` on every emitted signal:
    these are cross-cutting parent-thread observations, not subagent-
    scoped findings.
    """
    del agent_invocations  # Reserved for #271 (REVIEWER_CAUGHT)

    if not messages:
        return []

    # First pass: collect (user_idx, category, matched_phrase, snippet,
    # preceding_action) for each detected correction. ``total_user_messages``
    # is the denominator for ``session_correction_rate`` — counted on
    # the same pass so we don't traverse twice.
    detections: list[tuple[int, str, str, str, str]] = []
    total_user_messages = 0

    for idx, msg in enumerate(messages):
        if msg.type != "user":
            continue
        text = _user_message_text(msg)
        if not text:
            continue
        total_user_messages += 1

        had_write_tool, is_question_only = _preceding_assistant_action(messages, idx)
        match_result = _match_correction(
            text,
            had_write_tool=had_write_tool,
            is_question_only=is_question_only,
        )
        if match_result is None:
            continue
        category, matched_phrase = match_result

        if had_write_tool:
            preceding_action = "write_tool"
        elif is_question_only:
            preceding_action = "question"
        else:
            preceding_action = "text_only"

        snippet = text[:_SNIPPET_MAX_CHARS]
        if len(text) > _SNIPPET_MAX_CHARS:
            snippet = snippet.rstrip() + "…"

        detections.append((idx, category, matched_phrase, snippet, preceding_action))

    if not detections or total_user_messages == 0:
        return []

    session_correction_rate = len(detections) / total_user_messages

    # Second pass: construct signals once with the rate already known.
    # Avoids post-construction mutation of the detail dict.
    signals: list[DiagnosticSignal] = []
    for _, category, matched_phrase, snippet, preceding_action in detections:
        signals.append(
            DiagnosticSignal(
                signal_type=SignalType.USER_CORRECTION,
                severity=Severity.WARNING,
                agent_type=None,
                invocation_id=None,
                message=f"User correction in parent thread: {snippet}",
                detail={
                    "correction_text": snippet,
                    "matched_pattern": matched_phrase,
                    "matched_category": category,
                    "preceding_assistant_action": preceding_action,
                    "session_correction_rate": session_correction_rate,
                    "total_user_messages": total_user_messages,
                    "total_corrections": len(detections),
                },
            ),
        )
    return signals
