"""Fragment-merge shape detection (#694).

Current Claude Code writes one assistant response as several JSONL lines,
one content block per line, sharing ``message.id``. Each line carries a
running ``usage`` snapshot, so ``output_tokens`` often differs between the
lines of one message. The merge must keep every block regardless of that
variance, and only collapse a group to its final line when the lines are
true cumulative snapshots (each a positional prefix-superset of the last).
"""

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
        # All four fields come from the toolu_par_C line (output_tokens=96),
        # not a per-field max and not a sum across the five lines.
        assert first.usage == Usage(
            input_tokens=1500,
            output_tokens=96,
            cache_creation_input_tokens=200,
            cache_read_input_tokens=3000,
        )

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

    def test_union_keeps_first_tool_use_for_a_repeated_id(self) -> None:
        # A repeated id inside a non-cumulative group is deduped; the
        # first occurrence wins.
        merged = _merge_one([
            _frag([_thinking()], 2),
            _frag([_tool("t1", file_path="/first")], 8),
            _frag([_tool("t1", file_path="/second")], 9),
        ])
        tools = [b for b in merged.content_blocks if b.type == "tool_use"]
        assert len(tools) == 1
        assert tools[0].input == {"file_path": "/first"}


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

    def test_cumulative_path(self) -> None:
        merged = _merge_one([
            _frag([_text("Hi")], 10, input_tokens=1),
            _frag([_text("Hi there")], 90, input_tokens=700),
            _frag([_text("Hi there!")], 40, input_tokens=1),
        ])
        assert merged.usage == Usage(input_tokens=700, output_tokens=90)
        assert [b.text for b in merged.content_blocks] == ["Hi there!"]
