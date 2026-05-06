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
  in #269.
- ``FILE_REWORK`` — same file edited N+ times within a session,
  especially after a feature was declared "done". High rework density
  indicates a pre-implementation review or incremental testing would
  have caught issues earlier. Detected in #270.
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
from collections import defaultdict
from enum import StrEnum

from agentfluent.agents.models import WRITE_TOOLS, AgentInvocation
from agentfluent.config.models import Severity
from agentfluent.core.session import SessionMessage
from agentfluent.diagnostics.models import DiagnosticSignal, SignalType

# Review-style subagents whose presence and findings drive the
# REVIEWER_CAUGHT signal in #271. Defined here (not in ``agents.models``)
# because it is quality-axis-specific and may diverge from
# ``BUILTIN_AGENT_TYPES`` as users add custom review agents (e.g.
# project-specific ``security-review`` variants). Custom review-agent
# names will be a calibration-time addition in #274.
REVIEW_AGENT_TYPES: frozenset[str] = frozenset(
    {"architect", "security-review", "tester", "code-reviewer"},
)


class PrecedingAction(StrEnum):
    """How the assistant message preceding a user correction is classified.

    Stamped on each ``USER_CORRECTION`` signal's ``detail`` so #274
    calibration can analyze precision per-tier and so consumers can
    distinguish corrections-of-action from corrections-of-reasoning.
    """

    WRITE_TOOL = "write_tool"
    QUESTION = "question"
    TEXT_ONLY = "text_only"


class CorrectionCategory(StrEnum):
    """Pattern-tier classification for a detected user correction.

    ``STRONG`` corresponds to ``_STRONG_CORRECTION_PATTERNS``;
    the soft tiers correspond to the categories in
    ``_SOFT_PATTERN_CATEGORIES`` and are useful for #274 calibration.
    """

    STRONG = "strong"
    NEGATION = "negation"
    INTERRUPTION = "interruption"
    REDIRECTION = "redirection"
    UNDO = "undo"


def _ci(*patterns: str) -> tuple[re.Pattern[str], ...]:
    """Compile a tuple of case-insensitive regex patterns."""
    return tuple(re.compile(p, re.IGNORECASE) for p in patterns)


# Soft-correction pattern categories. These fire only when the primary
# gate (preceding message has a write tool) is satisfied. Suppressed when
# the preceding assistant message is question-only.
_NEGATION_PATTERNS = _ci(
    r"\bno,?\s",
    r"\bno\s+don'?t\b",
    r"\bthat'?s\s+not\s+what\s+I\b",
)
_INTERRUPTION_PATTERNS = _ci(r"\bstop\b", r"\bwait\b", r"\bhold\s+on\b")
_REDIRECTION_PATTERNS = _ci(
    r"\bactually,?\s",
    r"\binstead,?\s",
    r"\bI\s+meant\b",
    r"\bwhat\s+I\s+wanted\s+was\b",
)
_UNDO_PATTERNS = _ci(r"\bgo\s+back\s+to\b", r"\brestore\b")

# Strong-correction overlay: a strict subset of high-confidence phrases
# that fire regardless of the preceding-message gate. ``revert`` and
# ``undo`` are intentionally promoted out of ``_UNDO_PATTERNS`` to live
# only here so a single regex match can never be claimed by both tiers.
_STRONG_CORRECTION_PATTERNS = _ci(
    r"\bthat'?s\s+wrong\b",
    r"\brevert\b",
    r"\bundo\b",
    r"\bthat'?s\s+not\s+what\s+I\b",
)

_SOFT_PATTERN_CATEGORIES: tuple[
    tuple[CorrectionCategory, tuple[re.Pattern[str], ...]], ...
] = (
    (CorrectionCategory.NEGATION, _NEGATION_PATTERNS),
    (CorrectionCategory.INTERRUPTION, _INTERRUPTION_PATTERNS),
    (CorrectionCategory.REDIRECTION, _REDIRECTION_PATTERNS),
    (CorrectionCategory.UNDO, _UNDO_PATTERNS),
)

_SNIPPET_MAX_CHARS = 140


# Edit-tool subset of WRITE_TOOLS used for FILE_REWORK detection. Strict
# subset by design: ``Bash`` is excluded because its ``input`` carries
# ``command``, not ``file_path`` — extracting a filesystem target from a
# shell command is out of scope and error-prone. ``NotebookEdit`` is
# excluded because ``.ipynb`` rework is rare in our target use cases
# (Agent SDK + Claude Code). Sync with ``WRITE_TOOLS`` if that set grows
# with another file-path-bearing tool; the drift-prevention test in
# ``test_quality_signals.py`` will fail if this subset escapes.
_EDIT_TOOL_NAMES: frozenset[str] = frozenset({"Edit", "Write", "MultiEdit"})

# A file edited at or above this threshold within a single session
# fires ``FILE_REWORK``. Module-level constant for #274 calibration.
_FILE_REWORK_THRESHOLD = 4

# "Completion language" patterns. When any assistant message in the
# session matches one of these, subsequent edits to any file are
# counted as ``post_completion_edits``. Session-level granularity is
# the intentional Tier 1 trade-off — per-file completion tracking
# would require NLP to associate "done" with a specific file, which
# is a #274 concern. ``completion_scope: "session"`` is stamped on
# every emitted FILE_REWORK signal so calibration can measure how
# often multi-task sessions cause false attribution.
_COMPLETION_PATTERNS = _ci(
    r"\b(done|complete|completed|finished)\b",
    r"\bready\s+for\s+review\b",
    r"\ball\s+set\b",
    r"\bimplementation\s+complete\b",
)


def _classify_assistant(message: SessionMessage) -> tuple[bool, bool]:
    """Return ``(had_write_tool, is_question_only)`` for an assistant message.

    - ``had_write_tool`` — any ``tool_use`` block whose ``name`` is in
      ``WRITE_TOOLS``. Indicates the assistant was actively implementing,
      so a follow-up correction is structurally a correction-of-action.
    - ``is_question_only`` — text ends with ``?`` AND no write tool.
      Indicates the assistant was asking for clarification, so a "no"
      answer is not a correction.
    """
    had_write_tool = any(
        block.name in WRITE_TOOLS for block in message.tool_use_blocks
    )
    is_question_only = (
        not had_write_tool and message.text.rstrip().endswith("?")
    )
    return had_write_tool, is_question_only


def _match_correction(
    user_text: str,
    *,
    had_write_tool: bool,
    is_question_only: bool,
) -> tuple[CorrectionCategory, str] | None:
    """Apply the 3-tier heuristic; return ``(category, matched_phrase)`` or ``None``.

    Tier order: strong override → primary gate (write tool) → question
    suppression. Returns the first matching category and matched
    substring so callers can stamp it on ``detail`` for #274 calibration.
    """
    for pattern in _STRONG_CORRECTION_PATTERNS:
        if match := pattern.search(user_text):
            return CorrectionCategory.STRONG, match.group(0)

    # Soft patterns require the primary gate (write-tool present) and
    # are suppressed by the question-only classification.
    if not had_write_tool or is_question_only:
        return None

    for category, patterns in _SOFT_PATTERN_CATEGORIES:
        for pattern in patterns:
            if match := pattern.search(user_text):
                return category, match.group(0)
    return None


def _build_snippet(text: str) -> str:
    if len(text) <= _SNIPPET_MAX_CHARS:
        return text
    return text[:_SNIPPET_MAX_CHARS].rstrip() + "…"


def _matches_completion_phrase(text: str) -> bool:
    return any(p.search(text) for p in _COMPLETION_PATTERNS)


def _emit_user_correction_signals(
    detections: list[tuple[CorrectionCategory, str, str, PrecedingAction]],
    total_user_messages: int,
) -> list[DiagnosticSignal]:
    if not detections or total_user_messages == 0:
        return []
    session_correction_rate = len(detections) / total_user_messages
    return [
        DiagnosticSignal(
            signal_type=SignalType.USER_CORRECTION,
            severity=Severity.WARNING,
            agent_type=None,
            invocation_id=None,
            message=f"User correction in parent thread: {snippet}",
            detail={
                "correction_text": snippet,
                "matched_pattern": matched_phrase,
                "matched_category": category.value,
                "preceding_assistant_action": preceding.value,
                "session_correction_rate": session_correction_rate,
                "total_user_messages": total_user_messages,
            },
        )
        for category, matched_phrase, snippet, preceding in detections
    ]


def _emit_file_rework_signals(
    file_edit_counts: dict[str, int],
    post_completion_edits: dict[str, int],
    edit_tools_per_file: dict[str, set[str]],
) -> list[DiagnosticSignal]:
    return [
        DiagnosticSignal(
            signal_type=SignalType.FILE_REWORK,
            severity=Severity.WARNING,
            agent_type=None,
            invocation_id=None,
            message=(
                f"File '{file_path}' edited {count} times in this session"
            ),
            detail={
                "file_path": file_path,
                "edit_count": count,
                "post_completion_edits": post_completion_edits.get(
                    file_path, 0,
                ),
                "edit_tools": sorted(edit_tools_per_file[file_path]),
                "completion_scope": "session",
            },
        )
        for file_path, count in file_edit_counts.items()
        if count >= _FILE_REWORK_THRESHOLD
    ]


def extract_quality_signals(
    messages: list[SessionMessage],
    agent_invocations: list[AgentInvocation] | None = None,
) -> list[DiagnosticSignal]:
    """Extract quality-axis signals from parent-thread messages.

    Emits ``USER_CORRECTION`` (#269) and ``FILE_REWORK`` (#270);
    ``REVIEWER_CAUGHT`` (#271) will land here. ``agent_invocations`` is
    accepted but unused by the current detectors — locked in this
    signature so #271 can plug in without breaking the existing
    callers.

    Returns an empty list when ``messages`` is empty. ``agent_type`` is
    ``None`` on every emitted signal: these are cross-cutting
    parent-thread observations, not subagent-scoped findings.
    """
    if not messages:
        return []

    # Single forward pass: track the most recently seen assistant's
    # classification (for correction detection), file-edit counts (for
    # rework detection), and a session-level ``completion_seen`` flag
    # (gates ``post_completion_edits``).
    last_assistant: tuple[bool, bool] | None = None
    correction_detections: list[
        tuple[CorrectionCategory, str, str, PrecedingAction]
    ] = []
    total_user_messages = 0
    file_edit_counts: dict[str, int] = defaultdict(int)
    post_completion_edits: dict[str, int] = defaultdict(int)
    edit_tools_per_file: dict[str, set[str]] = defaultdict(set)
    completion_seen = False

    for msg in messages:
        if msg.type == "assistant":
            last_assistant = _classify_assistant(msg)
            if not completion_seen and _matches_completion_phrase(msg.text):
                completion_seen = True
            for block in msg.tool_use_blocks:
                if block.name not in _EDIT_TOOL_NAMES:
                    continue
                fp = block.input.get("file_path")
                if not isinstance(fp, str) or not fp:
                    continue
                file_edit_counts[fp] += 1
                edit_tools_per_file[fp].add(block.name)
                if completion_seen:
                    post_completion_edits[fp] += 1
            continue
        if msg.type != "user":
            continue
        text = msg.text
        if not text:
            continue
        total_user_messages += 1

        had_write_tool, is_question_only = last_assistant or (False, False)
        result = _match_correction(
            text,
            had_write_tool=had_write_tool,
            is_question_only=is_question_only,
        )
        if result is None:
            continue
        category, matched_phrase = result

        if had_write_tool:
            preceding = PrecedingAction.WRITE_TOOL
        elif is_question_only:
            preceding = PrecedingAction.QUESTION
        else:
            preceding = PrecedingAction.TEXT_ONLY

        correction_detections.append(
            (category, matched_phrase, _build_snippet(text), preceding),
        )

    return [
        *_emit_user_correction_signals(
            correction_detections, total_user_messages,
        ),
        *_emit_file_rework_signals(
            file_edit_counts, post_completion_edits, edit_tools_per_file,
        ),
    ]
