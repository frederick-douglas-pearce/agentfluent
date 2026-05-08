"""Tests for ``diagnostics.quality_signals``.

Covers USER_CORRECTION detection. The 3-tier false-positive heuristic
(strong-correction override / write-tool primary gate / question
suppression) is exercised explicitly so the regressions stay anchored
even when #274 calibration tunes thresholds.
"""

from __future__ import annotations

from agentfluent.agents.models import WRITE_TOOLS, AgentInvocation
from agentfluent.config.models import Severity
from agentfluent.core.session import ContentBlock, SessionMessage
from agentfluent.diagnostics.models import SignalType
from agentfluent.diagnostics.quality_signals import (
    _EDIT_TOOL_NAMES,
    _FILE_REWORK_THRESHOLD,
    REVIEW_AGENT_TYPES,
    extract_quality_signals,
)


def _user(text: str) -> SessionMessage:
    return SessionMessage(
        type="user",
        content_blocks=[ContentBlock(type="text", text=text)],
    )


def _assistant_text(text: str) -> SessionMessage:
    return SessionMessage(
        type="assistant",
        content_blocks=[ContentBlock(type="text", text=text)],
    )


def _assistant_with_edits(
    *file_paths: str,
    text: str = "",
    tool_name: str = "Edit",
) -> SessionMessage:
    """Assistant message with one Edit tool_use per file_path.

    Optional leading text block when ``text`` is non-empty. Tool block
    ids are unique within the message (``toolu_e0``, ``toolu_e1``, ...)
    so multiple-edit messages don't collide on tool_use_id.
    """
    blocks: list[ContentBlock] = []
    if text:
        blocks.append(ContentBlock(type="text", text=text))
    for i, fp in enumerate(file_paths):
        blocks.append(
            ContentBlock(
                type="tool_use",
                id=f"toolu_e{i}",
                name=tool_name,
                input={"file_path": fp},
            ),
        )
    return SessionMessage(type="assistant", content_blocks=blocks)


def _assistant_with_write_tool(
    text: str = "Editing the file now.", tool_name: str = "Edit",
) -> SessionMessage:
    """Single-file convenience wrapper around ``_assistant_with_edits``."""
    return _assistant_with_edits("/tmp/x.py", text=text, tool_name=tool_name)


class TestUserCorrectionDetection:
    def test_three_corrections_in_ten_user_messages_emits_three_signals(self) -> None:
        """AC fixture: 3 corrections in 10 user messages -> 3 signals."""
        messages: list[SessionMessage] = []
        # 7 non-correction user messages (preceded by a non-write assistant
        # text) interleaved with 3 corrections (preceded by write tools).
        for i in range(7):
            messages.append(_assistant_text(f"Step {i} complete."))
            messages.append(_user(f"continue with step {i + 1}"))
        for i in range(3):
            messages.append(_assistant_with_write_tool(f"Edited file {i}"))
            messages.append(_user(f"no, do something different ({i})"))

        signals = extract_quality_signals(messages)

        assert len(signals) == 3
        assert all(s.signal_type == SignalType.USER_CORRECTION for s in signals)
        # session_correction_rate stamped on every signal: 3 / 10 = 0.3
        rate = signals[0].detail["session_correction_rate"]
        assert isinstance(rate, float)
        assert rate == 0.3
        for s in signals:
            assert s.detail["total_user_messages"] == 10

    def test_zero_corrections_returns_empty(self) -> None:
        """AC fixture: session with 0 corrections does not fire."""
        messages = [
            _assistant_text("Step one done."),
            _user("looks good, please continue"),
            _assistant_text("Step two done."),
            _user("great, keep going"),
        ]
        assert extract_quality_signals(messages) == []

    def test_no_answer_to_question_does_not_fire(self) -> None:
        """AC fixture: 'no' as an answer to a question is not a correction."""
        messages = [
            _assistant_text("Should I keep going with approach A?"),
            _user("no, let's stop here for now"),
        ]
        assert extract_quality_signals(messages) == []

    def test_strong_correction_fires_after_question(self) -> None:
        """Strong-correction patterns override question suppression.

        Even when the preceding assistant message is question-only, a
        strong phrase like 'that's wrong' is the user overruling the
        system, not answering the question.
        """
        messages = [
            _assistant_text("Did you want me to delete the file?"),
            _user("that's wrong, we agreed not to delete anything"),
        ]
        signals = extract_quality_signals(messages)
        assert len(signals) == 1
        assert signals[0].detail["matched_category"] == "strong"
        assert signals[0].detail["preceding_assistant_action"] == "question"

    def test_revert_fires_regardless_of_preceding(self) -> None:
        """``revert`` is a strong-correction phrase. Even after a plain
        text assistant message (no write tool, not a question), it fires."""
        messages = [
            _assistant_text("Here is the plan we discussed."),
            _user("revert that change please"),
        ]
        signals = extract_quality_signals(messages)
        assert len(signals) == 1
        assert signals[0].detail["matched_category"] == "strong"

    def test_soft_pattern_after_write_tool_fires(self) -> None:
        """Primary gate: soft patterns fire when preceding message used a write tool."""
        messages = [
            _assistant_with_write_tool(),
            _user("actually, I wanted you to edit a different file"),
        ]
        signals = extract_quality_signals(messages)
        assert len(signals) == 1
        assert signals[0].detail["matched_category"] == "redirection"
        assert signals[0].detail["preceding_assistant_action"] == "write_tool"

    def test_soft_pattern_without_write_tool_or_question_does_not_fire(self) -> None:
        """Without the primary gate (no write tool) and not a question
        either, soft patterns are suppressed. Only strong-correction
        survives this case."""
        messages = [
            _assistant_text("Let me think about this."),
            _user("instead, please consider option B"),
        ]
        assert extract_quality_signals(messages) == []

    def test_multi_edit_treated_as_write_tool(self) -> None:
        """``MultiEdit`` is in WRITE_TOOLS (added in #269) so corrections
        following a MultiEdit message fire under the primary gate."""
        messages = [
            _assistant_with_write_tool(tool_name="MultiEdit"),
            _user("no, that's wrong"),
        ]
        signals = extract_quality_signals(messages)
        assert len(signals) == 1

    def test_first_user_message_no_preceding(self) -> None:
        """With no preceding assistant message, only strong-correction
        patterns can fire (no write_tool, not a question)."""
        soft_only = [_user("actually, do X instead")]
        assert extract_quality_signals(soft_only) == []

        strong_only = [_user("that's wrong")]
        assert len(extract_quality_signals(strong_only)) == 1

    def test_empty_messages_returns_empty(self) -> None:
        assert extract_quality_signals([]) == []

    def test_user_message_without_text_skipped(self) -> None:
        """User messages whose content is purely tool_result blocks
        (no text) are not user prose and must not be scanned."""
        messages = [
            _assistant_with_write_tool(),
            SessionMessage(
                type="user",
                content_blocks=[
                    ContentBlock(
                        type="tool_result",
                        tool_use_id="toolu_w",
                        text="ok",
                    ),
                ],
            ),
        ]
        assert extract_quality_signals(messages) == []

    def test_no_match_after_write_tool_returns_no_signal(self) -> None:
        """Primary gate is open (write tool present) but no correction
        pattern hits — benign user message after a write tool produces
        no signal. Counts toward total_user_messages denominator only
        if a correction is detected later."""
        messages = [
            _assistant_with_write_tool(),
            _user("looks great, please continue"),
        ]
        assert extract_quality_signals(messages) == []

    def test_skips_unknown_message_types(self) -> None:
        """Defensive guard: messages whose ``type`` is neither 'user'
        nor 'assistant' are silently skipped without affecting state."""
        messages = [
            _assistant_with_write_tool(),
            SessionMessage(type="system", content_blocks=[]),
            _user("no, that's wrong"),
        ]
        signals = extract_quality_signals(messages)
        assert len(signals) == 1
        assert signals[0].detail["preceding_assistant_action"] == "write_tool"

    def test_skips_intervening_non_assistant_messages(self) -> None:
        """Look-back finds the most recent assistant, skipping any
        intervening tool-result-only user messages."""
        messages = [
            _assistant_with_write_tool(),
            # Tool-result-only user message (no text) — must be skipped
            # by the look-back so the write-tool primary gate still
            # applies to the next user prose.
            SessionMessage(
                type="user",
                content_blocks=[
                    ContentBlock(
                        type="tool_result",
                        tool_use_id="toolu_w",
                        text="ok",
                    ),
                ],
            ),
            _user("actually, let's try something else"),
        ]
        signals = extract_quality_signals(messages)
        assert len(signals) == 1
        assert signals[0].detail["preceding_assistant_action"] == "write_tool"

    def test_emitted_signal_shape(self) -> None:
        """Verify cross-cutting attribution and required detail keys."""
        messages = [
            _assistant_with_write_tool(),
            _user("no, that's wrong"),
        ]
        signals = extract_quality_signals(messages)
        assert len(signals) == 1
        sig = signals[0]
        assert sig.severity == Severity.WARNING
        assert sig.agent_type is None
        assert sig.invocation_id is None
        for key in (
            "correction_text",
            "matched_pattern",
            "matched_category",
            "preceding_assistant_action",
            "session_correction_rate",
            "total_user_messages",
        ):
            assert key in sig.detail

    def test_long_correction_text_truncated(self) -> None:
        """Snippet stamped on detail is capped to 140 chars."""
        long_text = "no, " + ("very long context " * 20)
        messages = [
            _assistant_with_write_tool(),
            _user(long_text),
        ]
        signals = extract_quality_signals(messages)
        snippet = signals[0].detail["correction_text"]
        assert isinstance(snippet, str)
        assert len(snippet) <= 141  # 140 + ellipsis


class TestUserCorrectionSystemWrapperStripping:
    """System-injected wrappers (#321) are stripped before pattern
    matching. Trigger words inside ``<task-notification>``,
    ``<system-reminder>``, or the session-resumption preamble must not
    fire USER_CORRECTION — the dogfood corpus had ~85% FP rate before
    these were filtered."""

    def test_task_notification_wrapper_does_not_fire(self) -> None:
        """A user message that is purely a ``<task-notification>``
        wrapper containing trigger words must not fire — even with the
        write-tool primary gate open. Two such messages would be
        suppressed, so the OR-gate (count >= 2) cannot lift them."""
        wrapped = (
            "<task-notification><task-id>abc</task-id>"
            "<status>stop the world, revert everything</status>"
            "</task-notification>"
        )
        messages = [
            _assistant_with_write_tool(),
            _user(wrapped),
            _assistant_with_write_tool(),
            _user(wrapped),
        ]
        assert extract_quality_signals(messages) == []

    def test_system_reminder_wrapper_does_not_fire(self) -> None:
        """``<system-reminder>`` blocks are stripped — the multi-line
        wrapper from real Claude Code sessions must not surface even
        when it contains a trigger word like 'wait'."""
        wrapped = (
            "<system-reminder>\n"
            "Please wait for the user to confirm before proceeding.\n"
            "</system-reminder>"
        )
        messages = [
            _assistant_with_write_tool(),
            _user(wrapped),
            _assistant_with_write_tool(),
            _user(wrapped),
        ]
        assert extract_quality_signals(messages) == []

    def test_session_resumption_preamble_does_not_fire(self) -> None:
        """Claude Code's session-resumption preamble can contain
        trigger words ("revert", "stop", "instead") in the recap. The
        preamble is stripped from its known opening sentence up to the
        next blank line."""
        preamble = (
            "This session is being continued from a previous conversation "
            "that ran out of context. The user asked me to revert the "
            "earlier change and stop using the deprecated API instead.\n\n"
            "actual user prose continues here."
        )
        messages = [
            _assistant_with_write_tool(),
            _user(preamble),
            _assistant_with_write_tool(),
            _user(preamble),
        ]
        # "actual user prose continues here." has no trigger word, so no fire.
        assert extract_quality_signals(messages) == []

    def test_real_user_text_after_stripped_wrapper_still_fires(self) -> None:
        """When a user message contains both a system wrapper AND
        genuine correction prose, only the wrapper is stripped — the
        real correction still fires."""
        mixed = (
            "<task-notification><status>ok</status></task-notification>"
            "no, that's wrong"
        )
        messages = [
            _assistant_with_write_tool(),
            _user(mixed),
        ]
        signals = extract_quality_signals(messages)
        assert len(signals) == 1
        assert signals[0].detail["matched_category"] == "strong"

    def test_pure_wrapper_message_not_counted_in_denominator(self) -> None:
        """A message whose stripped text is empty must not inflate
        ``total_user_messages`` — wrapper-only messages aren't user
        prose and shouldn't dilute the correction-rate denominator."""
        messages: list[SessionMessage] = [
            _assistant_with_write_tool(),
            _user("<system-reminder>noop</system-reminder>"),
            _assistant_with_write_tool(),
            _user("no, that's wrong"),
        ]
        # Without stripping, total_user_messages would be 2 with 1
        # correction (rate=0.5, count=1). With stripping, only the real
        # correction message counts: total=1, rate=1.0, count=1.
        signals = extract_quality_signals(messages)
        assert len(signals) == 1
        assert signals[0].detail["total_user_messages"] == 1
        assert signals[0].detail["session_correction_rate"] == 1.0


class TestUserCorrectionTightenedPatterns:
    """Tightened soft patterns (#321): interruption requires imperative
    anchoring, ``instead`` requires an imperative continuation."""

    def test_wait_inside_question_does_not_fire(self) -> None:
        """``\\bwait\\b`` previously matched mid-sentence trigger words
        even after a write-tool message. Imperative-anchored form
        suppresses 'can you wait until I finish?'-style prose."""
        messages = [
            _assistant_with_write_tool(),
            _user("can you wait until I finish reviewing the diff?"),
        ]
        assert extract_quality_signals(messages) == []

    def test_wait_at_sentence_start_still_fires(self) -> None:
        """Imperative ``wait`` at start-of-message still fires under
        the new pattern."""
        messages = [
            _assistant_with_write_tool(),
            _user("wait, let me re-check the spec first"),
        ]
        signals = extract_quality_signals(messages)
        assert len(signals) == 1
        assert signals[0].detail["matched_category"] == "interruption"

    def test_stop_after_sentence_boundary_fires(self) -> None:
        """Sentence-internal trigger words still fire after a
        ``.``/``!``/``?`` boundary — preserves the imperative form."""
        messages = [
            _assistant_with_write_tool(),
            _user("Let me think. Stop the edit, I want to review first."),
        ]
        signals = extract_quality_signals(messages)
        assert len(signals) == 1
        assert signals[0].detail["matched_category"] == "interruption"

    def test_please_stop_fires(self) -> None:
        """Polite-prefix ``please stop`` is still imperative."""
        messages = [
            _assistant_with_write_tool(),
            _user("please stop editing that file"),
        ]
        signals = extract_quality_signals(messages)
        assert len(signals) == 1

    def test_instead_at_sentence_end_does_not_fire(self) -> None:
        """``instead`` with no imperative continuation is suggestion or
        comparison prose — it must not fire (#321 case #5 trade-off)."""
        messages = [
            _assistant_with_write_tool(),
            _user(
                "I think it would be better to get the list of acceptable "
                "labels from a database query instead.",
            ),
        ]
        assert extract_quality_signals(messages) == []

    def test_instead_of_fires(self) -> None:
        """``instead of <X>`` is the canonical imperative redirection."""
        messages = [
            _assistant_with_write_tool(),
            _user("instead of using a list, let's use a dict here"),
        ]
        signals = extract_quality_signals(messages)
        assert len(signals) == 1
        assert signals[0].detail["matched_category"] == "redirection"

    def test_instead_comma_please_fires(self) -> None:
        """``instead, please`` is a polite imperative redirection."""
        messages = [
            _assistant_with_write_tool(),
            _user("instead, please consider option B before proceeding"),
        ]
        signals = extract_quality_signals(messages)
        assert len(signals) == 1
        assert signals[0].detail["matched_category"] == "redirection"


class TestExtractQualitySignalsSignature:
    """Signature contract is locked per architect blocker on #269.

    ``agent_invocations`` is unused by USER_CORRECTION detection but
    must be accepted so #271 (REVIEWER_CAUGHT) can plug in without a
    signature break."""

    def test_agent_invocations_accepted_and_ignored(self) -> None:
        messages = [
            _assistant_with_write_tool(),
            _user("no, that's wrong"),
        ]
        invocations = [
            AgentInvocation(
                agent_type="architect",
                description="d",
                prompt="p",
                tool_use_id="toolu_a",
            ),
        ]
        with_invocations = extract_quality_signals(messages, invocations)
        without_invocations = extract_quality_signals(messages)
        assert len(with_invocations) == len(without_invocations) == 1

    def test_review_agent_types_constant_present(self) -> None:
        assert REVIEW_AGENT_TYPES >= {
            "architect", "code-reviewer", "tester", "security-review",
        }


class TestFileReworkDetection:
    """``FILE_REWORK`` fires when a single file is edited at or above
    ``_FILE_REWORK_THRESHOLD`` (default 4) within one session. Detection
    is cross-cutting — ``agent_type=None``."""

    def test_fires_at_threshold(self) -> None:
        messages = [
            _assistant_with_edits("/src/foo.py")
            for _ in range(_FILE_REWORK_THRESHOLD)
        ]
        signals = extract_quality_signals(messages)
        rework = [s for s in signals if s.signal_type == SignalType.FILE_REWORK]
        assert len(rework) == 1
        sig = rework[0]
        assert sig.agent_type is None
        assert sig.detail["file_path"] == "/src/foo.py"
        assert sig.detail["edit_count"] == _FILE_REWORK_THRESHOLD
        assert sig.detail["post_completion_edits"] == 0
        assert sig.detail["completion_scope"] == "session"

    def test_below_threshold_does_not_fire(self) -> None:
        messages = [
            _assistant_with_edits("/src/foo.py")
            for _ in range(_FILE_REWORK_THRESHOLD - 1)
        ]
        signals = extract_quality_signals(messages)
        assert not any(
            s.signal_type == SignalType.FILE_REWORK for s in signals
        )

    def test_post_completion_edits_counted(self) -> None:
        """Edits after completion language are still tallied on ``detail``
        even when ``POST_COMPLETION_BOOST`` is disabled — the field
        itself remains useful for downstream analysis."""
        n = _FILE_REWORK_THRESHOLD
        # Spread n edits across pre/post-completion messages: 2 before,
        # n-2 after a completion phrase. The signal still fires (count
        # >= threshold) and detail.post_completion_edits reflects the
        # count after the flag flipped.
        messages = [
            *(_assistant_with_edits("/src/foo.py") for _ in range(2)),
            _assistant_with_edits("/src/foo.py", text="all done with this"),
            *(_assistant_with_edits("/src/foo.py") for _ in range(n - 3)),
        ]
        signals = extract_quality_signals(messages)
        rework = [s for s in signals if s.signal_type == SignalType.FILE_REWORK]
        assert len(rework) == 1
        # 1 (the completion-language message itself) + (n - 3) after = n - 2.
        assert rework[0].detail["post_completion_edits"] == n - 2

    def test_multiple_files_independent(self) -> None:
        """Each file is evaluated against the threshold independently."""
        # /a.py: edited above threshold; /b.py: edited just twice.
        a_edits = _FILE_REWORK_THRESHOLD
        messages = (
            [_assistant_with_edits("/a.py", "/b.py") for _ in range(2)]
            + [_assistant_with_edits("/a.py") for _ in range(a_edits - 2)]
        )
        signals = extract_quality_signals(messages)
        rework = {
            s.detail["file_path"]: s for s in signals
            if s.signal_type == SignalType.FILE_REWORK
        }
        assert "/a.py" in rework
        assert "/b.py" not in rework

    def test_multi_edit_counted(self) -> None:
        """``MultiEdit`` is in ``_EDIT_TOOL_NAMES`` and contributes."""
        messages = [
            _assistant_with_edits("/x.py", tool_name="MultiEdit")
            for _ in range(_FILE_REWORK_THRESHOLD)
        ]
        signals = extract_quality_signals(messages)
        rework = [s for s in signals if s.signal_type == SignalType.FILE_REWORK]
        assert len(rework) == 1
        assert "MultiEdit" in rework[0].detail["edit_tools"]

    def test_bash_ignored(self) -> None:
        """``Bash`` is excluded from ``_EDIT_TOOL_NAMES`` (no
        single ``file_path`` input — shell-command parsing is out of
        scope for FILE_REWORK)."""
        messages = [
            SessionMessage(
                type="assistant",
                content_blocks=[
                    ContentBlock(
                        type="tool_use",
                        id=f"toolu_b{i}",
                        name="Bash",
                        input={"command": "echo hello"},
                    ),
                ],
            )
            for i in range(5)
        ]
        signals = extract_quality_signals(messages)
        assert not any(
            s.signal_type == SignalType.FILE_REWORK for s in signals
        )

    def test_missing_file_path_skipped(self) -> None:
        """An Edit block whose ``input`` lacks ``file_path`` (or has a
        non-string value) is silently skipped — no ``None``-keyed entries
        in the per-file count dict."""
        messages = [
            SessionMessage(
                type="assistant",
                content_blocks=[
                    ContentBlock(
                        type="tool_use",
                        id=f"toolu_m{i}",
                        name="Edit",
                        input={},  # no file_path
                    ),
                ],
            )
            for i in range(5)
        ]
        signals = extract_quality_signals(messages)
        assert not any(
            s.signal_type == SignalType.FILE_REWORK for s in signals
        )

    def test_non_string_file_path_skipped(self) -> None:
        messages = [
            SessionMessage(
                type="assistant",
                content_blocks=[
                    ContentBlock(
                        type="tool_use",
                        id=f"toolu_n{i}",
                        name="Edit",
                        input={"file_path": 42},  # type: ignore[dict-item]
                    ),
                ],
            )
            for i in range(5)
        ]
        signals = extract_quality_signals(messages)
        assert not any(
            s.signal_type == SignalType.FILE_REWORK for s in signals
        )


class TestEditToolNamesContract:
    """Drift-prevention: ``_EDIT_TOOL_NAMES`` is a strict subset of
    ``WRITE_TOOLS``. If ``WRITE_TOOLS`` ever gains a new file-path-bearing
    tool, this test fails as a reminder to consider extending the
    rework detector to include it."""

    def test_edit_tool_names_subset_of_write_tools(self) -> None:
        assert _EDIT_TOOL_NAMES <= WRITE_TOOLS


def _review_invocation(
    agent_type: str = "architect",
    output_text: str = "",
    tool_use_id: str = "toolu_review",
) -> AgentInvocation:
    return AgentInvocation(
        agent_type=agent_type,
        description="review",
        prompt="review the diff",
        tool_use_id=tool_use_id,
        output_text=output_text,
    )


def _user_with_tool_result(
    tool_use_id: str, content: str = "review complete",
) -> SessionMessage:
    return SessionMessage(
        type="user",
        content_blocks=[
            ContentBlock(
                type="tool_result", tool_use_id=tool_use_id, text=content,
            ),
        ],
    )


_SUBSTANTIVE_REVIEW = (
    "I reviewed the change and found several blocker issues that must "
    "be addressed before merge. First concern: the new function in "
    "src/foo.py does not handle the empty-input case and will raise "
    "an unexpected exception at runtime. Second issue: a security risk "
    "in the auth flow — credentials are logged at debug level which "
    "is a real vulnerability if log levels are misconfigured. Third "
    "warning: the test fixture in tests/test_foo.py mocks behavior "
    "that contradicts the production code path; the test should "
    "exercise the real implementation. Recommended fix: add input "
    "validation, redact credentials before logging, and rewrite the "
    "fixture against the real code path."
)


class TestReviewerCaughtDetection:
    """REVIEWER_CAUGHT fires when a review-style subagent (architect,
    code-reviewer, security-review, tester) produces a substantive
    response (>= 500 chars + finding keywords). Per-agent attribution
    — ``agent_type`` carries the named review agent."""

    def test_substantive_review_fires(self) -> None:
        inv = _review_invocation(output_text=_SUBSTANTIVE_REVIEW)
        signals = extract_quality_signals(
            messages=[_user_with_tool_result(inv.tool_use_id)],
            agent_invocations=[inv],
        )
        rev = [s for s in signals if s.signal_type == SignalType.REVIEWER_CAUGHT]
        assert len(rev) == 1
        sig = rev[0]
        assert sig.agent_type == "architect"
        assert len(sig.detail["finding_keywords"]) >= 1
        assert "blocker" in sig.detail["finding_keywords"]

    def test_lgtm_pass_does_not_fire(self) -> None:
        inv = _review_invocation(output_text="LGTM, no concerns.")
        signals = extract_quality_signals(
            messages=[_user_with_tool_result(inv.tool_use_id)],
            agent_invocations=[inv],
        )
        assert not any(
            s.signal_type == SignalType.REVIEWER_CAUGHT for s in signals
        )

    def test_long_response_without_keywords_does_not_fire(self) -> None:
        inv = _review_invocation(output_text="x" * 1000)
        signals = extract_quality_signals(
            messages=[_user_with_tool_result(inv.tool_use_id)],
            agent_invocations=[inv],
        )
        assert not any(
            s.signal_type == SignalType.REVIEWER_CAUGHT for s in signals
        )

    def test_parent_acted_when_mentioned_file_subsequently_edited(self) -> None:
        inv = _review_invocation(output_text=_SUBSTANTIVE_REVIEW)
        messages = [
            _user_with_tool_result(inv.tool_use_id),
            _assistant_with_edits("src/foo.py"),
        ]
        signals = extract_quality_signals(messages, [inv])
        rev = next(s for s in signals if s.signal_type == SignalType.REVIEWER_CAUGHT)
        assert rev.detail["parent_acted"] is True
        assert "src/foo.py" in rev.detail["files_acted_on"]

    def test_parent_not_acted_when_no_followup_edits(self) -> None:
        inv = _review_invocation(output_text=_SUBSTANTIVE_REVIEW)
        signals = extract_quality_signals(
            messages=[_user_with_tool_result(inv.tool_use_id)],
            agent_invocations=[inv],
        )
        rev = next(s for s in signals if s.signal_type == SignalType.REVIEWER_CAUGHT)
        assert rev.detail["parent_acted"] is False

    def test_parent_acted_window_caps_lookforward(self) -> None:
        """Edits beyond the look-forward window do not count toward
        ``parent_acted``. Defends against attributing unrelated edits
        100+ messages later to a review (architect concern #2)."""
        inv = _review_invocation(output_text=_SUBSTANTIVE_REVIEW)
        messages: list[SessionMessage] = [_user_with_tool_result(inv.tool_use_id)]
        for i in range(20):
            messages.append(_assistant_text(f"step {i}"))
        messages.append(_assistant_with_edits("src/foo.py"))
        signals = extract_quality_signals(messages, [inv])
        rev = next(s for s in signals if s.signal_type == SignalType.REVIEWER_CAUGHT)
        assert rev.detail["parent_acted"] is False

    def test_built_in_code_reviewer_fires(self) -> None:
        inv = _review_invocation(
            agent_type="code-reviewer", output_text=_SUBSTANTIVE_REVIEW,
        )
        signals = extract_quality_signals(
            messages=[_user_with_tool_result(inv.tool_use_id)],
            agent_invocations=[inv],
        )
        rev = [s for s in signals if s.signal_type == SignalType.REVIEWER_CAUGHT]
        assert len(rev) == 1
        assert rev[0].agent_type == "code-reviewer"

    def test_non_review_agent_ignored(self) -> None:
        inv = _review_invocation(
            agent_type="general-purpose", output_text=_SUBSTANTIVE_REVIEW,
        )
        signals = extract_quality_signals(
            messages=[_user_with_tool_result(inv.tool_use_id)],
            agent_invocations=[inv],
        )
        assert not any(
            s.signal_type == SignalType.REVIEWER_CAUGHT for s in signals
        )

    def test_no_invocations_yields_no_reviewer_signals(self) -> None:
        """When ``agent_invocations`` is None or empty, the detector
        is silently skipped."""
        signals = extract_quality_signals(messages=[], agent_invocations=None)
        assert signals == []

    def test_files_mentioned_uses_strict_pattern(self) -> None:
        """``v1.0``, ``github.com``, ``section 3.b`` should NOT be
        classified as files (architect concern #1 on #271)."""
        review = _SUBSTANTIVE_REVIEW + (
            " See v1.0 release notes at github.com/foo/bar — "
            "section 3.b for context."
        )
        inv = _review_invocation(output_text=review)
        signals = extract_quality_signals(
            messages=[_user_with_tool_result(inv.tool_use_id)],
            agent_invocations=[inv],
        )
        rev = next(s for s in signals if s.signal_type == SignalType.REVIEWER_CAUGHT)
        assert "src/foo.py" in rev.detail["files_mentioned"]
        assert "tests/test_foo.py" in rev.detail["files_mentioned"]
        assert "v1.0" not in rev.detail["files_mentioned"]
        assert "3.b" not in rev.detail["files_mentioned"]
        assert "github.com" not in rev.detail["files_mentioned"]

    def test_parent_acted_skips_user_messages_between(self) -> None:
        """Look-forward over assistant edits ignores intervening user
        messages — defensive ``continue`` branch."""
        inv = _review_invocation(output_text=_SUBSTANTIVE_REVIEW)
        messages: list[SessionMessage] = [
            _user_with_tool_result(inv.tool_use_id),
            _user("interjection from the user"),
            _assistant_with_edits("src/foo.py"),
        ]
        signals = extract_quality_signals(messages, [inv])
        rev = next(s for s in signals if s.signal_type == SignalType.REVIEWER_CAUGHT)
        assert rev.detail["parent_acted"] is True

    def test_parent_acted_ignores_non_edit_tool_blocks(self) -> None:
        """Bash and other non-edit tools in the look-forward window
        do not count toward ``files_acted_on``."""
        inv = _review_invocation(output_text=_SUBSTANTIVE_REVIEW)
        messages = [
            _user_with_tool_result(inv.tool_use_id),
            SessionMessage(
                type="assistant",
                content_blocks=[
                    ContentBlock(
                        type="tool_use",
                        id="toolu_b",
                        name="Bash",
                        input={"command": "ls src/"},
                    ),
                ],
            ),
        ]
        signals = extract_quality_signals(messages, [inv])
        rev = next(s for s in signals if s.signal_type == SignalType.REVIEWER_CAUGHT)
        assert rev.detail["parent_acted"] is False

    def test_parent_acted_relative_mention_matches_absolute_edit(self) -> None:
        """Review prose carries relative paths (``src/foo.py``); Edit
        tool calls carry absolute paths (``/home/u/repo/src/foo.py``).
        Suffix-match bridges the two — without it ``parent_acted`` is
        always False on real Claude Code data (#322)."""
        inv = _review_invocation(output_text=_SUBSTANTIVE_REVIEW)
        messages = [
            _user_with_tool_result(inv.tool_use_id),
            _assistant_with_edits("/home/u/repo/src/foo.py"),
        ]
        signals = extract_quality_signals(messages, [inv])
        rev = next(s for s in signals if s.signal_type == SignalType.REVIEWER_CAUGHT)
        assert rev.detail["parent_acted"] is True
        assert "src/foo.py" in rev.detail["files_acted_on"]

    def test_parent_acted_bare_filename_matches_absolute_edit(self) -> None:
        """Bare-filename mentions (``quality_signals.py``) match any
        absolute edit whose basename matches — verifies the suffix
        match handles the no-directory case via ``e.endswith("/" + m)``."""
        review = (
            "I reviewed the change and found a blocker issue that must "
            "be addressed before merge. The function in quality_signals.py "
            "does not handle the empty-input case and will raise an "
            "unexpected exception at runtime. This is a real risk to "
            "production behavior under any code path that funnels "
            "user-supplied data into this function without a prior "
            "non-empty check. Recommended fix: add input validation "
            "and a defensive guard before the loop. Additional concern: "
            "the test fixture is missing for this path — please add "
            "coverage for the empty-input case so this regression cannot "
            "recur silently. A second issue worth flagging is that the "
            "error message itself does not include the offending field "
            "name, which makes downstream debugging harder than it "
            "needs to be; that should be tightened up too."
        )
        inv = _review_invocation(output_text=review)
        messages = [
            _user_with_tool_result(inv.tool_use_id),
            _assistant_with_edits(
                "/home/u/repo/src/agentfluent/diagnostics/quality_signals.py",
            ),
        ]
        signals = extract_quality_signals(messages, [inv])
        rev = next(s for s in signals if s.signal_type == SignalType.REVIEWER_CAUGHT)
        assert rev.detail["parent_acted"] is True
        assert "quality_signals.py" in rev.detail["files_acted_on"]

    def test_parent_acted_false_when_no_filename_overlap(self) -> None:
        """Suffix match still gates on filename — an edit to an
        entirely unrelated file (``unrelated_module.py``) does NOT
        satisfy ``parent_acted`` even when other mentioned files exist
        in the review prose. Guards against the regression where the
        suffix change accidentally makes everything fire."""
        inv = _review_invocation(output_text=_SUBSTANTIVE_REVIEW)
        messages = [
            _user_with_tool_result(inv.tool_use_id),
            _assistant_with_edits("/home/u/repo/src/unrelated_module.py"),
        ]
        signals = extract_quality_signals(messages, [inv])
        rev = next(s for s in signals if s.signal_type == SignalType.REVIEWER_CAUGHT)
        assert rev.detail["parent_acted"] is False


class TestUserCorrectionEmissionGates:
    """OR-gated session-level gates on USER_CORRECTION (#274 calibration).

    Disjunctive: fires if count >= ``MIN_CORRECTIONS_PER_SESSION``
    OR session_correction_rate >= ``MIN_CORRECTION_RATE``. PM-revised
    from the originally proposed AND so a long session with two strong
    corrections still surfaces.
    """

    @staticmethod
    def _user_corrections(signals: list) -> list:
        return [s for s in signals if s.signal_type == SignalType.USER_CORRECTION]

    def test_below_both_floors_suppresses(self) -> None:
        # 1 correction in 30 user messages: count=1 < 2, rate=0.033 < 0.10.
        messages: list[SessionMessage] = []
        for i in range(29):
            messages.append(_assistant_text(f"point {i}"))
            messages.append(_user(f"continue {i}"))
        messages.append(_assistant_text("here is the plan"))
        messages.append(_user("revert that change please"))
        assert self._user_corrections(extract_quality_signals(messages)) == []

    def test_count_floor_fires_with_low_rate(self) -> None:
        # 2 corrections in 30 user messages: count=2 (gate), rate=0.067 (no gate).
        messages: list[SessionMessage] = []
        for i in range(28):
            messages.append(_assistant_text(f"point {i}"))
            messages.append(_user(f"continue {i}"))
        for _ in range(2):
            messages.append(_assistant_text("here is the plan"))
            messages.append(_user("revert that change please"))
        signals = self._user_corrections(extract_quality_signals(messages))
        assert len(signals) == 2

    def test_rate_floor_fires_with_low_count(self) -> None:
        # 1 correction in 5 user messages: count=1 (no gate), rate=0.20 (gate).
        messages: list[SessionMessage] = []
        for i in range(4):
            messages.append(_assistant_text(f"point {i}"))
            messages.append(_user(f"continue {i}"))
        messages.append(_assistant_text("here is the plan"))
        messages.append(_user("revert that change please"))
        signals = self._user_corrections(extract_quality_signals(messages))
        assert len(signals) == 1


class TestFileReworkPostCompletionBoost:
    """``POST_COMPLETION_BOOST`` is disabled by default after #274
    calibration — completion-language phrases are too common in
    normal dev prose for the boost to be meaningful. These tests
    pin the off-by-default behavior so a future change to the flag
    surfaces here. The boost mechanism itself (lower threshold by
    1, floored at 2) is exercised via patching when re-enabled."""

    def test_no_boost_at_default_when_below_threshold(self) -> None:
        # threshold-1 edits with completion language → no signal.
        n = _FILE_REWORK_THRESHOLD - 1
        messages = [
            _assistant_with_edits("/src/foo.py") for _ in range(n - 1)
        ] + [
            _assistant_with_edits("/src/foo.py", text="all done with this"),
        ]
        signals = extract_quality_signals(messages)
        assert not any(
            s.signal_type == SignalType.FILE_REWORK for s in signals
        )

    def test_boost_helper_lowers_threshold_when_enabled(self) -> None:
        # Patch the module-level flag to True; threshold-1 edits then fire.
        from agentfluent.diagnostics import quality_signals as qs
        original = qs.POST_COMPLETION_BOOST
        qs.POST_COMPLETION_BOOST = True
        try:
            n = _FILE_REWORK_THRESHOLD - 1
            messages = [
                _assistant_with_edits("/src/foo.py")
                for _ in range(n - 1)
            ] + [
                _assistant_with_edits("/src/foo.py", text="all done with this"),
            ]
            signals = extract_quality_signals(messages)
            rework = [
                s for s in signals if s.signal_type == SignalType.FILE_REWORK
            ]
            assert len(rework) == 1
            assert rework[0].detail["edit_count"] == n
        finally:
            qs.POST_COMPLETION_BOOST = original


class TestReviewerCaughtRateGate:
    """Per-(session, agent_type) ``MIN_REVIEWER_CAUGHT_RATE`` gate
    suppresses signals from review agents whose substantive-finding
    fraction is below the threshold within the session."""

    def test_low_rate_suppresses_all_signals(self) -> None:
        # 1 substantive of 10 invocations → rate 0.1 < 0.5, suppressed.
        substantive = _review_invocation(
            output_text=_SUBSTANTIVE_REVIEW, tool_use_id="toolu_sub",
        )
        noise = [
            _review_invocation(output_text="LGTM", tool_use_id=f"toolu_n{i}")
            for i in range(9)
        ]
        invocations = [substantive, *noise]
        messages = [
            _user_with_tool_result(inv.tool_use_id) for inv in invocations
        ]
        signals = extract_quality_signals(messages, invocations)
        assert not any(
            s.signal_type == SignalType.REVIEWER_CAUGHT for s in signals
        )

    def test_high_rate_passes_all_substantive(self) -> None:
        # 3 substantive of 5 invocations → rate 0.6 >= 0.5, all 3 fire.
        invocations = [
            _review_invocation(
                output_text=_SUBSTANTIVE_REVIEW,
                tool_use_id=f"toolu_sub{i}",
            )
            for i in range(3)
        ] + [
            _review_invocation(output_text="LGTM", tool_use_id=f"toolu_n{i}")
            for i in range(2)
        ]
        messages = [
            _user_with_tool_result(inv.tool_use_id) for inv in invocations
        ]
        signals = extract_quality_signals(messages, invocations)
        rev = [s for s in signals if s.signal_type == SignalType.REVIEWER_CAUGHT]
        assert len(rev) == 3

    def test_rate_gate_applies_per_agent_type(self) -> None:
        # architect: 1 substantive of 1 invocation → rate 1.0, fires.
        # tester: 0 substantive of 2 invocations → no candidates, no signals.
        invocations = [
            _review_invocation(
                agent_type="architect",
                output_text=_SUBSTANTIVE_REVIEW,
                tool_use_id="toolu_arch",
            ),
            _review_invocation(
                agent_type="tester",
                output_text="LGTM",
                tool_use_id="toolu_test1",
            ),
            _review_invocation(
                agent_type="tester",
                output_text="LGTM",
                tool_use_id="toolu_test2",
            ),
        ]
        messages = [
            _user_with_tool_result(inv.tool_use_id) for inv in invocations
        ]
        signals = extract_quality_signals(messages, invocations)
        rev = [s for s in signals if s.signal_type == SignalType.REVIEWER_CAUGHT]
        assert len(rev) == 1
        assert rev[0].agent_type == "architect"


class TestMinFindingKeywords:
    """``MIN_FINDING_KEYWORDS`` defaults to 1 — preserves existing
    behavior. Exposed as a module constant so #274 calibration can
    sweep ``{1, 2, 3}``."""

    def test_default_one_keyword_passes(self) -> None:
        text = (
            "I reviewed the file. " * 30
            + " There is one issue with the implementation."
        )
        inv = _review_invocation(output_text=text)
        signals = extract_quality_signals(
            messages=[_user_with_tool_result(inv.tool_use_id)],
            agent_invocations=[inv],
        )
        rev = [s for s in signals if s.signal_type == SignalType.REVIEWER_CAUGHT]
        assert len(rev) == 1
        assert rev[0].detail["finding_keywords"] == ["issue"]
