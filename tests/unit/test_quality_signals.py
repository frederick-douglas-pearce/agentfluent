"""Tests for ``diagnostics.quality_signals``.

Covers USER_CORRECTION detection. The 3-tier false-positive heuristic
(strong-correction override / write-tool primary gate / question
suppression) is exercised explicitly so the regressions stay anchored
even when #274 calibration tunes thresholds.
"""

from __future__ import annotations

from agentfluent.agents.models import AgentInvocation
from agentfluent.config.models import Severity
from agentfluent.core.session import ContentBlock, SessionMessage
from agentfluent.diagnostics.models import SignalType
from agentfluent.diagnostics.quality_signals import (
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


def _assistant_with_write_tool(
    text: str = "Editing the file now.", tool_name: str = "Edit",
) -> SessionMessage:
    return SessionMessage(
        type="assistant",
        content_blocks=[
            ContentBlock(type="text", text=text),
            ContentBlock(
                type="tool_use",
                id="toolu_w",
                name=tool_name,
                input={"file_path": "/tmp/x.py"},
            ),
        ],
    )


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
