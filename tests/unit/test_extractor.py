"""Tests for agent invocation extractor."""

from pathlib import Path

from agentfluent.agents.extractor import extract_agent_invocations
from agentfluent.core.parser import parse_session
from agentfluent.core.session import (
    ContentBlock,
    SessionMessage,
    ToolResultMetadata,
)


class TestExtractFromFixtures:
    def test_session_with_agents(self, agent_session_path: Path) -> None:
        messages = parse_session(agent_session_path)
        invocations = extract_agent_invocations(messages)

        assert len(invocations) == 2

        # First: PM agent (custom)
        pm = invocations[0]
        assert pm.agent_type == "pm"
        assert pm.is_builtin is False
        assert pm.description == "Review backlog and create issues"
        assert "backlog" in pm.prompt.lower()
        assert pm.total_tokens == 31621
        assert pm.tool_uses == 14
        assert pm.duration_ms == 122963
        assert pm.agent_id == "agent-abc123"
        assert "Created 5 issues" in pm.output_text

        # Second: Explore agent (built-in)
        explore = invocations[1]
        assert explore.agent_type == "Explore"
        assert explore.is_builtin is True
        assert explore.total_tokens == 8500
        assert explore.tool_uses == 5
        assert explore.agent_id == "agent-def456"

    def test_session_without_agents(self, basic_session_path: Path) -> None:
        messages = parse_session(basic_session_path)
        invocations = extract_agent_invocations(messages)
        assert invocations == []

    def test_session_with_regular_tools_only(self, tool_calls_session_path: Path) -> None:
        messages = parse_session(tool_calls_session_path)
        invocations = extract_agent_invocations(messages)
        assert invocations == []


class TestExtractFromConstructedMessages:
    def test_agent_without_matching_result(self) -> None:
        """Agent tool_use with no corresponding tool_result (interrupted)."""
        messages = [
            SessionMessage(
                type="assistant",
                content_blocks=[
                    ContentBlock(
                        type="tool_use",
                        id="toolu_orphan",
                        name="Agent",
                        input={
                            "subagent_type": "pm",
                            "description": "Interrupted task",
                            "prompt": "Do something",
                        },
                    ),
                ],
            ),
        ]
        invocations = extract_agent_invocations(messages)
        assert len(invocations) == 1
        assert invocations[0].agent_type == "pm"
        assert invocations[0].total_tokens is None
        assert invocations[0].output_text == ""

    def test_tool_result_without_metadata(self) -> None:
        """Agent tool_result that lacks the metadata block."""
        messages = [
            SessionMessage(
                type="assistant",
                content_blocks=[
                    ContentBlock(
                        type="tool_use",
                        id="toolu_no_meta",
                        name="Agent",
                        input={
                            "subagent_type": "Explore",
                            "description": "Quick search",
                            "prompt": "Find files",
                        },
                    ),
                ],
            ),
            SessionMessage(
                type="tool_result",
                tool_use_id="toolu_no_meta",
                content_blocks=[ContentBlock(type="text", text="Found 3 files.")],
                metadata=None,
            ),
        ]
        invocations = extract_agent_invocations(messages)
        assert len(invocations) == 1
        assert invocations[0].is_builtin is True
        assert invocations[0].output_text == "Found 3 files."
        assert invocations[0].total_tokens is None
        assert invocations[0].agent_id is None

    def test_multiple_agents_in_one_message(self) -> None:
        """Assistant message with multiple Agent tool_use blocks."""
        messages = [
            SessionMessage(
                type="assistant",
                content_blocks=[
                    ContentBlock(
                        type="tool_use",
                        id="toolu_a",
                        name="Agent",
                        input={"subagent_type": "Explore", "description": "A", "prompt": "A"},
                    ),
                    ContentBlock(
                        type="tool_use",
                        id="toolu_b",
                        name="Agent",
                        input={"subagent_type": "pm", "description": "B", "prompt": "B"},
                    ),
                ],
            ),
            SessionMessage(
                type="tool_result",
                tool_use_id="toolu_a",
                content_blocks=[ContentBlock(type="text", text="Result A")],
                metadata=ToolResultMetadata(total_tokens=1000, tool_uses=5),
            ),
            SessionMessage(
                type="tool_result",
                tool_use_id="toolu_b",
                content_blocks=[ContentBlock(type="text", text="Result B")],
                metadata=ToolResultMetadata(total_tokens=2000, tool_uses=10),
            ),
        ]
        invocations = extract_agent_invocations(messages)
        assert len(invocations) == 2
        assert invocations[0].agent_type == "Explore"
        assert invocations[0].total_tokens == 1000
        assert invocations[1].agent_type == "pm"
        assert invocations[1].total_tokens == 2000

    def test_mixed_agent_and_regular_tools(self) -> None:
        """Assistant message with both Agent and regular tool_use blocks."""
        messages = [
            SessionMessage(
                type="assistant",
                content_blocks=[
                    ContentBlock(
                        type="tool_use",
                        id="toolu_read",
                        name="Read",
                        input={"file_path": "/tmp/test"},
                    ),
                    ContentBlock(
                        type="tool_use",
                        id="toolu_agent",
                        name="Agent",
                        input={"subagent_type": "Plan", "description": "Plan", "prompt": "Plan"},
                    ),
                ],
            ),
            SessionMessage(
                type="tool_result",
                tool_use_id="toolu_read",
                content_blocks=[ContentBlock(type="text", text="file content")],
            ),
            SessionMessage(
                type="tool_result",
                tool_use_id="toolu_agent",
                content_blocks=[ContentBlock(type="text", text="Plan result")],
                metadata=ToolResultMetadata(total_tokens=500, tool_uses=3),
            ),
        ]
        invocations = extract_agent_invocations(messages)
        # Only the Agent tool_use should be extracted
        assert len(invocations) == 1
        assert invocations[0].agent_type == "Plan"

    def test_efficiency_metrics_computed(self) -> None:
        messages = [
            SessionMessage(
                type="assistant",
                content_blocks=[
                    ContentBlock(
                        type="tool_use",
                        id="toolu_1",
                        name="Agent",
                        input={"subagent_type": "pm", "description": "D", "prompt": "P"},
                    ),
                ],
            ),
            SessionMessage(
                type="tool_result",
                tool_use_id="toolu_1",
                content_blocks=[ContentBlock(type="text", text="Done")],
                metadata=ToolResultMetadata(
                    total_tokens=10000, tool_uses=5, duration_ms=50000
                ),
            ),
        ]
        invocations = extract_agent_invocations(messages)
        assert invocations[0].tokens_per_tool_use == 2000.0
        assert invocations[0].duration_per_tool_use == 10000.0

    def test_empty_messages(self) -> None:
        assert extract_agent_invocations([]) == []
