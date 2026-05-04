"""Analytics pipeline orchestration.

Connects parser, extractor, and analytics modules into a single
analysis pipeline. Reusable by the CLI, future webapp, and tests.
"""

from __future__ import annotations

import logging
from pathlib import Path

from pydantic import BaseModel, Field

from agentfluent.agents.extractor import extract_agent_invocations
from agentfluent.agents.models import AgentInvocation
from agentfluent.analytics.agent_metrics import (
    AgentMetrics,
    AgentTypeMetrics,
    compute_agent_metrics,
)
from agentfluent.analytics.tokens import (
    ModelTokenBreakdown,
    TokenMetrics,
    compute_token_metrics,
)
from agentfluent.analytics.tools import (
    ConcentrationEntry,
    ToolMetrics,
    compute_tool_metrics,
)
from agentfluent.core.parser import parse_session
from agentfluent.core.session import SessionMessage
from agentfluent.diagnostics.mcp_assessment import (
    McpToolCall,
    extract_mcp_calls_from_messages,
)
from agentfluent.diagnostics.models import DiagnosticsResult
from agentfluent.traces.discovery import discover_session_subagents
from agentfluent.traces.linker import link_traces
from agentfluent.traces.models import SubagentTrace
from agentfluent.traces.parser import parse_subagent_trace

logger = logging.getLogger(__name__)


class SessionAnalysis(BaseModel):
    """Complete analysis results for a single session."""

    session_path: Path
    token_metrics: TokenMetrics
    tool_metrics: ToolMetrics
    agent_metrics: AgentMetrics
    invocations: list[AgentInvocation] = Field(default_factory=list)
    mcp_tool_calls: list[McpToolCall] = Field(default_factory=list)
    """Parent-session MCP tool calls (those made directly in the main
    session, outside any Agent delegation). Subagent-trace MCP calls
    are already captured on ``invocations[i].trace.tool_calls`` and
    not duplicated here."""

    messages: list[SessionMessage] = Field(default_factory=list)
    """Parsed parent-session messages. Retained so downstream
    diagnostics — currently only #189's parent-thread offload-candidate
    pipeline — can re-walk the session without re-parsing the JSONL.

    **Memory tradeoff.** The dominant cost is ``ToolUseBlock.input``
    payloads (Edit/Write file contents, Bash output, large Read
    results) carried on assistant messages. Typical session sizes are
    fine; large ``--latest N`` runs over heavy sessions can grow
    proportionally. v0.6 follow-up if profiling proves it: extract
    bursts during ``analyze_session`` and store ``bursts: list[ToolBurst]``
    here instead, dropping ``input`` dicts at parse time."""

    message_count: int = 0
    user_message_count: int = 0
    assistant_message_count: int = 0


class AnalysisResult(BaseModel):
    """Aggregated analysis results across one or more sessions."""

    sessions: list[SessionAnalysis] = Field(default_factory=list)
    token_metrics: TokenMetrics = Field(default_factory=TokenMetrics)
    tool_metrics: ToolMetrics = Field(default_factory=ToolMetrics)
    agent_metrics: AgentMetrics = Field(default_factory=AgentMetrics)
    session_count: int = 0
    diagnostics: DiagnosticsResult | None = None


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

    invocations = _link_subagent_traces(invocations, path)
    mcp_tool_calls = extract_mcp_calls_from_messages(messages)

    agent_metrics = compute_agent_metrics(
        invocations,
        session_total_tokens=token_metrics.total_tokens,
        session_total_cost=token_metrics.total_cost,
    )

    return SessionAnalysis(
        session_path=path,
        token_metrics=token_metrics,
        tool_metrics=tool_metrics,
        agent_metrics=agent_metrics,
        invocations=invocations,
        mcp_tool_calls=mcp_tool_calls,
        messages=messages,
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

    Calls analyze_session per file, then merges results mathematically
    rather than re-processing raw messages.

    Args:
        paths: List of .jsonl session file paths.
        agent_filter: If set, only include this agent type in agent metrics.

    Returns:
        AnalysisResult with per-session and aggregated metrics.
    """
    session_analyses = [analyze_session(p, agent_filter=agent_filter) for p in paths]

    if not session_analyses:
        return AnalysisResult()

    agg_token = _merge_token_metrics([s.token_metrics for s in session_analyses])
    agg_tool = _merge_tool_metrics([s.tool_metrics for s in session_analyses])
    agg_agent = _merge_agent_metrics(
        [s.agent_metrics for s in session_analyses],
        session_total_tokens=agg_token.total_tokens,
    )

    return AnalysisResult(
        sessions=session_analyses,
        token_metrics=agg_token,
        tool_metrics=agg_tool,
        agent_metrics=agg_agent,
        session_count=len(session_analyses),
    )


def _merge_token_metrics(metrics_list: list[TokenMetrics]) -> TokenMetrics:
    """Merge multiple TokenMetrics by summing per-model breakdowns."""
    merged_models: dict[str, ModelTokenBreakdown] = {}
    api_call_count = 0

    for tm in metrics_list:
        api_call_count += tm.api_call_count
        for model_name, breakdown in tm.by_model.items():
            existing = merged_models.get(model_name)
            if existing is None:
                merged_models[model_name] = ModelTokenBreakdown(
                    model=model_name,
                    input_tokens=breakdown.input_tokens,
                    output_tokens=breakdown.output_tokens,
                    cache_creation_input_tokens=breakdown.cache_creation_input_tokens,
                    cache_read_input_tokens=breakdown.cache_read_input_tokens,
                    cost=breakdown.cost,
                )
            else:
                existing.input_tokens += breakdown.input_tokens
                existing.output_tokens += breakdown.output_tokens
                existing.cache_creation_input_tokens += breakdown.cache_creation_input_tokens
                existing.cache_read_input_tokens += breakdown.cache_read_input_tokens
                existing.cost += breakdown.cost

    total_input = sum(b.input_tokens for b in merged_models.values())
    total_output = sum(b.output_tokens for b in merged_models.values())
    total_cache_creation = sum(b.cache_creation_input_tokens for b in merged_models.values())
    total_cache_read = sum(b.cache_read_input_tokens for b in merged_models.values())
    total_cost = sum(b.cost for b in merged_models.values())

    cache_denom = total_cache_read + total_input + total_cache_creation
    cache_efficiency = (
        round(total_cache_read / cache_denom * 100, 1) if cache_denom > 0 else 0.0
    )

    return TokenMetrics(
        input_tokens=total_input,
        output_tokens=total_output,
        cache_creation_input_tokens=total_cache_creation,
        cache_read_input_tokens=total_cache_read,
        total_cost=total_cost,
        cache_efficiency=cache_efficiency,
        api_call_count=api_call_count,
        by_model=merged_models,
    )


def _merge_tool_metrics(metrics_list: list[ToolMetrics]) -> ToolMetrics:
    """Merge multiple ToolMetrics by summing frequency dicts."""
    merged_counts: dict[str, int] = {}
    for tm in metrics_list:
        for name, count in tm.tool_frequency.items():
            merged_counts[name] = merged_counts.get(name, 0) + count

    if not merged_counts:
        return ToolMetrics()

    sorted_tools = sorted(merged_counts.items(), key=lambda x: (-x[1], x[0]))
    sorted_freq = dict(sorted_tools)
    total = sum(merged_counts.values())

    concentration: list[ConcentrationEntry] = []
    cumulative = 0
    for i, (_name, count) in enumerate(sorted_tools, start=1):
        cumulative += count
        concentration.append(
            ConcentrationEntry(
                top_n=i,
                call_count=cumulative,
                percentage=round(cumulative / total * 100, 1),
            )
        )

    return ToolMetrics(
        tool_frequency=sorted_freq,
        unique_tool_count=len(merged_counts),
        total_tool_calls=total,
        concentration=concentration,
    )


def _merge_agent_metrics(
    metrics_list: list[AgentMetrics],
    session_total_tokens: int = 0,
) -> AgentMetrics:
    """Merge multiple AgentMetrics by summing per-type breakdowns."""
    merged: dict[str, AgentTypeMetrics] = {}

    for am in metrics_list:
        for key, m in am.by_agent_type.items():
            existing = merged.get(key)
            if existing is None:
                merged[key] = AgentTypeMetrics(
                    agent_type=m.agent_type,
                    is_builtin=m.is_builtin,
                    invocation_count=m.invocation_count,
                    total_tokens=m.total_tokens,
                    total_tool_uses=m.total_tool_uses,
                    total_duration_ms=m.total_duration_ms,
                    estimated_total_cost_usd=m.estimated_total_cost_usd,
                )
            else:
                existing.invocation_count += m.invocation_count
                existing.total_tokens += m.total_tokens
                existing.total_tool_uses += m.total_tool_uses
                existing.total_duration_ms += m.total_duration_ms
                existing.estimated_total_cost_usd += m.estimated_total_cost_usd

    # estimated_total_cost_usd is summed at each session's blended rate
    # so the per-invocation average property reads correctly. Tool-use
    # averages have to be recomputed because the per-tool-use ratio
    # changes when invocations from different sessions merge.
    for m in merged.values():
        if m.total_tool_uses > 0:
            if m.total_tokens > 0:
                m.avg_tokens_per_tool_use = m.total_tokens / m.total_tool_uses
            if m.total_duration_ms > 0:
                m.avg_duration_per_tool_use = m.total_duration_ms / m.total_tool_uses

    total_invocations = sum(m.invocation_count for m in merged.values())
    total_agent_tokens = sum(m.total_tokens for m in merged.values())
    total_agent_duration = sum(m.total_duration_ms for m in merged.values())
    builtin_count = sum(m.invocation_count for m in merged.values() if m.is_builtin)
    custom_count = sum(m.invocation_count for m in merged.values() if not m.is_builtin)

    agent_token_pct = (
        round(total_agent_tokens / session_total_tokens * 100, 1)
        if session_total_tokens > 0 and total_agent_tokens > 0
        else 0.0
    )

    return AgentMetrics(
        by_agent_type=merged,
        total_invocations=total_invocations,
        total_agent_tokens=total_agent_tokens,
        total_agent_duration_ms=total_agent_duration,
        builtin_invocations=builtin_count,
        custom_invocations=custom_count,
        agent_token_percentage=agent_token_pct,
    )


def _link_subagent_traces(
    invocations: list[AgentInvocation], session_path: Path,
) -> list[AgentInvocation]:
    """Discover subagent trace files for ``session_path`` and attach them
    to matching invocations.

    Scoped per session: subagent files for a session ``<uuid>.jsonl`` live
    under ``<uuid>/subagents/``. Lazy-loads only traces whose ``agent_id``
    appears in ``invocations``; skipped files cost one dict lookup each.
    Orphan traces (file exists, no matching invocation) are debug-logged.
    """
    # Discover subagent files for this session and build the lookup map.
    session_dir = session_path.parent / session_path.stem
    subagent_files = discover_session_subagents(session_dir)
    path_map = {info.agent_id: info.path for info in subagent_files}

    # Lazy loader: only parses traces for invocations that actually exist.
    def loader(agent_id: str) -> SubagentTrace | None:
        file_path = path_map.get(agent_id)
        if file_path is None:
            return None
        try:
            return parse_subagent_trace(file_path)
        except (FileNotFoundError, ValueError):
            logger.debug(
                "Skipping malformed or missing subagent trace: %s", file_path,
            )
            return None

    invocations = link_traces(invocations, loader)

    # Orphans: trace files with no matching invocation.
    linked_ids = {inv.agent_id for inv in invocations if inv.trace is not None}
    for orphan_id in path_map.keys() - linked_ids:
        logger.debug(
            "Orphan subagent trace (no matching invocation): agent_id=%s",
            orphan_id,
        )

    return invocations


def _count_type(messages: list[SessionMessage], msg_type: str) -> int:
    """Count messages of a given type."""
    return sum(1 for m in messages if m.type == msg_type)
