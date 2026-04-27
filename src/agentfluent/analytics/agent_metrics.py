"""Per-agent execution metrics.

Computes invocation counts, token usage, cost estimates, and efficiency
metrics grouped by agent type. Cost is estimated via a session-level
blended per-token rate (``session.token_metrics.total_cost /
session.token_metrics.total_tokens``) applied to each agent's
``total_tokens``. This is an estimate — accurate per-agent cost
requires per-token-type splits (input / output / cache_creation /
cache_read) which #143 will retain on AgentInvocation.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agentfluent.agents.models import AgentInvocation


@dataclass
class AgentTypeMetrics:
    """Execution metrics for a single agent type."""

    agent_type: str
    is_builtin: bool
    invocation_count: int = 0
    total_tokens: int = 0
    total_tool_uses: int = 0
    total_duration_ms: int = 0
    total_cost_usd: float = 0.0
    """Estimated total cost in USD for this agent type's invocations.
    Computed from a session-level blended per-token rate; accuracy is
    bounded by the lack of per-invocation token-type splits (see #143)."""
    avg_tokens_per_tool_use: float | None = None
    avg_duration_per_tool_use: float | None = None
    avg_cost_per_invocation_usd: float | None = None
    """Estimated average cost per invocation in USD. ``None`` when
    invocation count is zero or no token data was captured."""

    @property
    def avg_tokens_per_invocation(self) -> float | None:
        if self.invocation_count > 0 and self.total_tokens > 0:
            return self.total_tokens / self.invocation_count
        return None

    @property
    def avg_duration_per_invocation(self) -> float | None:
        if self.invocation_count > 0 and self.total_duration_ms > 0:
            return self.total_duration_ms / self.invocation_count
        return None


@dataclass
class AgentMetrics:
    """Aggregated agent execution metrics for a session."""

    by_agent_type: dict[str, AgentTypeMetrics] = field(default_factory=dict)
    """Per-agent-type metrics, keyed by agent_type."""

    total_invocations: int = 0
    total_agent_tokens: int = 0
    total_agent_duration_ms: int = 0

    builtin_invocations: int = 0
    custom_invocations: int = 0

    agent_token_percentage: float = 0.0
    """Agent tokens as percentage of session total tokens (0-100).
    Set by the caller who has session-level token data."""


def compute_agent_metrics(
    invocations: list[AgentInvocation],
    session_total_tokens: int = 0,
    session_total_cost: float = 0.0,
) -> AgentMetrics:
    """Compute per-agent-type execution metrics from extracted invocations.

    Groups invocations by agent_type and computes counts, totals, and averages.
    Invocations with missing metadata are counted but excluded from averages.

    Args:
        invocations: Extracted agent invocations from a session.
        session_total_tokens: Total session tokens for computing agent percentage.
        session_total_cost: Total session cost in USD; used to derive a
            blended per-token rate that estimates per-agent cost.

    Returns:
        AgentMetrics with per-type breakdowns and summary totals.
    """
    by_type: dict[str, AgentTypeMetrics] = {}

    for inv in invocations:
        key = inv.agent_type.lower()
        metrics = by_type.get(key)
        if metrics is None:
            metrics = AgentTypeMetrics(agent_type=inv.agent_type, is_builtin=inv.is_builtin)
            by_type[key] = metrics

        metrics.invocation_count += 1

        if inv.total_tokens is not None:
            metrics.total_tokens += inv.total_tokens
        if inv.tool_uses is not None:
            metrics.total_tool_uses += inv.tool_uses
        if inv.duration_ms is not None:
            metrics.total_duration_ms += inv.duration_ms

    # Session-level blended rate: total_cost / total_tokens. Per-agent
    # cost is then `agent_tokens * rate`. This is an estimate — without
    # per-invocation input/output/cache splits (#143), an agent that uses
    # a different model mix than the session average will be misattributed.
    blended_rate = (
        session_total_cost / session_total_tokens
        if session_total_tokens > 0 and session_total_cost > 0
        else 0.0
    )

    # Compute averages and cost
    for metrics in by_type.values():
        if metrics.total_tool_uses > 0:
            if metrics.total_tokens > 0:
                metrics.avg_tokens_per_tool_use = metrics.total_tokens / metrics.total_tool_uses
            if metrics.total_duration_ms > 0:
                metrics.avg_duration_per_tool_use = (
                    metrics.total_duration_ms / metrics.total_tool_uses
                )
        if blended_rate > 0 and metrics.total_tokens > 0:
            metrics.total_cost_usd = metrics.total_tokens * blended_rate
            if metrics.invocation_count > 0:
                metrics.avg_cost_per_invocation_usd = (
                    metrics.total_cost_usd / metrics.invocation_count
                )

    # Aggregate totals
    total_invocations = sum(m.invocation_count for m in by_type.values())
    total_agent_tokens = sum(m.total_tokens for m in by_type.values())
    total_agent_duration = sum(m.total_duration_ms for m in by_type.values())
    builtin_count = sum(m.invocation_count for m in by_type.values() if m.is_builtin)
    custom_count = sum(m.invocation_count for m in by_type.values() if not m.is_builtin)

    agent_token_pct = (
        round(total_agent_tokens / session_total_tokens * 100, 1)
        if session_total_tokens > 0 and total_agent_tokens > 0
        else 0.0
    )

    return AgentMetrics(
        by_agent_type=by_type,
        total_invocations=total_invocations,
        total_agent_tokens=total_agent_tokens,
        total_agent_duration_ms=total_agent_duration,
        builtin_invocations=builtin_count,
        custom_invocations=custom_count,
        agent_token_percentage=agent_token_pct,
    )
