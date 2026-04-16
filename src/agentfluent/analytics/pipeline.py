"""Analytics pipeline orchestration.

Connects parser, extractor, and analytics modules into a single
analysis pipeline. Reusable by the CLI, future webapp, and tests.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from agentfluent.agents.extractor import extract_agent_invocations
from agentfluent.analytics.agent_metrics import AgentMetrics, compute_agent_metrics
from agentfluent.analytics.tokens import TokenMetrics, compute_token_metrics
from agentfluent.analytics.tools import ToolMetrics, compute_tool_metrics
from agentfluent.core.parser import parse_session
from agentfluent.core.session import SessionMessage


@dataclass
class SessionAnalysis:
    """Complete analysis results for a single session."""

    session_path: Path
    token_metrics: TokenMetrics
    tool_metrics: ToolMetrics
    agent_metrics: AgentMetrics
    message_count: int = 0
    user_message_count: int = 0
    assistant_message_count: int = 0


@dataclass
class AnalysisResult:
    """Aggregated analysis results across one or more sessions."""

    sessions: list[SessionAnalysis] = field(default_factory=list)
    token_metrics: TokenMetrics = field(default_factory=TokenMetrics)
    tool_metrics: ToolMetrics = field(default_factory=ToolMetrics)
    agent_metrics: AgentMetrics = field(default_factory=AgentMetrics)
    session_count: int = 0


def analyze_session(
    path: Path,
    *,
    agent_filter: str | None = None,
) -> SessionAnalysis:
    """Run the full analytics pipeline on a single session file.

    Args:
        path: Path to the .jsonl session file.
        agent_filter: If set, only include this agent type in agent metrics.

    Returns:
        SessionAnalysis with token, tool, and agent metrics.
    """
    messages = parse_session(path)

    token_metrics = compute_token_metrics(messages)
    tool_metrics = compute_tool_metrics(messages)

    invocations = extract_agent_invocations(messages)
    if agent_filter:
        invocations = [
            inv for inv in invocations if inv.agent_type.lower() == agent_filter.lower()
        ]

    agent_metrics = compute_agent_metrics(
        invocations, session_total_tokens=token_metrics.total_tokens
    )

    return SessionAnalysis(
        session_path=path,
        token_metrics=token_metrics,
        tool_metrics=tool_metrics,
        agent_metrics=agent_metrics,
        message_count=len(messages),
        user_message_count=_count_type(messages, "user"),
        assistant_message_count=_count_type(messages, "assistant"),
    )


def analyze_sessions(
    paths: list[Path],
    *,
    agent_filter: str | None = None,
) -> AnalysisResult:
    """Run analytics across multiple session files and aggregate results.

    Args:
        paths: List of .jsonl session file paths.
        agent_filter: If set, only include this agent type in agent metrics.

    Returns:
        AnalysisResult with per-session and aggregated metrics.
    """
    session_analyses: list[SessionAnalysis] = []

    all_messages: list[SessionMessage] = []
    for path in paths:
        messages = parse_session(path)
        all_messages.extend(messages)

        token_metrics = compute_token_metrics(messages)
        tool_metrics = compute_tool_metrics(messages)

        invocations = extract_agent_invocations(messages)
        if agent_filter:
            invocations = [
                inv for inv in invocations if inv.agent_type.lower() == agent_filter.lower()
            ]

        agent_metrics = compute_agent_metrics(
            invocations, session_total_tokens=token_metrics.total_tokens
        )

        session_analyses.append(
            SessionAnalysis(
                session_path=path,
                token_metrics=token_metrics,
                tool_metrics=tool_metrics,
                agent_metrics=agent_metrics,
                message_count=len(messages),
                user_message_count=_count_type(messages, "user"),
                assistant_message_count=_count_type(messages, "assistant"),
            )
        )

    # Aggregate across all sessions
    agg_token = compute_token_metrics(all_messages)
    agg_tool = compute_tool_metrics(all_messages)

    all_invocations = extract_agent_invocations(all_messages)
    if agent_filter:
        all_invocations = [
            inv for inv in all_invocations if inv.agent_type.lower() == agent_filter.lower()
        ]
    agg_agent = compute_agent_metrics(all_invocations, session_total_tokens=agg_token.total_tokens)

    return AnalysisResult(
        sessions=session_analyses,
        token_metrics=agg_token,
        tool_metrics=agg_tool,
        agent_metrics=agg_agent,
        session_count=len(session_analyses),
    )


def _count_type(messages: list[SessionMessage], msg_type: str) -> int:
    """Count messages of a given type."""
    return sum(1 for m in messages if m.type == msg_type)
