"""Per-agent execution metrics.

Computes invocation counts, token usage, cost, and efficiency metrics grouped
by agent type.

**Spend is sourced from subagent traces (#646).** Per-agent spend
(``spend_tokens``) is the sum of each linked trace's
``analytics.tokens.trace_spend_usage`` -- per-turn usage deduplicated within the
file (#696) and across the session's files (#701) -- and per-agent cost prices
each trace at its own model's rate through
``analytics.tokens.price_trace`` -- the same primitive the session fold uses, so
Σ per-agent cost reconciles with the session's subagent-origin ``by_model`` cost.
Trace-missing invocations add no spend and no cost; there is no blended fill-in.

``total_tokens`` is **not** spend: it sums the parent ``toolUseResult.totalTokens``
rollup, a final-turn context-size proxy (D057(1)), retained unchanged under D029.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from agentfluent.agents.models import AgentInvocation
from agentfluent.analytics.tokens import (
    price_trace,
    trace_counts_as_spend,
    trace_spend_usage,
)


def _recompute_ratios(metrics: AgentTypeMetrics) -> None:
    """Set the stored ratio fields from the stored totals.

    Shared by ``compute_agent_metrics`` (single session) and
    ``_merge_agent_metrics`` (multi-session) so the guard logic lives in
    one place.

    Every ratio divides a coverage-matched pair (#690): numerator and
    denominator are summed over the same invocations, so an invocation that
    carries only one of the two values never dilutes or inflates the ratio.
    ``avg_tool_calls_per_turn`` divides ``total_tool_uses_with_turns`` (not the
    all-invocation ``total_tool_uses``) by ``total_model_turns``;
    ``avg_tokens_per_tool_use`` and ``avg_duration_per_tool_use`` divide their
    own ``*_with_*`` pairs. ``estimated_avg_cost_per_turn_usd`` divides
    ``estimated_total_cost_usd``, so the caller must set cost before
    calling this. ``avg_turns_per_invocation`` is guarded on
    ``invocations_with_turns`` (so a 0-turn trace yields 0.0, not None);
    the per-turn ratios are guarded on ``total_model_turns`` (0 turns ->
    None, division-by-zero guard).

    Denominators (#646 AC6): the per-turn cost divides priced cost by
    ``priced_model_turns``, so an unpriced model never dilutes it.
    ``avg_tokens_per_turn`` divides trace spend by ``total_model_turns``, which
    is **not** fully coverage-matched: a trace excluded from spend (model
    ``None``, or a ``<synthetic>`` first assistant message) still contributes
    its real turns to ``total_model_turns``, diluting the ratio. That is a
    parser defect, tracked in #692 (latent: no local trace has the shape).

    **Fork traces dilute both per-turn ratios too (#704).** Spend and cost are
    deduplicated across the session's files (#701), but ``total_model_turns``
    and ``priced_model_turns`` count every turn in each file, including turns a
    fork replayed from the spawning conversation. A fork type's per-turn token
    and cost figures therefore read low; the turn-shaped metrics are fixed
    together in #704.
    """
    if metrics.invocations_with_turns > 0:
        metrics.avg_turns_per_invocation = (
            metrics.total_model_turns / metrics.invocations_with_turns
        )
    if metrics.total_tool_uses_with_tokens > 0 and metrics.total_tokens_with_tool_uses > 0:
        metrics.avg_tokens_per_tool_use = (
            metrics.total_tokens_with_tool_uses / metrics.total_tool_uses_with_tokens
        )
    if (
        metrics.total_tool_uses_with_duration > 0
        and metrics.total_duration_ms_with_tool_uses > 0
    ):
        metrics.avg_duration_per_tool_use = (
            metrics.total_duration_ms_with_tool_uses / metrics.total_tool_uses_with_duration
        )
    if metrics.total_model_turns > 0:
        metrics.avg_tool_calls_per_turn = (
            metrics.total_tool_uses_with_turns / metrics.total_model_turns
        )
        metrics.avg_tokens_per_turn = metrics.spend_tokens / metrics.total_model_turns
    if metrics.priced_model_turns > 0 and metrics.estimated_total_cost_usd > 0:
        metrics.estimated_avg_cost_per_turn_usd = (
            metrics.estimated_total_cost_usd / metrics.priced_model_turns
        )


@dataclass
class AgentTypeMetrics:
    """Execution metrics for a single agent type."""

    agent_type: str
    is_builtin: bool
    invocation_count: int = 0
    total_tokens: int = 0
    """Sum of the parent ``toolUseResult.totalTokens`` rollup. **A context-size
    proxy, not spend** (D057(1)): it equals the four components of the sibling
    ``toolUseResult.usage`` snapshot -- usually the final turn -- and is absent on
    invocations whose rollup block is missing. Retained unchanged under D029; real
    spend is ``spend_tokens``."""
    total_tool_uses: int = 0
    total_duration_ms: int = 0
    estimated_total_cost_usd: float = 0.0
    """Σ ``price_trace`` over this type's priced trace-linked invocations, each at
    its trace model's rate and the session price date (#646). Not reconciled
    against the Usage & Cost Admin API; validated by internal cross-checks only
    (D059)."""
    avg_tokens_per_tool_use: float | None = None
    """``total_tokens_with_tool_uses / total_tool_uses_with_tokens`` -- built on
    the rollup context-size proxy, **not** spend (D057(1)); see ``total_tokens``.
    Its units differ from the spend-based ``avg_tokens_per_turn``."""
    avg_duration_per_tool_use: float | None = None
    """``total_duration_ms_with_tool_uses / total_tool_uses_with_duration``."""

    # Coverage-matched pairs for the stored ratios (#690). ``tool_uses`` is
    # trace-sourced on every trace-linked invocation while ``total_tokens``
    # stays rollup-only, so the all-invocation sums above no longer cover the
    # same invocations. Each ratio divides a pair summed over the invocations
    # that carry BOTH of its values.
    total_tool_uses_with_turns: int = 0
    """Σ ``tool_uses`` over invocations with ``model_turns`` (a linked trace);
    numerator of ``avg_tool_calls_per_turn`` over ``total_model_turns``."""

    total_tokens_with_tool_uses: int = 0
    """Σ ``total_tokens`` over invocations that also carry ``tool_uses``."""

    total_tool_uses_with_tokens: int = 0
    """Σ ``tool_uses`` over invocations that also carry ``total_tokens``."""

    total_duration_ms_with_tool_uses: int = 0
    """Σ ``duration_ms`` over invocations that also carry ``tool_uses``."""

    total_tool_uses_with_duration: int = 0
    """Σ ``tool_uses`` over invocations that also carry ``duration_ms``."""

    # Active-duration aggregates (#480). ``total_duration_ms`` above is
    # raw wall-clock summed over *all* invocations and silently includes
    # user-wait time (the IDE sitting on an approval prompt while the user
    # is AFK). The three fields below let the summary table show the
    # idle-subtracted "active" duration next to wall-clock so an
    # interactive agent like ``pm`` doesn't read as a duration problem.
    #
    # Honesty constraint (#480, architect review): active duration only
    # exists for trace-linked invocations. To make the wall/active ratio
    # measure *only* idle subtraction -- not trace-coverage skew --
    # ``total_wallclock_ms_trace_linked`` sums wall-clock over the SAME
    # subset that contributed active duration, so both averages share
    # ``active_duration_invocation_count`` as their denominator. Since #690
    # both values of one invocation come from its trace.
    total_active_duration_ms: int = 0
    """Sum of active duration over trace-linked invocations with a trace
    duration: ``active_duration_ms`` (idle gaps subtracted), or the trace
    ``duration_ms`` when idle gaps could not be computed (fewer than two
    timestamped tool calls), mirroring ``active_duration_per_tool_use`` (#690)."""

    total_wallclock_ms_trace_linked: int = 0
    """Sum of raw ``duration_ms`` over the *same* trace-linked invocations
    that contributed ``total_active_duration_ms``. The wall-clock
    numerator for a coverage-matched wall/active ratio."""

    active_duration_invocation_count: int = 0
    """Count of invocations contributing to the two active totals (a linked
    trace with a ``duration_ms``). Shared denominator for the
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

    # Trace-sourced spend (#646). Two coverage counts, deliberately distinct:
    # ``spend_invocation_count`` is TOKEN coverage (trace linked and counted as
    # spend); ``priced_invocation_count`` / ``priced_model_turns`` are PRICING
    # coverage (that trace's model also had a price). Cost averages divide by the
    # priced counts so an unpriced model reads as uncovered, never as cheap.
    spend_tokens: int = 0
    """Σ ``trace_spend_usage`` tokens (per-turn usage, deduplicated within the file
    by the #696 merge and across the session's files by #701) over trace-linked
    invocations whose trace counts as spend (``trace_counts_as_spend``)."""

    spend_invocation_count: int = 0
    """Invocations contributing to ``spend_tokens``. Against
    ``invocation_count`` this is the spend-coverage figure."""

    priced_invocation_count: int = 0
    """Spend invocations whose trace model was priced -- the denominator of
    ``estimated_avg_cost_per_invocation_usd``."""

    priced_model_turns: int = 0
    """Σ ``model_turns`` over the priced invocations -- the denominator of
    ``estimated_avg_cost_per_turn_usd``."""

    # Turn-based ratios. Stored (not @property) so they serialize: this
    # is a stdlib dataclass nested in a Pydantic envelope, and Pydantic
    # serializes dataclass *fields* but not their properties. Computed in
    # compute_agent_metrics() / recomputed in _merge_agent_metrics(),
    # the same stored-field pattern as avg_tokens_per_tool_use above.
    avg_turns_per_invocation: float | None = None
    avg_tool_calls_per_turn: float | None = None
    avg_tokens_per_turn: float | None = None
    estimated_avg_cost_per_turn_usd: float | None = None

    @property
    def avg_tokens_per_invocation(self) -> float | None:
        """``total_tokens / invocation_count`` -- the rollup context-size proxy,
        **not** spend (D057(1)), averaged over all invocations including those
        with no rollup. For spend use ``avg_spend_tokens_per_invocation``."""
        if self.invocation_count > 0 and self.total_tokens > 0:
            return self.total_tokens / self.invocation_count
        return None

    @property
    def avg_duration_per_invocation(self) -> float | None:
        if self.invocation_count > 0 and self.total_duration_ms > 0:
            return self.total_duration_ms / self.invocation_count
        return None

    @property
    def avg_spend_tokens_per_invocation(self) -> float | None:
        """Average trace spend per spend-covered invocation (#646). ``None``
        when no invocation contributed spend."""
        if self.spend_invocation_count > 0:
            return self.spend_tokens / self.spend_invocation_count
        return None

    @property
    def estimated_avg_cost_per_invocation_usd(self) -> float | None:
        """Average cost per *priced* invocation (#646 AC6)."""
        if self.priced_invocation_count > 0 and self.estimated_total_cost_usd > 0:
            return self.estimated_total_cost_usd / self.priced_invocation_count
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
    """Σ ``total_tokens`` -- the rollup context-size proxy, not spend (D057(1))."""
    total_agent_spend_tokens: int = 0
    """Σ ``spend_tokens`` -- trace-sourced spend (#646)."""
    total_agent_duration_ms: int = 0

    builtin_invocations: int = 0
    custom_invocations: int = 0

    agent_token_percentage: float = 0.0
    """Agent spend tokens (``total_agent_spend_tokens``) as a percentage of the
    session total tokens (0-100). The session total already folds the same
    depth-1 trace usage, so this is a true subset share (#646 AC5)."""

    total_model_turns: int = 0
    """Sum of ``total_model_turns`` across all agent types (#467)."""


def agent_token_percentage(agent_spend_tokens: int, session_total_tokens: int) -> float:
    """Agent spend as a percentage of the session total, rounded to 0.1.

    Shared by ``compute_agent_metrics`` and ``_merge_agent_metrics`` (#646 AC5) so
    both read the numerator from trace spend, never from the rollup proxy.
    """
    if session_total_tokens > 0 and agent_spend_tokens > 0:
        return round(agent_spend_tokens / session_total_tokens * 100, 1)
    return 0.0


def compute_agent_metrics(
    invocations: list[AgentInvocation],
    session_total_tokens: int = 0,
    *,
    timestamp: datetime | None = None,
) -> AgentMetrics:
    """Compute per-agent-type execution metrics from extracted invocations.

    Groups invocations by agent_type and computes counts, totals, and averages.
    Invocations with missing metadata are counted but excluded from averages.

    Args:
        invocations: Extracted agent invocations from a session.
        session_total_tokens: Total session tokens for computing agent percentage.
        timestamp: Session price date (#546); pass the same value the session's
            token metrics were priced at so per-agent cost reconciles with them.

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
            if inv.total_tokens is not None:
                metrics.total_tokens_with_tool_uses += inv.total_tokens
                metrics.total_tool_uses_with_tokens += inv.tool_uses
            if inv.duration_ms is not None:
                metrics.total_duration_ms_with_tool_uses += inv.duration_ms
                metrics.total_tool_uses_with_duration += inv.tool_uses
        if inv.duration_ms is not None:
            metrics.total_duration_ms += inv.duration_ms
        # Active duration exists only for trace-linked invocations. Sum
        # active and its coverage-matched wall-clock over the same subset
        # (#480) so a downstream wall/active ratio reflects idle
        # subtraction, not which invocations happened to have a trace.
        # On a trace-linked invocation both come from the trace (#690).
        # Where idle gaps could not be computed (fewer than two timestamped
        # tool calls), the trace span stands in as active -- same source, no
        # idle subtracted -- so the invocation still counts as covered.
        if inv.trace is not None and inv.duration_ms is not None:
            active = inv.active_duration_ms
            metrics.total_active_duration_ms += (
                active if active is not None else inv.duration_ms
            )
            metrics.total_wallclock_ms_trace_linked += inv.duration_ms
            metrics.active_duration_invocation_count += 1
        # ``is not None`` (not truthiness): a 0-turn trace still has turn
        # data, so it counts toward invocations_with_turns and yields a
        # legitimate 0.0 average, distinct from a trace-missing gap.
        if inv.model_turns is not None:
            metrics.total_model_turns += inv.model_turns
            metrics.invocations_with_turns += 1
            if inv.tool_uses is not None:
                metrics.total_tool_uses_with_turns += inv.tool_uses
        # Trace-sourced spend and cost (#646). Trace-missing invocations add
        # nothing here -- no blended fill-in (#646 AC4).
        trace = inv.trace
        if trace is not None and trace_counts_as_spend(trace):
            metrics.spend_tokens += trace_spend_usage(trace).total_tokens
            metrics.spend_invocation_count += 1
            cost = price_trace(trace, timestamp=timestamp)
            if cost is not None:
                metrics.estimated_total_cost_usd += cost
                metrics.priced_invocation_count += 1
                metrics.priced_model_turns += trace.model_turns

    # Cost is already accumulated: cost-per-turn divides it.
    for metrics in by_type.values():
        _recompute_ratios(metrics)

    # Aggregate totals
    total_invocations = sum(m.invocation_count for m in by_type.values())
    total_agent_tokens = sum(m.total_tokens for m in by_type.values())
    total_agent_spend_tokens = sum(m.spend_tokens for m in by_type.values())
    total_agent_duration = sum(m.total_duration_ms for m in by_type.values())
    total_turns = sum(m.total_model_turns for m in by_type.values())
    builtin_count = sum(m.invocation_count for m in by_type.values() if m.is_builtin)
    custom_count = sum(m.invocation_count for m in by_type.values() if not m.is_builtin)

    agent_token_pct = agent_token_percentage(total_agent_spend_tokens, session_total_tokens)

    return AgentMetrics(
        by_agent_type=by_type,
        total_invocations=total_invocations,
        total_agent_tokens=total_agent_tokens,
        total_agent_spend_tokens=total_agent_spend_tokens,
        total_agent_duration_ms=total_agent_duration,
        builtin_invocations=builtin_count,
        custom_invocations=custom_count,
        agent_token_percentage=agent_token_pct,
        total_model_turns=total_turns,
    )
