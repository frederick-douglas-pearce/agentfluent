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


def _recompute_turn_ratios(metrics: AgentTypeMetrics) -> None:
    """Set the four turn-based ratio fields from the stored totals.

    Shared by ``compute_agent_metrics`` (single session) and
    ``_merge_agent_metrics`` (multi-session) so the guard logic lives in
    one place. ``estimated_avg_cost_per_turn_usd`` divides
    ``estimated_total_cost_usd``, so the caller must set cost before
    calling this. ``avg_turns_per_invocation`` is guarded on
    ``invocations_with_turns`` (so a 0-turn trace yields 0.0, not None);
    the per-turn ratios are guarded on ``total_model_turns`` (0 turns ->
    None, division-by-zero guard).
    """
    if metrics.invocations_with_turns > 0:
        metrics.avg_turns_per_invocation = (
            metrics.total_model_turns / metrics.invocations_with_turns
        )
    if metrics.total_model_turns > 0:
        metrics.avg_tool_calls_per_turn = (
            metrics.total_tool_uses / metrics.total_model_turns
        )
        metrics.avg_tokens_per_turn = metrics.total_tokens / metrics.total_model_turns
        if metrics.estimated_total_cost_usd > 0:
            metrics.estimated_avg_cost_per_turn_usd = (
                metrics.estimated_total_cost_usd / metrics.total_model_turns
            )


@dataclass
class AgentTypeMetrics:
    """Execution metrics for a single agent type."""

    agent_type: str
    is_builtin: bool
    invocation_count: int = 0
    total_tokens: int = 0
    total_tool_uses: int = 0
    total_duration_ms: int = 0
    estimated_total_cost_usd: float = 0.0
    """Estimate; see module docstring. Bounded by #143."""
    avg_tokens_per_tool_use: float | None = None
    avg_duration_per_tool_use: float | None = None

    # Active-duration aggregates (#480). ``total_duration_ms`` above is
    # raw wall-clock summed over *all* invocations and silently includes
    # user-wait time (the IDE sitting on an approval prompt while the user
    # is AFK). The three fields below let the summary table show the
    # idle-subtracted "active" duration next to wall-clock so an
    # interactive agent like ``pm`` doesn't read as a duration problem.
    #
    # Honesty constraint (#480, architect review): active duration only
    # exists for trace-linked invocations (~80% of the dogfood corpus).
    # To make the wall/active ratio measure *only* idle subtraction --
    # not trace-coverage skew -- ``total_wallclock_ms_trace_linked``
    # sums wall-clock over the SAME subset that contributed active
    # duration, so both averages share ``active_duration_invocation_count``
    # as their denominator.
    total_active_duration_ms: int = 0
    """Sum of ``active_duration_ms`` over invocations that had a linked
    trace (idle gaps subtracted)."""

    total_wallclock_ms_trace_linked: int = 0
    """Sum of raw ``duration_ms`` over the *same* trace-linked invocations
    that contributed ``total_active_duration_ms``. The wall-clock
    numerator for a coverage-matched wall/active ratio."""

    active_duration_invocation_count: int = 0
    """Count of invocations contributing to the two active totals
    (``active_duration_ms is not None``). Shared denominator for the
    active and trace-linked-wall per-call averages, and the coverage
    figure against ``invocation_count``. Mirrors the
    ``invocations_with_turns`` pattern."""

    total_model_turns: int = 0
    """Sum of ``model_turns`` across this agent type's invocations,
    counting only invocations where ``model_turns is not None`` (a
    subagent trace was linked). One model turn is one merged assistant
    message -- one API round-trip (#467)."""

    invocations_with_turns: int = 0
    """Count of this agent type's invocations that had turn data
    (``model_turns is not None``). The denominator for
    ``avg_turns_per_invocation`` -- distinct from ``invocation_count``,
    which includes trace-missing invocations. Lets a consumer see how
    much of the population the turn averages actually cover (#467)."""

    # Turn-based ratios. Stored (not @property) so they serialize: this
    # is a stdlib dataclass nested in a Pydantic envelope, and Pydantic
    # serializes dataclass *fields* but not their properties. Computed in
    # compute_agent_metrics() / recomputed in _merge_agent_metrics(),
    # mirroring avg_tokens_per_tool_use above.
    avg_turns_per_invocation: float | None = None
    avg_tool_calls_per_turn: float | None = None
    avg_tokens_per_turn: float | None = None
    estimated_avg_cost_per_turn_usd: float | None = None

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

    @property
    def estimated_avg_cost_per_invocation_usd(self) -> float | None:
        if self.invocation_count > 0 and self.estimated_total_cost_usd > 0:
            return self.estimated_total_cost_usd / self.invocation_count
        return None

    @property
    def avg_active_duration_per_invocation(self) -> float | None:
        """Average idle-subtracted duration (ms) per invocation, over the
        trace-linked subset. ``None`` when no invocation had a trace."""
        if self.active_duration_invocation_count > 0 and self.total_active_duration_ms > 0:
            return self.total_active_duration_ms / self.active_duration_invocation_count
        return None

    @property
    def avg_wallclock_per_trace_linked_invocation(self) -> float | None:
        """Average raw wall-clock (ms) per invocation over the *same*
        trace-linked subset as :attr:`avg_active_duration_per_invocation`,
        so the two are directly comparable. ``None`` when no invocation
        had a trace."""
        if (
            self.active_duration_invocation_count > 0
            and self.total_wallclock_ms_trace_linked > 0
        ):
            return self.total_wallclock_ms_trace_linked / self.active_duration_invocation_count
        return None

    @property
    def wallclock_active_ratio(self) -> float | None:
        """Wall-clock / active over the coverage-matched trace-linked
        subset. >1 means idle (user-wait) time inflated wall-clock;
        a large ratio marks an interactive-pattern agent whose headline
        wall-clock duration would otherwise mislead. ``None`` when no
        active duration is available."""
        if self.total_active_duration_ms > 0 and self.total_wallclock_ms_trace_linked > 0:
            return self.total_wallclock_ms_trace_linked / self.total_active_duration_ms
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

    total_model_turns: int = 0
    """Sum of ``total_model_turns`` across all agent types (#467)."""


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
        # Active duration exists only for trace-linked invocations. Sum
        # active and its coverage-matched wall-clock over the same subset
        # (#480) so a downstream wall/active ratio reflects idle
        # subtraction, not which invocations happened to have a trace.
        # ``duration_ms`` is guaranteed present here: a linked trace
        # implies parent toolUseResult metadata, but guard anyway so a
        # degenerate trace can't desync the two sums from their count.
        if inv.active_duration_ms is not None and inv.duration_ms is not None:
            metrics.total_active_duration_ms += inv.active_duration_ms
            metrics.total_wallclock_ms_trace_linked += inv.duration_ms
            metrics.active_duration_invocation_count += 1
        # ``is not None`` (not truthiness): a 0-turn trace still has turn
        # data, so it counts toward invocations_with_turns and yields a
        # legitimate 0.0 average, distinct from a trace-missing gap.
        if inv.model_turns is not None:
            metrics.total_model_turns += inv.model_turns
            metrics.invocations_with_turns += 1

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
            metrics.estimated_total_cost_usd = metrics.total_tokens * blended_rate
        # After cost is set: cost-per-turn divides the value above.
        _recompute_turn_ratios(metrics)

    # Aggregate totals
    total_invocations = sum(m.invocation_count for m in by_type.values())
    total_agent_tokens = sum(m.total_tokens for m in by_type.values())
    total_agent_duration = sum(m.total_duration_ms for m in by_type.values())
    total_turns = sum(m.total_model_turns for m in by_type.values())
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
        total_model_turns=total_turns,
    )
