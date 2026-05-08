"""Tests for parent-thread tool-burst extraction (sub-issue B of #189).

Covers ``extract_bursts``, ``burst_text``, ``filter_bursts`` and the
boundary semantics that downstream sub-issues (C: cost, D: clustering)
depend on.

Fixture: ``tests/fixtures/parent_workload_session.jsonl`` is a synthetic
parent-thread session designed to produce exactly 5 bursts pre-filter
and 2 bursts post-filter, exercising every boundary case in one read.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentfluent.core.parser import parse_session
from agentfluent.core.session import (
    ContentBlock,
    SessionMessage,
    ToolUseBlock,
    Usage,
)
from agentfluent.diagnostics.parent_workload import (
    MAX_BURST_TOOLS,
    MIN_BURST_TEXT_TOKENS,
    MIN_BURST_TOOLS,
    ToolBurst,
    _is_real_user_text,
    burst_text,
    extract_bursts,
    filter_bursts,
)

FIXTURE = Path(__file__).parent.parent / "fixtures" / "parent_workload_session.jsonl"


def _user(text: str = "", *, with_tool_result: bool = False) -> SessionMessage:
    blocks: list[ContentBlock] = []
    if text:
        blocks.append(ContentBlock(type="text", text=text))
    if with_tool_result:
        blocks.append(
            ContentBlock(type="tool_result", tool_use_id="toolu_x", text="result"),
        )
    return SessionMessage(type="user", content_blocks=blocks)


def _assistant(
    *,
    text: str = "",
    tools: list[str] | None = None,
    usage: Usage | None = None,
    model: str = "claude-opus-4-7",
) -> SessionMessage:
    blocks: list[ContentBlock] = []
    if text:
        blocks.append(ContentBlock(type="text", text=text))
    for i, name in enumerate(tools or []):
        blocks.append(
            ContentBlock(type="tool_use", id=f"toolu_{i}", name=name, input={}),
        )
    return SessionMessage(
        type="assistant",
        content_blocks=blocks,
        model=model,
        usage=usage or Usage(),
    )


# ---------------------------------------------------------------------------
# _is_real_user_text
# ---------------------------------------------------------------------------


class TestIsRealUserText:
    def test_text_only_user_is_real(self) -> None:
        assert _is_real_user_text(_user("hello")) is True

    def test_tool_result_only_user_is_not_real(self) -> None:
        assert _is_real_user_text(_user(with_tool_result=True)) is False

    def test_text_plus_tool_result_user_is_not_real(self) -> None:
        # Defensive: even if a user message somehow has both text and a
        # tool_result, it's still structurally a tool-result wrapper.
        assert _is_real_user_text(_user("hi", with_tool_result=True)) is False

    def test_assistant_message_is_not_real_user_text(self) -> None:
        assert _is_real_user_text(_assistant(text="hi", tools=["Read"])) is False

    def test_whitespace_only_text_is_not_real(self) -> None:
        assert _is_real_user_text(_user("   ")) is False


# ---------------------------------------------------------------------------
# (Usage.__add__ is exercised indirectly via extract_bursts' usage summing;
# direct tests for the operator live in tests/unit/test_session_models.py.)


# ---------------------------------------------------------------------------
# extract_bursts — boundary semantics on hand-built message sequences
# ---------------------------------------------------------------------------


class TestExtractBurstsBoundaries:
    def test_empty_messages_returns_empty(self) -> None:
        assert extract_bursts([]) == []

    def test_no_assistant_messages_returns_empty(self) -> None:
        assert extract_bursts([_user("hello")]) == []

    def test_single_assistant_burst_no_tool_use_returns_empty(self) -> None:
        # An assistant message with only text (no tool_use) doesn't
        # constitute a burst.
        msgs = [_user("hi"), _assistant(text="answer")]
        assert extract_bursts(msgs) == []

    def test_single_user_assistant_burst_extracts_one_burst(self) -> None:
        msgs = [
            _user("read this file"),
            _assistant(tools=["Read"]),
        ]
        bursts = extract_bursts(msgs)
        assert len(bursts) == 1
        assert bursts[0].preceding_user_text == "read this file"
        assert [b.name for b in bursts[0].tool_use_blocks] == ["Read"]

    def test_cross_turn_merge_via_tool_result_only_user(self) -> None:
        # The standard tool loop: assistant → tool_result-only user →
        # assistant. Both assistant turns belong to one burst.
        msgs = [
            _user("do the thing"),
            _assistant(text="step one", tools=["Bash"]),
            _user(with_tool_result=True),
            _assistant(text="step two", tools=["Read"]),
        ]
        bursts = extract_bursts(msgs)
        assert len(bursts) == 1
        assert [b.name for b in bursts[0].tool_use_blocks] == ["Bash", "Read"]
        assert "step one" in bursts[0].assistant_text
        assert "step two" in bursts[0].assistant_text

    def test_real_user_message_breaks_the_burst(self) -> None:
        msgs = [
            _user("first task"),
            _assistant(tools=["Read", "Edit"]),
            _user(with_tool_result=True),
            _user("second task"),
            _assistant(tools=["Bash"]),
        ]
        bursts = extract_bursts(msgs)
        assert len(bursts) == 2
        assert bursts[0].preceding_user_text == "first task"
        assert [b.name for b in bursts[0].tool_use_blocks] == ["Read", "Edit"]
        assert bursts[1].preceding_user_text == "second task"
        assert [b.name for b in bursts[1].tool_use_blocks] == ["Bash"]

    def test_text_only_assistant_turn_within_burst_is_folded_in(self) -> None:
        # An "I'll now do X" text-only turn between two tool_use turns
        # contributes its text to the burst's assistant_text without
        # breaking the burst.
        msgs = [
            _user("plan and execute"),
            _assistant(text="planning", tools=["Read"]),
            _user(with_tool_result=True),
            _assistant(text="now executing"),  # text-only, no tools
            _assistant(text="done", tools=["Edit"]),
        ]
        bursts = extract_bursts(msgs)
        assert len(bursts) == 1
        assert "now executing" in bursts[0].assistant_text
        assert [b.name for b in bursts[0].tool_use_blocks] == ["Read", "Edit"]

    def test_burst_usage_sums_constituent_messages(self) -> None:
        msgs = [
            _user("go"),
            _assistant(
                tools=["Read"],
                usage=Usage(input_tokens=10, output_tokens=20),
            ),
            _user(with_tool_result=True),
            _assistant(
                tools=["Edit"],
                usage=Usage(input_tokens=5, output_tokens=15,
                            cache_read_input_tokens=100),
            ),
        ]
        bursts = extract_bursts(msgs)
        assert len(bursts) == 1
        assert bursts[0].usage.input_tokens == 15
        assert bursts[0].usage.output_tokens == 35
        assert bursts[0].usage.cache_read_input_tokens == 100

    def test_burst_model_is_first_assistant_model(self) -> None:
        msgs = [
            _user("go"),
            _assistant(tools=["Read"], model="claude-opus-4-7"),
            _user(with_tool_result=True),
            _assistant(tools=["Edit"], model="claude-sonnet-4-6"),
        ]
        bursts = extract_bursts(msgs)
        assert len(bursts) == 1
        assert bursts[0].model == "claude-opus-4-7"

    def test_burst_open_at_session_end_is_emitted(self) -> None:
        # No closing real-user message; burst should still be emitted.
        msgs = [
            _user("last task"),
            _assistant(tools=["Read", "Edit"]),
        ]
        bursts = extract_bursts(msgs)
        assert len(bursts) == 1


# ---------------------------------------------------------------------------
# extract_bursts — tool_result pairing for error-rate (#264)
# ---------------------------------------------------------------------------


def _assistant_tools_with_ids(
    tool_ids: list[tuple[str, str]],
    *,
    text: str = "",
    model: str = "claude-opus-4-7",
) -> SessionMessage:
    """Build an assistant message with caller-specified ``(tool_name, id)``
    pairs so tests can pair specific tool_use ids to tool_result blocks
    by id."""
    blocks: list[ContentBlock] = []
    if text:
        blocks.append(ContentBlock(type="text", text=text))
    for name, tool_id in tool_ids:
        blocks.append(
            ContentBlock(type="tool_use", id=tool_id, name=name, input={}),
        )
    return SessionMessage(
        type="assistant",
        content_blocks=blocks,
        model=model,
        usage=Usage(),
    )


def _user_tool_result(
    tool_use_id: str, *, is_error: bool | None = False, text: str = "ok",
) -> SessionMessage:
    return SessionMessage(
        type="user",
        content_blocks=[
            ContentBlock(
                type="tool_result",
                tool_use_id=tool_use_id,
                text=text,
                is_error=is_error,
            ),
        ],
    )


class TestExtractBurstsToolResultPairing:
    """``extract_bursts`` pairs each tool_use to its tool_result via
    ``index_tool_results_by_id`` and stamps the per-burst error count
    on ``ToolBurst.tool_result_errors`` — driving the cluster
    ``error_rate`` in ``_aggregate_burst_stats`` (#264)."""

    def test_no_paired_results_yields_zero_errors(self) -> None:
        """Backward-compat: a session with no tool_result blocks (or
        an interrupted session where pairs are missing) still produces
        bursts with ``tool_result_errors=0`` — the default fall-through."""
        msgs = [
            _user("read it"),
            _assistant_tools_with_ids([("Read", "toolu_a")]),
        ]
        bursts = extract_bursts(msgs)
        assert len(bursts) == 1
        assert bursts[0].tool_result_errors == 0

    def test_all_success_results_yields_zero_errors(self) -> None:
        msgs = [
            _user("read both"),
            _assistant_tools_with_ids(
                [("Read", "toolu_a"), ("Read", "toolu_b")],
            ),
            _user_tool_result("toolu_a", is_error=False),
            _user_tool_result("toolu_b", is_error=False),
        ]
        bursts = extract_bursts(msgs)
        assert bursts[0].tool_result_errors == 0

    def test_partial_errors_counted(self) -> None:
        """1-of-3 paired tool_results is_error=True → tool_result_errors=1."""
        msgs = [
            _user("run three"),
            _assistant_tools_with_ids(
                [
                    ("Bash", "toolu_a"),
                    ("Bash", "toolu_b"),
                    ("Bash", "toolu_c"),
                ],
            ),
            _user_tool_result("toolu_a", is_error=False),
            _user_tool_result("toolu_b", is_error=True),
            _user_tool_result("toolu_c", is_error=False),
        ]
        bursts = extract_bursts(msgs)
        assert bursts[0].tool_result_errors == 1
        assert len(bursts[0].tool_use_blocks) == 3

    def test_is_error_none_not_counted(self) -> None:
        """``is_error`` is ``bool | None``; missing field (``None``)
        must not count as an error — checked via ``is True`` rather
        than truthy."""
        msgs = [
            _user("read it"),
            _assistant_tools_with_ids([("Read", "toolu_a")]),
            _user_tool_result("toolu_a", is_error=None),
        ]
        bursts = extract_bursts(msgs)
        assert bursts[0].tool_result_errors == 0

    def test_missing_pair_not_counted_as_error(self) -> None:
        """Tool_use without a paired tool_result (interrupted session)
        is treated as 'not an error' rather than an error — defensive
        default that preserves the prior 0.0 baseline."""
        msgs = [
            _user("two tools"),
            _assistant_tools_with_ids(
                [("Bash", "toolu_a"), ("Bash", "toolu_b")],
            ),
            _user_tool_result("toolu_a", is_error=True),
            # toolu_b never paired (interrupted)
        ]
        bursts = extract_bursts(msgs)
        # Only the paired error counts; the missing pair contributes 0.
        assert bursts[0].tool_result_errors == 1

    def test_errors_segregated_by_burst(self) -> None:
        """Two bursts separated by a real user message: errors only
        attribute to the burst they belong to."""
        msgs = [
            _user("first job"),
            _assistant_tools_with_ids([("Bash", "toolu_a")]),
            _user_tool_result("toolu_a", is_error=True),
            _user("second job"),
            _assistant_tools_with_ids([("Read", "toolu_b")]),
            _user_tool_result("toolu_b", is_error=False),
        ]
        bursts = extract_bursts(msgs)
        assert len(bursts) == 2
        assert bursts[0].tool_result_errors == 1
        assert bursts[1].tool_result_errors == 0


# ---------------------------------------------------------------------------
# burst_text
# ---------------------------------------------------------------------------


class TestBurstText:
    def test_joins_user_assistant_and_tool_names(self) -> None:
        b = ToolBurst(
            preceding_user_text="run pytest",
            assistant_text="ok",
            tool_use_blocks=[
                ToolUseBlock(id="t1", name="Bash"),
                ToolUseBlock(id="t2", name="Read"),
            ],
        )
        assert burst_text(b) == "run pytest ok Bash Read"

    def test_does_not_dedup_repeated_tool_names(self) -> None:
        # Repeated tool use is a discriminative signal — sub-issue D's
        # TF-IDF should weight the multiplicity. Confirm we don't strip it.
        b = ToolBurst(
            preceding_user_text="x",
            assistant_text="y",
            tool_use_blocks=[
                ToolUseBlock(id="t1", name="Read"),
                ToolUseBlock(id="t2", name="Read"),
                ToolUseBlock(id="t3", name="Edit"),
            ],
        )
        text = burst_text(b)
        assert text.count("Read") == 2
        assert text.count("Edit") == 1

    def test_skips_empty_components(self) -> None:
        b = ToolBurst(
            preceding_user_text="",
            assistant_text="",
            tool_use_blocks=[ToolUseBlock(id="t1", name="Read")],
        )
        # No leading/trailing whitespace from the empty components.
        assert burst_text(b) == "Read"


# ---------------------------------------------------------------------------
# filter_bursts
# ---------------------------------------------------------------------------


def _burst_with(*, n_tools: int, text_tokens: int) -> ToolBurst:
    """Build a ToolBurst whose burst_text has roughly text_tokens tokens."""
    # Use a short word repeated, then add tool names. We pre-subtract the
    # tool-name tokens so the total lands at text_tokens.
    n_filler = max(0, text_tokens - n_tools)
    return ToolBurst(
        preceding_user_text=" ".join(["w"] * n_filler),
        assistant_text="",
        tool_use_blocks=[ToolUseBlock(id=f"t{i}", name="X") for i in range(n_tools)],
    )


class TestFilterBursts:
    def test_drops_below_min_burst_tools(self) -> None:
        b = _burst_with(n_tools=MIN_BURST_TOOLS - 1, text_tokens=100)
        assert filter_bursts([b]) == []

    def test_keeps_at_min_burst_tools(self) -> None:
        b = _burst_with(n_tools=MIN_BURST_TOOLS, text_tokens=100)
        assert filter_bursts([b]) == [b]

    def test_drops_above_max_burst_tools(self) -> None:
        b = _burst_with(n_tools=MAX_BURST_TOOLS + 1, text_tokens=100)
        assert filter_bursts([b]) == []

    def test_keeps_at_max_burst_tools(self) -> None:
        b = _burst_with(n_tools=MAX_BURST_TOOLS, text_tokens=100)
        assert filter_bursts([b]) == [b]

    def test_drops_below_min_burst_text_tokens(self) -> None:
        b = _burst_with(n_tools=2, text_tokens=MIN_BURST_TEXT_TOKENS - 1)
        assert filter_bursts([b]) == []

    def test_keeps_at_min_burst_text_tokens(self) -> None:
        b = _burst_with(n_tools=2, text_tokens=MIN_BURST_TEXT_TOKENS)
        assert filter_bursts([b]) == [b]

    def test_max_burst_drop_logs_at_debug(
        self, caplog: pytest.LogCaptureFixture,
    ) -> None:
        b = _burst_with(n_tools=MAX_BURST_TOOLS + 1, text_tokens=100)
        with caplog.at_level("DEBUG", logger="agentfluent.diagnostics.parent_workload"):
            filter_bursts([b])
        assert any("Dropping burst" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------------
# End-to-end via the fixture
# ---------------------------------------------------------------------------


class TestFixtureSession:
    def test_extracts_five_bursts_pre_filter(self) -> None:
        messages = parse_session(FIXTURE)
        bursts = extract_bursts(messages)
        assert len(bursts) == 5

    def test_filter_keeps_two_bursts(self) -> None:
        messages = parse_session(FIXTURE)
        bursts = filter_bursts(extract_bursts(messages))
        assert len(bursts) == 2

    def test_first_kept_burst_spans_cross_turn_merge(self) -> None:
        messages = parse_session(FIXTURE)
        bursts = filter_bursts(extract_bursts(messages))
        first = bursts[0]
        # Burst 1 in the fixture: pytest + read failing test, then edit
        # the test on the next assistant turn (separated by a tool_result
        # user message). 3 tools across two assistant messages.
        assert [b.name for b in first.tool_use_blocks] == ["Bash", "Read", "Edit"]
        assert "pytest" in first.preceding_user_text
        # Usage summed across the burst's two assistant messages:
        # 100+150 input, 50+30 output, 200+0 cache_creation, 1000+1300 cache_read.
        assert first.usage.input_tokens == 250
        assert first.usage.output_tokens == 80
        assert first.usage.cache_creation_input_tokens == 200
        assert first.usage.cache_read_input_tokens == 2300

    def test_second_kept_burst_starts_after_real_user_break(self) -> None:
        messages = parse_session(FIXTURE)
        bursts = filter_bursts(extract_bursts(messages))
        second = bursts[1]
        assert [b.name for b in second.tool_use_blocks] == ["WebFetch", "Read"]
        assert "pull request" in second.preceding_user_text.lower()

    def test_dropped_bursts_match_expected_reasons(self) -> None:
        messages = parse_session(FIXTURE)
        all_bursts = extract_bursts(messages)
        # Bursts 3, 4, 5 in the fixture are designed to be filtered out
        # for distinct reasons: 1-tool, 21-tool, short-text.
        assert len(all_bursts[2].tool_use_blocks) < MIN_BURST_TOOLS
        assert len(all_bursts[3].tool_use_blocks) > MAX_BURST_TOOLS
        assert len(burst_text(all_bursts[4]).split()) < MIN_BURST_TEXT_TOKENS
