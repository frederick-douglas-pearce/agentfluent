"""Tests for MCP usage extraction (diagnostics/mcp_assessment.py).

Covers ``parse_mcp_tool_name``, message-level extraction with its
tool_use / tool_result pairing + ``is_error`` priority ladder, and
aggregation across trace and parent-session sources.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agentfluent.agents.models import AgentInvocation
from agentfluent.analytics.pipeline import analyze_session
from agentfluent.core.session import ContentBlock, SessionMessage
from agentfluent.diagnostics.mcp_assessment import (
    McpToolCall,
    extract_mcp_calls_from_messages,
    extract_mcp_usage,
    parse_mcp_tool_name,
)
from agentfluent.traces.models import SubagentToolCall, SubagentTrace
from tests._builders import (
    assistant_message,
    tool_result_block,
    tool_use_block,
    user_message,
)


def _assistant_with_mcp(
    tool_use_id: str, tool_name: str, *, message_id: str = "msg_a",
) -> SessionMessage:
    return SessionMessage(
        type="assistant",
        message_id=message_id,
        content_blocks=[
            ContentBlock(
                type="tool_use", id=tool_use_id, name=tool_name, input={},
            ),
        ],
    )


def _user_with_result(
    tool_use_id: str, text: str = "ok", *, is_error: bool | None = None,
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


def _inv_with_trace(tool_calls: list[SubagentToolCall]) -> AgentInvocation:
    trace = SubagentTrace(
        agent_id="ag-1",
        agent_type="general-purpose",
        delegation_prompt="p",
        tool_calls=tool_calls,
    )
    return AgentInvocation(
        agent_type="general-purpose",
        description="d",
        prompt="p",
        tool_use_id="toolu_test",
        trace=trace,
    )


class TestParseMcpToolName:
    def test_simple_server_and_tool(self) -> None:
        assert parse_mcp_tool_name("mcp__github__create_issue") == (
            "github", "create_issue",
        )

    def test_server_with_internal_underscores(self) -> None:
        assert parse_mcp_tool_name("mcp__claude_ai_Gmail__authenticate") == (
            "claude_ai_Gmail", "authenticate",
        )

    def test_tool_with_leading_underscore(self) -> None:
        # First `__` after the prefix delimits server from tool, so
        # the triple-underscore boundary here leaves `_internal_sync`
        # as the tool — preserving leading-underscore tool names.
        assert parse_mcp_tool_name("mcp__srv___internal_sync") == (
            "srv", "_internal_sync",
        )

    def test_empty_server_returns_none(self) -> None:
        # mcp____tool has empty server between the two __ delimiters.
        assert parse_mcp_tool_name("mcp____tool") is None

    def test_empty_tool_returns_none(self) -> None:
        assert parse_mcp_tool_name("mcp__srv__") is None

    def test_non_mcp_prefix_returns_none(self) -> None:
        assert parse_mcp_tool_name("Bash") is None
        assert parse_mcp_tool_name("mcp_github_create") is None  # single underscore
        assert parse_mcp_tool_name("") is None


class TestExtractMcpCallsFromMessages:
    def test_success_pair_yields_non_error_call(self) -> None:
        messages = [
            _assistant_with_mcp("tu-1", "mcp__github__create_issue"),
            _user_with_result("tu-1", text="created", is_error=False),
        ]
        calls = extract_mcp_calls_from_messages(messages)
        assert calls == [
            McpToolCall(
                server_name="github", tool_name="create_issue", is_error=False,
            ),
        ]

    def test_explicit_is_error_true_propagates(self) -> None:
        messages = [
            _assistant_with_mcp("tu-2", "mcp__github__get_pr"),
            _user_with_result("tu-2", text="failed", is_error=True),
        ]
        calls = extract_mcp_calls_from_messages(messages)
        assert calls[0].is_error is True

    def test_no_paired_result_defaults_to_not_error(self) -> None:
        messages = [_assistant_with_mcp("tu-3", "mcp__github__create_issue")]
        calls = extract_mcp_calls_from_messages(messages)
        assert len(calls) == 1
        assert calls[0].is_error is False

    def test_error_regex_fallback_when_is_error_absent(self) -> None:
        messages = [
            _assistant_with_mcp("tu-4", "mcp__github__read"),
            _user_with_result(
                "tu-4", text="unable to reach server", is_error=None,
            ),
        ]
        calls = extract_mcp_calls_from_messages(messages)
        assert calls[0].is_error is True  # "unable to" matches ERROR_REGEX

    def test_explicit_is_error_false_wins_over_error_keyword(self) -> None:
        # Documents the ERROR_REGEX fallback's limited scope: explicit
        # is_error=False is trusted even when the text contains an
        # error keyword. This is the "error" word appearing as normal
        # content, e.g., "reviewed error handling in the PR."
        messages = [
            _assistant_with_mcp("tu-5", "mcp__github__review"),
            _user_with_result(
                "tu-5",
                text="reviewed error handling in the diff",
                is_error=False,
            ),
        ]
        calls = extract_mcp_calls_from_messages(messages)
        assert calls[0].is_error is False

    def test_non_mcp_tool_use_blocks_ignored(self) -> None:
        messages = [
            SessionMessage(
                type="assistant",
                message_id="msg_mix",
                content_blocks=[
                    ContentBlock(
                        type="tool_use", id="tu-6a", name="Bash", input={},
                    ),
                    ContentBlock(
                        type="tool_use",
                        id="tu-6b",
                        name="mcp__github__create_issue",
                        input={},
                    ),
                    ContentBlock(
                        type="tool_use", id="tu-6c", name="Read", input={},
                    ),
                ],
            ),
            _user_with_result("tu-6b", is_error=False),
        ]
        calls = extract_mcp_calls_from_messages(messages)
        assert len(calls) == 1
        assert calls[0].server_name == "github"


class TestExtractMcpUsage:
    def test_trace_only_path(self) -> None:
        inv = _inv_with_trace([
            SubagentToolCall(
                tool_name="mcp__github__create_issue",
                input_summary="", result_summary="", is_error=False,
            ),
            SubagentToolCall(
                tool_name="mcp__github__get_pr",
                input_summary="", result_summary="", is_error=True,
            ),
            SubagentToolCall(
                tool_name="Bash",  # non-MCP, should be skipped
                input_summary="", result_summary="", is_error=False,
            ),
        ])
        usage = extract_mcp_usage([inv])
        assert "github" in usage
        gh = usage["github"]
        assert gh.total_calls == 2
        assert gh.error_count == 1
        assert gh.unique_tools == ["create_issue", "get_pr"]

    def test_session_only_path(self) -> None:
        session_calls = [
            McpToolCall(server_name="slack", tool_name="send", is_error=False),
            McpToolCall(server_name="slack", tool_name="send", is_error=True),
        ]
        usage = extract_mcp_usage([], session_mcp_calls=session_calls)
        slack = usage["slack"]
        assert slack.total_calls == 2
        assert slack.error_count == 1
        assert slack.unique_tools == ["send"]

    def test_both_sources_aggregated_per_server(self) -> None:
        inv = _inv_with_trace([
            SubagentToolCall(
                tool_name="mcp__github__create_issue",
                input_summary="", result_summary="", is_error=False,
            ),
        ])
        session_calls = [
            McpToolCall(
                server_name="github", tool_name="get_file", is_error=False,
            ),
            McpToolCall(
                server_name="slack", tool_name="send", is_error=False,
            ),
        ]
        usage = extract_mcp_usage([inv], session_mcp_calls=session_calls)
        assert set(usage) == {"github", "slack"}
        assert usage["github"].total_calls == 2
        # unique_tools is the union across both sources, sorted.
        assert usage["github"].unique_tools == ["create_issue", "get_file"]
        assert usage["slack"].total_calls == 1

    def test_empty_inputs_return_empty_dict(self) -> None:
        assert extract_mcp_usage([]) == {}
        assert extract_mcp_usage([], session_mcp_calls=[]) == {}

    def test_invocation_without_trace_silently_skipped(self) -> None:
        inv_no_trace = AgentInvocation(
            agent_type="general-purpose",
            description="d",
            prompt="p",
            tool_use_id="toolu_x",
        )
        assert extract_mcp_usage([inv_no_trace]) == {}


class TestAnalyzeSessionWiresInMcpCalls:
    """End-to-end: analyze_session surfaces MCP calls on SessionAnalysis."""

    def test_analyze_session_populates_mcp_tool_calls(
        self, write_jsonl: Any, tmp_path: Path,
    ) -> None:
        path = write_jsonl(
            "session_mcp.jsonl",
            [
                user_message("kick off"),
                assistant_message([
                    tool_use_block(
                        "tu-mcp", name="mcp__github__create_issue",
                    ),
                ]),
                user_message([tool_result_block("tu-mcp", is_error=False)]),
            ],
        )
        result = analyze_session(path)
        assert len(result.mcp_tool_calls) == 1
        assert result.mcp_tool_calls[0].server_name == "github"
        assert result.mcp_tool_calls[0].tool_name == "create_issue"
        assert result.mcp_tool_calls[0].is_error is False

    def test_analyze_session_with_no_mcp_tools_yields_empty_list(
        self, basic_session_path: Path,
    ) -> None:
        result = analyze_session(basic_session_path)
        assert result.mcp_tool_calls == []
