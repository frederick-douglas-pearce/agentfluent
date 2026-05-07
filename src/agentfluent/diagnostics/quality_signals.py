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
from collections import Counter, defaultdict
from dataclasses import dataclass, field
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

# Session-level gates on USER_CORRECTION emission. Disjunctive (OR):
# either the absolute count crosses ``MIN_CORRECTIONS_PER_SESSION`` or
# the rate crosses ``MIN_CORRECTION_RATE``. PM review on #274 chose OR
# over AND so a long session with two strong corrections still surfaces
# (the ANDed form would have suppressed it at low rates), while still
# blocking the single-correction noise floor. Calibration may tune.
MIN_CORRECTIONS_PER_SESSION = 2
MIN_CORRECTION_RATE = 0.10


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
# fires ``FILE_REWORK``. Calibrated in #274 against agentfluent dogfood
# data: edit_count distribution was p25=4, median=5, p75=8, p90=12 —
# the prior default of 4 sat at the noise floor and over-fired on
# normal iterative dev. Raised to 8 to land above the p75, in the
# right tail where edits-per-file plausibly indicate a quality miss.
_FILE_REWORK_THRESHOLD = 8

# When ``True``, lower the FILE_REWORK threshold by 1 for files that
# received any post-completion edits. Disabled in #274 calibration:
# completion-language patterns (``done``/``complete``/``finished``)
# are ubiquitous in normal dev prose, so the boost effectively meant
# "always lower the threshold by 1" — defeating the very signal it
# was supposed to encode. Re-enable only after ``_COMPLETION_PATTERNS``
# is tightened to require explicit ship claims.
POST_COMPLETION_BOOST = False

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


# Keywords that indicate a review subagent's response contains a
# substantive finding rather than a "looks good" pass. Combined with
# ``_SUBSTANTIVE_RESPONSE_MIN_CHARS`` as an AND-gate before emitting a
# REVIEWER_CAUGHT signal: short responses or those without these
# keywords are treated as clean passes and produce no signal.
_FINDING_KEYWORDS: frozenset[str] = frozenset(
    {
        "blocker", "issue", "concern", "must", "should",
        "warning", "risk", "vulnerability", "fix", "change needed",
    },
)
_FINDING_KEYWORDS_PATTERN = re.compile(
    "|".join(rf"\b{re.escape(k)}\b" for k in _FINDING_KEYWORDS),
    re.IGNORECASE,
)

# Minimum count of finding-keyword hits required for a review's
# response to count as substantive. The current behavior is "any
# keyword fires"; exposing this as a constant lets #274 calibration
# sweep ``{1, 2, 3}``. Default stays at 1 to preserve existing
# behavior — calibration is the mechanism for raising it.
MIN_FINDING_KEYWORDS = 1

# Minimum response length (chars) for a review subagent's output to
# count as substantive. Short responses are ``LGTM``-style passes.
_SUBSTANTIVE_RESPONSE_MIN_CHARS = 500

# Per-(session, agent_type) rate gate on REVIEWER_CAUGHT emission. If
# a review agent produced substantive findings on fewer than this
# fraction of its invocations within a session, all REVIEWER_CAUGHT
# signals from that agent_type in that session are suppressed —
# treats noisy reviewers as not-yet-earning-their-keep. Per-session
# scope avoids cross-session persistence (analyze-time architecture).
MIN_REVIEWER_CAUGHT_RATE = 0.5

# Source-file extensions a review subagent is plausibly referencing.
# The list is conservative — including version strings (``v1.0``) and
# domain names (``github.com``) as "files" would inflate ``parent_acted``
# counts. Architect review on #271 promoted this list to a module
# constant for #274 calibration.
_SOURCE_FILE_EXTENSIONS: frozenset[str] = frozenset(
    {
        "py", "ts", "js", "jsx", "tsx", "md", "yaml", "yml", "json",
        "toml", "rs", "go", "java", "rb", "sh", "css", "html", "c",
        "cpp", "h", "hpp",
    },
)
_SOURCE_FILE_EXTENSIONS_RE = "|".join(_SOURCE_FILE_EXTENSIONS)
# Match either (a) paths with a directory component (anything before a
# ``/``) and a file-like name, or (b) bare filenames whose extension is
# in our known-source-extension set. Both styles are common in review
# output ("see src/foo.py" vs "rename module.py to ...").
_DIR_PATH_PATTERN = re.compile(
    r"(?:[a-zA-Z0-9_.\-]+/)+[a-zA-Z0-9_\-]+\.[a-zA-Z]{1,10}",
)
_BARE_FILE_PATTERN = re.compile(
    rf"\b[a-zA-Z0-9_\-]+\.(?:{_SOURCE_FILE_EXTENSIONS_RE})\b",
    re.IGNORECASE,
)

# Cap on assistant messages to scan after a review's tool_result when
# checking whether the parent acted on the review's findings. An edit
# 150 messages later is almost certainly unrelated — capping at 15
# matches the typical "reviewer findings → parent follows up within a
# few turns" cadence and is calibratable in #274.
_PARENT_ACTED_WINDOW = 15


@dataclass(slots=True)
class _FileEditStats:
    """Accumulator for a single file's edit activity within a session.

    Bundles the three lockstep state values (``count``, ``post_completion``,
    ``tools``) so the forward-walk update site is one record mutation
    rather than three parallel-dict updates, and the emit site reads
    one struct rather than threading three dicts as parameters.
    """

    count: int = 0
    post_completion: int = 0
    tools: set[str] = field(default_factory=set)


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
    if (
        len(detections) < MIN_CORRECTIONS_PER_SESSION
        and session_correction_rate < MIN_CORRECTION_RATE
    ):
        return []
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


def _extract_files_mentioned(text: str) -> set[str]:
    """Extract plausible source-file paths from review prose.

    Strict by design (architect review concern #1 on #271): matches
    require either a directory separator (``src/foo.py``) or a known
    source-file extension (``.py``, ``.ts``, ``.md``, ...). Strings like
    ``v1.0``, ``section 3.b``, and ``github.com`` do not match —
    inflating ``parent_acted = True`` from prose noise would undermine
    the signal's reliability, and a missed obscure extension is the
    safer error direction (yields ``parent_acted = False``).
    """
    return set(_DIR_PATH_PATTERN.findall(text)) | set(
        _BARE_FILE_PATTERN.findall(text),
    )


def _files_edited_after(
    messages: list[SessionMessage],
    start_idx: int,
    *,
    window: int = _PARENT_ACTED_WINDOW,
) -> set[str]:
    """File paths edited in the next ``window`` assistant messages
    after ``start_idx``. Used to compute ``parent_acted`` — capped so
    edits 100+ messages later cannot be attributed to the review."""
    edited: set[str] = set()
    seen_assistants = 0
    for msg in messages[start_idx + 1:]:
        if msg.type != "assistant":
            continue
        seen_assistants += 1
        if seen_assistants > window:
            break
        for block in msg.tool_use_blocks:
            if block.name not in _EDIT_TOOL_NAMES:
                continue
            fp = block.input.get("file_path")
            if isinstance(fp, str) and fp:
                edited.add(fp)
    return edited


def _extract_reviewer_caught_signals(
    messages: list[SessionMessage],
    agent_invocations: list[AgentInvocation],
) -> list[DiagnosticSignal]:
    """Emit one ``REVIEWER_CAUGHT`` per substantive review.

    A review invocation is "substantive" when the response is at least
    ``_SUBSTANTIVE_RESPONSE_MIN_CHARS`` long AND mentions at least one
    finding keyword. ``parent_acted`` is True when any file mentioned
    in the review's output is edited in the following
    ``_PARENT_ACTED_WINDOW`` assistant messages.
    """
    # Skip the per-message walk when no review-style agents ran at all
    # — the most common case in non-review-using projects.
    review_invocations = [
        inv for inv in agent_invocations
        if inv.agent_type.lower() in REVIEW_AGENT_TYPES
    ]
    if not review_invocations:
        return []

    # tool_use_id -> index of the message containing the corresponding
    # tool_result block. Lets us scope ``parent_acted`` look-forward to
    # messages strictly after the review's result.
    tool_result_idx: dict[str, int] = {}
    for idx, msg in enumerate(messages):
        if msg.type != "user":
            continue
        for block in msg.content_blocks:
            if block.type == "tool_result" and block.tool_use_id:
                tool_result_idx[block.tool_use_id] = idx

    # First pass: build candidate signals per agent_type and count
    # total invocations per agent_type. The rate gate at the end emits
    # only those agent_types whose substantive-finding fraction meets
    # ``MIN_REVIEWER_CAUGHT_RATE``.
    candidates_by_agent: defaultdict[str, list[DiagnosticSignal]] = defaultdict(list)
    invocations_by_agent: Counter[str] = Counter()
    for inv in review_invocations:
        invocations_by_agent[inv.agent_type] += 1
        text = inv.output_text
        if not text or len(text) < _SUBSTANTIVE_RESPONSE_MIN_CHARS:
            continue
        keyword_hits = sorted({m.lower() for m in _FINDING_KEYWORDS_PATTERN.findall(text)})
        if len(keyword_hits) < MIN_FINDING_KEYWORDS:
            continue

        files_mentioned = _extract_files_mentioned(text)
        files_acted_on: set[str] = set()
        result_idx = tool_result_idx.get(inv.tool_use_id)
        if result_idx is not None and files_mentioned:
            edited = _files_edited_after(messages, result_idx)
            files_acted_on = files_mentioned & edited

        candidates_by_agent[inv.agent_type].append(
            DiagnosticSignal(
                signal_type=SignalType.REVIEWER_CAUGHT,
                severity=Severity.INFO,
                agent_type=inv.agent_type,
                invocation_id=inv.tool_use_id,
                message=(
                    f"`{inv.agent_type}` review surfaced "
                    f"{len(keyword_hits)} finding-keyword(s)"
                ),
                detail={
                    "finding_keywords": keyword_hits,
                    "parent_acted": bool(files_acted_on),
                    "response_length": len(text),
                    "files_mentioned": sorted(files_mentioned),
                    "files_acted_on": sorted(files_acted_on),
                },
            ),
        )

    signals: list[DiagnosticSignal] = []
    for agent_type, candidates in candidates_by_agent.items():
        rate = len(candidates) / invocations_by_agent[agent_type]
        if rate >= MIN_REVIEWER_CAUGHT_RATE:
            signals.extend(candidates)
    return signals


def _file_rework_threshold_for(stats: _FileEditStats) -> int:
    """Effective FILE_REWORK threshold for one file, applying the
    POST_COMPLETION_BOOST (1-step lower with floor of 2) when the file
    received any post-completion edits.
    """
    if POST_COMPLETION_BOOST and stats.post_completion > 0:
        return max(2, _FILE_REWORK_THRESHOLD - 1)
    return _FILE_REWORK_THRESHOLD


def _emit_file_rework_signals(
    edit_stats: dict[str, _FileEditStats],
) -> list[DiagnosticSignal]:
    return [
        DiagnosticSignal(
            signal_type=SignalType.FILE_REWORK,
            severity=Severity.WARNING,
            agent_type=None,
            invocation_id=None,
            message=(
                f"File '{file_path}' edited {stats.count} times in this session"
            ),
            detail={
                "file_path": file_path,
                "edit_count": stats.count,
                "post_completion_edits": stats.post_completion,
                "edit_tools": sorted(stats.tools),
                "completion_scope": "session",
            },
        )
        for file_path, stats in edit_stats.items()
        if stats.count >= _file_rework_threshold_for(stats)
    ]


def extract_quality_signals(
    messages: list[SessionMessage],
    agent_invocations: list[AgentInvocation] | None = None,
) -> list[DiagnosticSignal]:
    """Extract quality-axis signals from parent-thread messages.

    Emits ``USER_CORRECTION`` (#269), ``FILE_REWORK`` (#270), and
    ``REVIEWER_CAUGHT`` (#271). The first two are cross-cutting
    (``agent_type=None``) parent-thread observations; ``REVIEWER_CAUGHT``
    is per-agent (``agent_type`` carries the named review subagent) so
    aggregation can group findings under each review agent rather than
    lumping them globally.

    ``agent_invocations`` drives the REVIEWER_CAUGHT detector; the
    other two detectors only need ``messages``. Pass ``None`` (or omit)
    to skip review-agent analysis when invocations aren't available.

    Returns an empty list when ``messages`` is empty.
    """
    if not messages:
        return []

    last_assistant: tuple[bool, bool] | None = None
    correction_detections: list[
        tuple[CorrectionCategory, str, str, PrecedingAction]
    ] = []
    total_user_messages = 0
    edit_stats: dict[str, _FileEditStats] = {}
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
                stats = edit_stats.setdefault(fp, _FileEditStats())
                stats.count += 1
                stats.tools.add(block.name)
                if completion_seen:
                    stats.post_completion += 1
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

    reviewer_signals = (
        _extract_reviewer_caught_signals(messages, agent_invocations)
        if agent_invocations
        else []
    )
    return [
        *_emit_user_correction_signals(
            correction_detections, total_user_messages,
        ),
        *_emit_file_rework_signals(edit_stats),
        *reviewer_signals,
    ]
