"""Fragment-merge shape detection (#694).

Current Claude Code writes one assistant response as several JSONL lines,
one content block per line, sharing ``message.id``. Each line carries a
running ``usage`` snapshot, so ``output_tokens`` often differs between the
lines of one message. The merge must keep every block regardless of that
variance, and only collapse a group to its final line when the lines are
true cumulative snapshots (each a positional prefix-superset of the last).
"""

from datetime import UTC, datetime
from pathlib import Path

from agentfluent.core.parser import merge_assistant_fragments, parse_session
from agentfluent.core.session import ContentBlock, SessionMessage, Usage
from agentfluent.traces.parser import parse_subagent_trace


def _frag(
    blocks: list[ContentBlock], output_tokens: int, *, input_tokens: int = 1,
) -> SessionMessage:
    return SessionMessage(
        type="assistant",
        message_id="m",
        content_blocks=blocks,
        usage=Usage(input_tokens=input_tokens, output_tokens=output_tokens),
    )


def _merge_one(frags: list[SessionMessage]) -> SessionMessage:
    result = merge_assistant_fragments(frags)
    assert len(result) == 1
    return result[0]


def _text(text: str | None) -> ContentBlock:
    return ContentBlock(type="text", text=text)


def _thinking() -> ContentBlock:
    # The parser stores no text for thinking blocks (it lives under the
    # "thinking" key, which is not read), so text is None here as in real data.
    return ContentBlock(type="thinking", text=None)


def _tool(tool_id: str, name: str = "Read", **tool_input: str) -> ContentBlock:
    return ContentBlock(type="tool_use", id=tool_id, name=name, input=tool_input)


class TestParallelToolUseFixture:
    """AC1/AC3/AC4: the real shape, read from disk."""

    def test_every_block_survives_in_jsonl_order(
        self, subagent_parallel_tool_use_path: Path,
    ) -> None:
        messages = parse_session(subagent_parallel_tool_use_path)
        first = next(m for m in messages if m.type == "assistant")
        assert [(b.type, b.id) for b in first.content_blocks] == [
            ("thinking", None),
            ("text", None),
            ("tool_use", "toolu_par_A"),
            ("tool_use", "toolu_par_B"),
            ("tool_use", "toolu_par_C"),
        ]

    def test_usage_is_the_max_output_tokens_record(
        self, subagent_parallel_tool_use_path: Path,
    ) -> None:
        messages = parse_session(subagent_parallel_tool_use_path)
        first = next(m for m in messages if m.type == "assistant")
        # All four fields come from the toolu_par_C line (output_tokens=96).
        # The toolu_par_A line carries a larger cache_read_input_tokens
        # (5000), so a per-field max would report 5000 here, and a sum
        # would inflate every field.
        assert first.usage == Usage(
            input_tokens=1500,
            output_tokens=96,
            cache_creation_input_tokens=200,
            cache_read_input_tokens=3000,
        )

    def test_merged_message_carries_first_line_timestamp(
        self, subagent_parallel_tool_use_path: Path,
    ) -> None:
        # Every line of msg_par01 has a distinct timestamp; the merged
        # message (and so every parallel call paired from it) takes the
        # first line's.
        messages = parse_session(subagent_parallel_tool_use_path)
        first = next(m for m in messages if m.type == "assistant")
        assert first.timestamp == datetime(2026, 11, 1, 10, 0, 1, tzinfo=UTC)
        trace = parse_subagent_trace(subagent_parallel_tool_use_path)
        assert {c.timestamp for c in trace.tool_calls} == {first.timestamp}

    def test_message_count_and_order_unchanged(
        self, subagent_parallel_tool_use_path: Path,
    ) -> None:
        messages = parse_session(subagent_parallel_tool_use_path)
        assert [m.type for m in messages] == [
            "user", "assistant", "user", "user", "user", "assistant",
        ]

    def test_trace_tool_calls_count_every_parallel_call(
        self, subagent_parallel_tool_use_path: Path,
    ) -> None:
        trace = parse_subagent_trace(subagent_parallel_tool_use_path)
        assert [c.tool_name for c in trace.tool_calls] == ["Read", "Grep", "Glob"]
        assert all(c.result_summary for c in trace.tool_calls)

    def test_parallel_calls_split_across_lines_share_turn_index(
        self, subagent_parallel_tool_use_path: Path,
    ) -> None:
        # #695: on the real block-per-line layout, siblings share a turn index
        # only because the fragment merge runs before tool-call pairing.
        trace = parse_subagent_trace(subagent_parallel_tool_use_path)
        assert [c.turn_index for c in trace.tool_calls] == [0, 0, 0]


class TestShapeDetectionIgnoresTokenVariance:
    """AC2: the shape test reads content, never output_tokens variance."""

    def test_block_per_line_with_varying_tokens_unions(self) -> None:
        merged = _merge_one([
            _frag([_thinking()], 3),
            _frag([_text("Reading both.")], 12),
            _frag([_tool("t1")], 40),
            _frag([_tool("t2")], 71),
        ])
        assert [b.type for b in merged.content_blocks] == [
            "thinking", "text", "tool_use", "tool_use",
        ]

    def test_cumulative_snapshots_with_identical_tokens_collapse(self) -> None:
        merged = _merge_one([
            _frag([_text("Hi")], 25),
            _frag([_text("Hi there")], 25),
            _frag([_text("Hi there!"), _tool("t1")], 25),
        ])
        assert [(b.type, b.text, b.id) for b in merged.content_blocks] == [
            ("text", "Hi there!", None),
            ("tool_use", None, "t1"),
        ]

    def test_parallel_tool_use_only_message_unions(self) -> None:
        # The #694 shape with no thinking/text preamble: distinct ids never
        # match positionally, so every call survives.
        merged = _merge_one([
            _frag([_tool("t1")], 10),
            _frag([_tool("t2")], 20),
            _frag([_tool("t3")], 30),
        ])
        assert [b.id for b in merged.content_blocks] == ["t1", "t2", "t3"]

    def test_idless_tool_use_lines_union(self) -> None:
        merged = _merge_one([
            _frag([ContentBlock(type="tool_use", name="Read")], 10),
            _frag([ContentBlock(type="tool_use", name="Read")], 20),
        ])
        assert len(merged.content_blocks) == 2

    def test_later_line_with_fewer_blocks_unions(self) -> None:
        # The earlier line's text is a prefix of the later one's, but the
        # later line is shorter, so it cannot be a cumulative snapshot;
        # t1 must survive.
        merged = _merge_one([
            _frag([_text("Hi"), _tool("t1")], 5),
            _frag([_text("Hi there")], 9),
        ])
        assert [(b.type, b.text, b.id) for b in merged.content_blocks] == [
            ("text", "Hi", None),
            ("tool_use", None, "t1"),
            ("text", "Hi there", None),
        ]

    def test_partial_prefix_falls_back_to_union(self) -> None:
        # The second pair breaks the prefix chain, so nothing is collapsed.
        merged = _merge_one([
            _frag([_text("Hi")], 2),
            _frag([_text("Hi there")], 10),
            _frag([_text("Unrelated")], 20),
        ])
        assert [b.text for b in merged.content_blocks] == [
            "Hi", "Hi there", "Unrelated",
        ]


class TestNonCumulativeBlockTypesUnion:
    """Architect B1: only text (non-empty prefix) and tool_use (same id) can
    match a position; anything else makes the group a union."""

    def test_thinking_only_group_unions(self) -> None:
        merged = _merge_one([
            _frag([_thinking()], 5),
            _frag([_thinking()], 9),
            _frag([_thinking()], 14),
        ])
        assert [b.type for b in merged.content_blocks] == ["thinking"] * 3

    def test_empty_text_followed_by_text_unions(self) -> None:
        merged = _merge_one([
            _frag([_text("")], 5),
            _frag([_text("Next")], 9),
        ])
        assert [b.text for b in merged.content_blocks] == ["", "Next"]

    def test_unknown_block_type_unions(self) -> None:
        merged = _merge_one([
            _frag([ContentBlock(type="redacted_thinking", text=None)], 5),
            _frag([ContentBlock(type="redacted_thinking", text=None)], 9),
        ])
        assert len(merged.content_blocks) == 2


class TestLegacyCumulativeResult:
    """Architect I2: lock what a true cumulative group merges to."""

    def test_streaming_fixture_merges_to_final_snapshot(
        self, streaming_dupes_session_path: Path,
    ) -> None:
        messages = parse_session(streaming_dupes_session_path)
        second = [m for m in messages if m.type == "assistant"][1]
        assert [(b.type, b.text, b.id) for b in second.content_blocks] == [
            ("text", "Let me look at the file.", None),
            ("tool_use", None, "toolu_01READ"),
        ]

    def test_growing_tool_input_keeps_final_input(self) -> None:
        merged = _merge_one([
            _frag([_tool("t1", file_path="/p")], 2),
            _frag([_tool("t1", file_path="/project/src/a.py")], 8),
        ])
        assert len(merged.content_blocks) == 1
        assert merged.content_blocks[0].input == {"file_path": "/project/src/a.py"}

    def test_union_keeps_latest_tool_use_at_first_position(self) -> None:
        # A repeated id inside a non-cumulative group is deduped: it keeps
        # the first occurrence's position and the latest occurrence's block.
        merged = _merge_one([
            _frag([_thinking()], 2),
            _frag([_tool("t1", file_path="/first")], 8),
            _frag([_tool("t2", file_path="/other")], 9),
            _frag([_tool("t1", file_path="/second")], 10),
        ])
        assert [(b.type, b.id) for b in merged.content_blocks] == [
            ("thinking", None), ("tool_use", "t1"), ("tool_use", "t2"),
        ]
        assert merged.content_blocks[1].input == {"file_path": "/second"}


class TestAcceptedAmbiguities:
    """Scope ruling on #696 review (D1, D2): two documented consequences of
    the detection rule, pinned so a change to them is deliberate."""

    def test_legacy_chain_with_thinking_unions_with_final_tool_input(self) -> None:
        merged = _merge_one([
            _frag([_thinking(), _text("Let")], 5),
            _frag([_thinking(), _text("Let me"), _tool("a")], 9),
            _frag([_thinking(), _text("Let me look"), _tool("a", p="/x")], 20),
        ])
        types = [b.type for b in merged.content_blocks]
        assert types.count("thinking") == 3
        assert [b.text for b in merged.content_blocks if b.type == "text"] == [
            "Let", "Let me", "Let me look",
        ]
        tools = [b for b in merged.content_blocks if b.type == "tool_use"]
        assert len(tools) == 1
        assert tools[0].input == {"p": "/x"}

    def test_prefix_text_lines_collapse_to_last_line(self) -> None:
        merged = _merge_one([
            _frag([_text("Checking")], 5),
            _frag([_text("Checking the config now")], 5),
        ])
        assert [b.text for b in merged.content_blocks] == ["Checking the config now"]


class TestUsageWinnerIsMaxNotLast:
    """Architect M5: usage comes from the max-output_tokens fragment, which
    need not be the last line."""

    def test_union_path(self) -> None:
        merged = _merge_one([
            _frag([_text("a")], 10, input_tokens=1),
            _frag([_tool("t1")], 90, input_tokens=700),
            _frag([_tool("t2")], 40, input_tokens=1),
        ])
        assert merged.usage == Usage(input_tokens=700, output_tokens=90)
        assert len(merged.content_blocks) == 3

    def test_whole_record_not_per_field_max(self) -> None:
        # The first line has the larger cache_read; the winner's whole record
        # (cache_read 10) must come through, not a per-field max (900).
        frags = [
            _frag([_text("a")], 10),
            _frag([_tool("t1")], 90, input_tokens=700),
        ]
        frags[0].usage = Usage(input_tokens=1, output_tokens=10, cache_read_input_tokens=900)
        frags[1].usage = Usage(input_tokens=700, output_tokens=90, cache_read_input_tokens=10)
        merged = _merge_one(frags)
        assert merged.usage == Usage(
            input_tokens=700, output_tokens=90, cache_read_input_tokens=10,
        )

    def test_cumulative_path(self) -> None:
        merged = _merge_one([
            _frag([_text("Hi")], 10, input_tokens=1),
            _frag([_text("Hi there")], 90, input_tokens=700),
            _frag([_text("Hi there!")], 40, input_tokens=1),
        ])
        assert merged.usage == Usage(input_tokens=700, output_tokens=90)
        assert [b.text for b in merged.content_blocks] == ["Hi there!"]
