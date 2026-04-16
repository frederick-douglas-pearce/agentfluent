"""Tests for tool pattern analytics."""

from agentfluent.analytics.tools import ToolMetrics, compute_tool_metrics
from agentfluent.core.session import ContentBlock, SessionMessage


def _assistant_with_tools(*tool_names: str) -> SessionMessage:
    """Helper to create an assistant message with tool_use blocks."""
    blocks = [
        ContentBlock(type="tool_use", id=f"toolu_{i}", name=name, input={})
        for i, name in enumerate(tool_names)
    ]
    return SessionMessage(type="assistant", content_blocks=blocks)


class TestComputeToolMetrics:
    def test_basic_frequency(self) -> None:
        messages = [
            _assistant_with_tools("Read", "Edit"),
            _assistant_with_tools("Read", "Read", "Bash"),
        ]
        metrics = compute_tool_metrics(messages)
        assert metrics.tool_frequency == {"Read": 3, "Bash": 1, "Edit": 1}
        assert metrics.unique_tool_count == 3
        assert metrics.total_tool_calls == 5

    def test_empty_messages(self) -> None:
        metrics = compute_tool_metrics([])
        assert metrics.tool_frequency == {}
        assert metrics.unique_tool_count == 0
        assert metrics.total_tool_calls == 0

    def test_no_tool_calls(self) -> None:
        messages = [
            SessionMessage(
                type="assistant",
                content_blocks=[ContentBlock(type="text", text="Hello")],
            ),
        ]
        metrics = compute_tool_metrics(messages)
        assert metrics.unique_tool_count == 0
        assert metrics.total_tool_calls == 0

    def test_skips_non_assistant_messages(self) -> None:
        messages = [
            SessionMessage(type="user", content_blocks=[ContentBlock(type="text", text="Hi")]),
            _assistant_with_tools("Read"),
            SessionMessage(type="tool_result", tool_use_id="t1"),
        ]
        metrics = compute_tool_metrics(messages)
        assert metrics.total_tool_calls == 1
        assert metrics.tool_frequency == {"Read": 1}

    def test_frequency_sorted_descending(self) -> None:
        messages = [
            _assistant_with_tools("Bash", "Read", "Read", "Edit", "Edit", "Edit"),
        ]
        metrics = compute_tool_metrics(messages)
        keys = list(metrics.tool_frequency.keys())
        assert keys == ["Edit", "Read", "Bash"]

    def test_alphabetical_tiebreaker(self) -> None:
        messages = [
            _assistant_with_tools("Zebra", "Alpha"),
        ]
        metrics = compute_tool_metrics(messages)
        keys = list(metrics.tool_frequency.keys())
        assert keys == ["Alpha", "Zebra"]


class TestConcentration:
    def test_concentration_curve(self) -> None:
        messages = [
            _assistant_with_tools("Read", "Read", "Read", "Read", "Read"),
            _assistant_with_tools("Edit", "Edit", "Edit"),
            _assistant_with_tools("Bash", "Bash"),
        ]
        metrics = compute_tool_metrics(messages)
        assert len(metrics.concentration) == 3

        # Top 1 tool (Read=5) accounts for 50% of 10 calls
        assert metrics.concentration[0].top_n == 1
        assert metrics.concentration[0].call_count == 5
        assert metrics.concentration[0].percentage == 50.0

        # Top 2 tools (Read=5, Edit=3) = 80%
        assert metrics.concentration[1].top_n == 2
        assert metrics.concentration[1].call_count == 8
        assert metrics.concentration[1].percentage == 80.0

        # All 3 tools = 100%
        assert metrics.concentration[2].percentage == 100.0

    def test_empty_concentration(self) -> None:
        metrics = compute_tool_metrics([])
        assert metrics.concentration == []

    def test_single_tool_concentration(self) -> None:
        messages = [_assistant_with_tools("Read", "Read", "Read")]
        metrics = compute_tool_metrics(messages)
        assert len(metrics.concentration) == 1
        assert metrics.concentration[0].percentage == 100.0
