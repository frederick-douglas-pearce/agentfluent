"""Tests for the verbosity-constraint scanner (C-006a) in config scoring.

Covers the advisory word-count-constraint detector added to
``_score_prompt_body`` per issue #437: detection, code-block exclusion,
severity split, threshold, span-overlap dedup, and the no-score-deduction
contract.
"""

from __future__ import annotations

from pathlib import Path

from agentfluent.config.models import AgentConfig, Scope, Severity
from agentfluent.config.scoring import (
    VERBOSITY_POSTMORTEM_URL,
    _detect_verbosity_constraints,
    _score_prompt_body,
    score_agent,
)


def _make_agent(prompt_body: str) -> AgentConfig:
    """AgentConfig with the given prompt body and otherwise sane defaults."""
    return AgentConfig(
        name="test-agent",
        file_path=Path("/tmp/test.md"),
        scope=Scope.USER,
        description="A test agent that reviews and analyzes things thoroughly.",
        model="claude-sonnet-5",
        tools=["Read", "Grep"],
        prompt_body=prompt_body,
    )


def _verbosity_recs(prompt_body: str) -> list:
    """Verbosity recs emitted by ``_score_prompt_body`` for a body."""
    _, recs = _score_prompt_body(_make_agent(prompt_body))
    return [r for r in recs if "word-count constraint" in r.message]


class TestDetectHelper:
    def test_le_25_no_space_detected(self) -> None:
        # The canonical postmortem phrasing has no space after the symbol.
        assert _detect_verbosity_constraints("Keep responses to ≤25 words") == [
            (25, "Keep responses to ≤25 words"),
        ]

    def test_max_100_detected(self) -> None:
        assert _detect_verbosity_constraints("Limit output to max 100 words") == [
            (100, "max 100 words"),
        ]

    def test_maximum_50_detected(self) -> None:
        assert _detect_verbosity_constraints("maximum 50 words") == [
            (50, "maximum 50 words"),
        ]

    def test_respond_with_30_detected(self) -> None:
        assert _detect_verbosity_constraints("respond with 30 words") == [
            (30, "respond with 30 words"),
        ]

    def test_no_constraint_no_match(self) -> None:
        assert _detect_verbosity_constraints("Write thorough, detailed responses") == []

    def test_above_threshold_not_flagged(self) -> None:
        # Captured (500) but dropped: 500 > VERBOSITY_CONSTRAINT_MAX_WORDS.
        assert _detect_verbosity_constraints("Limit output to 500 words") == []

    def test_empty_body_no_match(self) -> None:
        assert _detect_verbosity_constraints("") == []

    def test_postmortem_string_yields_two_distinct(self) -> None:
        # The literal string the feature exists to catch -> two constraints.
        body = (
            "keep text between tool calls to ≤25 words; "
            "keep final responses to ≤100 words"
        )
        assert _detect_verbosity_constraints(body) == [
            (25, "keep text between tool calls to ≤25 words"),
            (100, "keep final responses to ≤100 words"),
        ]

    def test_overlapping_patterns_deduped_to_one(self) -> None:
        # "maximum 50 words or fewer" is caught by patterns 2 and 3; dedup -> one.
        assert _detect_verbosity_constraints("maximum 50 words or fewer") == [
            (50, "maximum 50 words"),
        ]

    def test_le_25_single_rec_not_double(self) -> None:
        # The ≤-aware pattern 1 also matches pattern 2 on this string; span
        # dedup must collapse it to exactly one result.
        assert len(_detect_verbosity_constraints("Keep responses to ≤25 words")) == 1


class TestCodeBlockExclusion:
    def test_constraint_inside_code_block_ignored(self) -> None:
        body = "```\nkeep to ≤25 words\n```"
        assert _detect_verbosity_constraints(body) == []

    def test_only_outside_code_block_flagged(self) -> None:
        body = "keep to ≤25 words outside\n```\nkeep to ≤25 words\n```"
        assert _detect_verbosity_constraints(body) == [
            (25, "keep to ≤25 words"),
        ]


class TestScoringIntegration:
    def test_le_25_emits_warning(self) -> None:
        recs = _verbosity_recs("You are helpful. Keep responses to ≤25 words please.")
        assert len(recs) == 1
        assert recs[0].severity is Severity.WARNING
        assert recs[0].dimension == "prompt_body"

    def test_max_100_emits_info(self) -> None:
        recs = _verbosity_recs("You are helpful. Limit output to max 100 words please.")
        assert len(recs) == 1
        assert recs[0].severity is Severity.INFO

    def test_maximum_50_emits_warning(self) -> None:
        recs = _verbosity_recs("You are helpful. maximum 50 words in replies.")
        assert recs[0].severity is Severity.WARNING

    def test_respond_with_30_emits_warning(self) -> None:
        recs = _verbosity_recs("You are helpful. Always respond with 30 words.")
        assert recs[0].severity is Severity.WARNING

    def test_severity_boundary_50_warning_51_info(self) -> None:
        assert _verbosity_recs("maximum 50 words")[0].severity is Severity.WARNING
        assert _verbosity_recs("maximum 51 words")[0].severity is Severity.INFO

    def test_message_carries_matched_text_and_url(self) -> None:
        recs = _verbosity_recs("You are helpful. Keep responses to ≤25 words please.")
        rec = recs[0]
        assert rec.current_value == "Keep responses to ≤25 words"
        assert "Keep responses to ≤25 words" in rec.message
        assert VERBOSITY_POSTMORTEM_URL in rec.message
        assert "relax the word-count constraint" in rec.suggested_action

    def test_no_constraint_no_verbosity_rec(self) -> None:
        assert _verbosity_recs("You are a careful agent. Write thorough answers.") == []


class TestNoScoreDeduction:
    def _prompt_body_score(self, prompt_body: str) -> int:
        score, _ = _score_prompt_body(_make_agent(prompt_body))
        return score

    def test_dimension_score_unchanged_by_constraint(self) -> None:
        # A body that already earns the full 25 (present, >=100 chars, has a
        # markdown section, an error keyword, and a success keyword). Appending
        # a verbosity constraint must not drop it below 25 -- the constraint is
        # advisory-only and never deducts. (Base chosen at the cap so a
        # deduction, were it to exist, would be visible.)
        base = (
            "## Role\nYou analyze agent sessions and handle errors gracefully "
            "with retries. Return a complete result and deliver the output when "
            "the task is done."
        )
        assert self._prompt_body_score(base) == 25
        with_constraint = base + " Keep responses to ≤25 words."
        assert self._prompt_body_score(with_constraint) == 25

    def test_score_agent_includes_verbosity_rec(self) -> None:
        agent = _make_agent(
            "## Role\nYou analyze sessions. Handle errors gracefully and return "
            "a complete result. Keep responses to ≤25 words."
        )
        result = score_agent(agent)
        verbosity = [r for r in result.recommendations if "word-count constraint" in r.message]
        assert len(verbosity) == 1
        assert verbosity[0].dimension == "prompt_body"

    def test_score_agent_prompt_body_score_matches_direct(self) -> None:
        # score_agent must not double-count or drop the dimension when a
        # verbosity constraint is present.
        agent = _make_agent(
            "## Role\nYou analyze sessions. Handle errors gracefully and return "
            "a complete result. Keep responses to ≤25 words."
        )
        direct, _ = _score_prompt_body(agent)
        assert score_agent(agent).dimension_scores["prompt_body"] == direct
