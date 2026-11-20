"""Token and cost analytics for session analysis.

Computes token usage totals, dollar costs, and cache efficiency from
parsed session messages. Handles mixed-model sessions with per-model
cost breakdown and parent-vs-subagent origin attribution.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Literal

from agentfluent.analytics.pricing import SYNTHETIC_MODELS, compute_cost, get_pricing
from agentfluent.core.session import SessionMessage, Usage

if TYPE_CHECKING:
    from agentfluent.traces.models import SubagentTrace

logger = logging.getLogger(__name__)

Origin = Literal["parent", "subagent"]


@dataclass
class ModelTokenBreakdown:
    """Token counts and cost for a single (model, origin) row.

    ``origin`` distinguishes whether the usage came from the parent
    session's assistant messages or from a subagent trace. Two rows can
    share a model but differ in origin (e.g., Opus used in both the
    parent thread and a delegated subagent run).
    """

    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_5m_tokens: int = 0
    cache_creation_1h_tokens: int = 0
    cost: float = 0.0
    origin: Origin = "parent"
    unpriced_tokens: int = 0
    """Tokens in this row whose model had no price at the session's price date,
    so they are in the token counts and not in ``cost`` (#676/#688). ``cost``
    stays ``0.0``-defaulted, so this is the only signal that a row's ``0.0`` is
    "unpriced", not "free". A per-session parent row is all-or-nothing; a subagent
    row sums its unpriced traces; and a merged run-level row (either origin) sums
    sessions priced at different dates (#546), so it can be partly unpriced."""

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_creation_input_tokens
            + self.cache_read_input_tokens
        )


@dataclass
class TokenMetrics:
    """Aggregated token usage and cost metrics for a session.

    Top-level totals are *comprehensive*: parent-thread + subagent
    contributions summed. The ``by_model`` list decomposes them by
    ``(model, origin)``. Per #227, this matches users' intuition that
    "total cost" reflects what the session actually spent.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    total_cost: float = 0.0
    cache_efficiency: float = 0.0
    """Cache efficiency as a percentage (0-100).
    Formula: cache_read / (cache_read + input + cache_creation) * 100."""

    api_call_count: int = 0
    """Number of deduplicated assistant messages (API calls). Counts
    parent-session messages only — subagent traces' per-call usage is
    not reliably attributable per call, so we sum at the trace level
    instead (see ``SubagentTrace.usage``; the spend a trace contributes, with
    turns counted elsewhere in the session removed, is ``trace_spend_usage``)."""

    by_model: list[ModelTokenBreakdown] = field(default_factory=list)
    """Per-(model, origin) token breakdown. Schema v2: list, not dict.
    The ``origin`` field on each row distinguishes parent vs subagent.
    See #227."""

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_creation_input_tokens
            + self.cache_read_input_tokens
        )


def _accumulate_usage(breakdown: ModelTokenBreakdown, usage: Usage) -> None:
    """Fold one message/trace ``Usage`` into a per-model breakdown.

    The single population path for both the parent and subagent aggregation
    loops, so the cache-write TTL split (5m/1h) stays in lockstep with the
    authoritative total and any future usage field is wired in exactly one
    place rather than two copy-pasted blocks.
    """
    breakdown.input_tokens += usage.input_tokens
    breakdown.output_tokens += usage.output_tokens
    breakdown.cache_creation_input_tokens += usage.cache_creation_input_tokens
    breakdown.cache_read_input_tokens += usage.cache_read_input_tokens
    breakdown.cache_creation_5m_tokens += usage.cache_creation_5m_input_tokens
    breakdown.cache_creation_1h_tokens += usage.cache_creation_1h_input_tokens


def _cost_of(
    breakdown: ModelTokenBreakdown, timestamp: datetime | None = None,
) -> float | None:
    """Price one breakdown row at its model's rate; ``None`` iff the model is unpriced.

    The single call site of ``get_pricing`` / ``compute_cost`` in this module (#646 AC1):
    parent rows, subagent rows and per-agent spend (``price_trace``) all price here, so
    the ``Usage`` -> rate mapping (the 5m/1h cache-write split) lives on one path.

    **Linearity assumption.** Pricing is linear in tokens today (base tier only -- the
    upstream adapter discards context tiers -- and no caller passes request multipliers
    or surcharges), so pricing an aggregate equals summing per-trace prices up to float
    rounding. Subagent rows are priced per trace and summed
    (``compute_subagent_token_metrics``), so per-agent cost reconciles with the session
    fold by construction. **No test guards linearity**: a non-linear change here moves
    the session fold and per-agent attribution identically. Per-request levers --
    context tiers (#682), multipliers or surcharges (#537-#539) -- need per-request
    usage, which a trace's aggregated ``usage`` does not carry; #682 owns that guard.
    """
    pricing = get_pricing(breakdown.model, timestamp)
    if pricing is None:
        return None
    return compute_cost(
        pricing,
        breakdown.input_tokens,
        breakdown.output_tokens,
        breakdown.cache_creation_5m_tokens,
        breakdown.cache_read_input_tokens,
        cache_creation_1h_tokens=breakdown.cache_creation_1h_tokens,
    )


def _populate_costs(
    by_model: dict[tuple[str, str], ModelTokenBreakdown],
    timestamp: datetime | None = None,
) -> float:
    """Compute per-row costs in place, return the summed total.

    ``timestamp`` is the session's price date (first-message timestamp, #546); it is
    forwarded to ``get_pricing`` so each row is priced at the rate in effect when the
    session ran. ``None`` → latest rate (the fallback for a missing/malformed session
    timestamp, and the default for callers that do not price by date).
    """
    total = 0.0
    for breakdown in by_model.values():
        cost = _cost_of(breakdown, timestamp)
        if cost is None:
            breakdown.unpriced_tokens = breakdown.total_tokens
        else:
            breakdown.cost = cost
            total += cost
    return total


def trace_counts_as_spend(trace: SubagentTrace) -> bool:
    """Whether a subagent trace contributes to subagent spend (#646).

    The single inclusion predicate shared by the session fold
    (``compute_subagent_token_metrics``) and per-agent attribution
    (``analytics.agent_metrics``), so the two cannot drift on *which* traces count:
    the trace must carry a model, and not the ``<synthetic>`` sentinel (no real API
    call). It does not test depth -- which traces reach the fold is ownership
    (``traces.coverage.collect_session_traces``, #648), not this predicate's.
    """
    return trace.model is not None and trace.model not in SYNTHETIC_MODELS


def trace_spend_usage(trace: SubagentTrace) -> Usage:
    """The usage a trace contributes to spend (#701).

    ``trace.spend_usage`` once ``dedupe_trace_turns`` has run on the session,
    else the per-file ``trace.usage`` -- which is also what a trace dedup skipped
    returns (no ``turn_usages``, or failing ``trace_counts_as_spend``), so callers
    must gate on ``trace_counts_as_spend`` first. Every spend reader goes through it: the
    session fold (``compute_subagent_token_metrics``), ``price_trace``,
    per-agent ``spend_tokens`` (``analytics.agent_metrics``) and the verbose
    Per-Invocation Detail Tokens column (``cli.formatters.table``). So a turn
    dropped as a duplicate is dropped from all of them, and #646's per-agent ↔
    ``by_model`` reconciliation keeps holding.
    """
    return trace.spend_usage if trace.spend_usage is not None else trace.usage


def trace_spend(trace: SubagentTrace | None) -> tuple[Usage, str] | None:
    """A trace's spend and the model it ran on, or ``None`` when it has none (#675).

    The ``trace_counts_as_spend`` / ``trace_spend_usage`` pair every spend reader
    uses (#646, #701), packaged for the diagnostics savings estimates so their
    dollars and the analytics fold rest on one definition of spend. ``None`` for a
    missing trace or one that does not count as spend -- never a zero usage.
    """
    if trace is None or trace.model is None or not trace_counts_as_spend(trace):
        return None
    return trace_spend_usage(trace), trace.model


def counted_parent_message_ids(messages: Iterable[SessionMessage]) -> set[str]:
    """``message_id`` s of the parent-session turns ``compute_token_metrics``
    counts (#701).

    Uses the same predicate (``counts_as_parent_call``) so a trace turn is
    never dropped as "already counted" on the strength of a main-session turn
    the parent fold skipped (no usage, or ``<synthetic>``). Empty ids are not
    ids -- the same reading the #696 fragment merge takes.
    """
    return {
        msg.message_id
        for msg in messages
        if counts_as_parent_call(msg) and msg.message_id
    }


@dataclass(frozen=True)
class TraceDedupStats:
    """What ``dedupe_trace_turns`` dropped as already counted (#648 AC4)."""

    dropped_turns: int = 0
    dropped_tokens: int = 0


def dedupe_trace_turns(
    counted_message_ids: Iterable[str],
    depth_one_in_invocation_order: Sequence[SubagentTrace],
    rest: Iterable[SubagentTrace] = (),
) -> TraceDedupStats:
    """Set each trace's ``spend_usage`` so no API turn is counted twice (#701).

    Exclusivity -- every turn's usage lives in exactly one file -- holds for
    almost every trace, but **not** for fork agents, which replay the spawning
    conversation: sibling fork traces share ``message.id`` s with each other and
    with the main session. Summing per-file usage then counts those turns more
    than once. This is the hard guard: first occurrence wins.

    **Precedence is fixed here**, so every caller gets the same answer:
    ``counted_message_ids`` (the main session's counted turns -- see
    ``counted_parent_message_ids``) first, then ``depth_one_in_invocation_order``
    as given (the caller is the only one who knows invocation order), then
    ``rest`` sorted by ``(depth, agent_id)``.

    A turn with no ``message_id`` -- ``None`` or ``""``, matching the #696
    merge's notion of "no id" -- always counts (it cannot be matched). A trace
    with no ``turn_usages`` (not parser-built) is left alone and spends its
    ``usage``. A trace *object* appearing twice is processed once; two distinct
    objects for the same ``agent_id`` are not merged (#705).

    **Only a trace that counts as spend may claim an id** -- the trace-side twin
    of ``counted_parent_message_ids``' rule for the main session. A trace failing
    ``trace_counts_as_spend`` (no model, or a ``<synthetic>`` first assistant
    message -- the #692 shape) is skipped entirely and its ``spend_usage`` stays
    ``None``: every spend reader excludes it anyway, so letting it claim a turn
    it shares with a later fork would leave that turn counted nowhere.

    Mutates ``spend_usage`` only; the public per-file ``usage`` is unchanged.
    Returns how many turns, and how many tokens, were dropped as duplicates.
    """
    ordered = [
        *depth_one_in_invocation_order,
        *sorted(rest, key=lambda trace: (trace.depth, trace.agent_id)),
    ]
    seen = set(counted_message_ids)
    processed: set[int] = set()
    dropped_turns = 0
    dropped_tokens = 0
    for trace in ordered:
        if id(trace) in processed:
            continue
        processed.add(id(trace))
        if not trace.turn_usages or not trace_counts_as_spend(trace):
            continue
        spend = Usage()
        for turn in trace.turn_usages:
            if turn.message_id:
                if turn.message_id in seen:
                    dropped_turns += 1
                    dropped_tokens += turn.usage.total_tokens
                    continue
                seen.add(turn.message_id)
            spend = spend + turn.usage
        trace.spend_usage = spend
    return TraceDedupStats(dropped_turns=dropped_turns, dropped_tokens=dropped_tokens)


def price_usage(
    usage: Usage, model: str, *, timestamp: datetime | None = None,
) -> float | None:
    """Price one ``Usage`` at ``model``'s rate; ``None`` iff ``model`` is unpriced.

    The public token -> dollar primitive (#675): ``price_trace`` and the
    model-switch savings figures (``diagnostics.model_routing`` and the
    duration-outlier rule) route through it -- ``diagnostics.parent_workload``'s
    offload estimate still calls ``compute_cost`` directly until #707 -- and it
    routes through ``_cost_of``,
    so all five usage components -- including the 5m/1h cache-write split -- map to
    rates on a single path (#646 AC1).

    **Model-switch counterfactual.** A savings estimate prices the *same* usage at
    two models: the token mix actually observed, cache reads and writes included, at
    the alternative model's rates. That deliberately ignores tokenizer differences
    between model generations, a different turn count on the other model, and
    per-request levers (context tiers #682, multipliers and surcharges #537-#539).
    ``diagnostics.parent_workload``'s offload estimate uses a *different*
    counterfactual on purpose -- a delegated subagent loses the parent's cache, so
    its alternative side re-prices cache reads as fresh input -- and the two are not
    meant to converge (#707 routes that estimate's pricing, not its counterfactual,
    through here).
    """
    breakdown = ModelTokenBreakdown(model=model)
    _accumulate_usage(breakdown, usage)
    return _cost_of(breakdown, timestamp)


def price_trace(
    trace: SubagentTrace, *, timestamp: datetime | None = None,
) -> float | None:
    """Price one subagent trace's deduplicated per-turn usage at its model's rate.

    Returns ``None`` only when the trace's model is unpriced. **Precondition:**
    ``trace_counts_as_spend(trace)`` -- a trace that does not count as spend raises
    ``ValueError`` rather than being priced as $0, so "unpriced" and "excluded" never
    share a return value. ``timestamp`` is the session price date (#546).
    """
    if not trace_counts_as_spend(trace) or trace.model is None:
        raise ValueError(
            f"price_trace: trace {trace.agent_id!r} does not count as spend "
            f"(model={trace.model!r}); check trace_counts_as_spend first"
        )
    return price_usage(trace_spend_usage(trace), trace.model, timestamp=timestamp)


def _aggregate_totals(
    rows: Iterable[ModelTokenBreakdown],
) -> tuple[int, int, int, int, float, float]:
    """Sum totals across breakdown rows. Returns (input, output, cache_creation,
    cache_read, total_cost, cache_efficiency).

    Cache efficiency formula: cache_read / (cache_read + input + cache_creation) * 100.
    """
    total_input = 0
    total_output = 0
    total_cache_creation = 0
    total_cache_read = 0
    total_cost = 0.0
    for r in rows:
        total_input += r.input_tokens
        total_output += r.output_tokens
        total_cache_creation += r.cache_creation_input_tokens
        total_cache_read += r.cache_read_input_tokens
        total_cost += r.cost
    cache_denom = total_cache_read + total_input + total_cache_creation
    cache_efficiency = (
        round(total_cache_read / cache_denom * 100, 1) if cache_denom > 0 else 0.0
    )
    return (
        total_input, total_output, total_cache_creation, total_cache_read,
        total_cost, cache_efficiency,
    )


def session_price_timestamp(messages: list[SessionMessage]) -> datetime | None:
    """Return the session's price date: the first message carrying a timestamp.

    This is the single source of truth for pricing a session by the rate in effect
    when it ran (#546, AC#2). Returns ``None`` when no message has a timestamp
    (missing/malformed → latest-rate fallback, AC#3), which ``get_pricing`` treats as
    "latest". The parser sets ``SessionMessage.timestamp`` to ``None`` for absent or
    unparseable timestamps, so a malformed value never reaches pricing as a bad date.
    """
    price_date = next((m.timestamp for m in messages if m.timestamp is not None), None)
    if price_date is None:
        # AC3 (#546): no usable session timestamp (missing or parser-coerced malformed) ->
        # cost falls back to the latest rate. Log at DEBUG so the fallback is observable.
        logger.debug(
            "Session has no usable message timestamp; pricing at the latest rate."
        )
    return price_date


def counts_as_parent_call(msg: SessionMessage) -> bool:
    """Whether a parent-session message is a counted API call: an assistant
    message with usage, not the ``<synthetic>`` sentinel (no real API call, no
    pricing). Shared by ``compute_token_metrics``,
    ``counted_parent_message_ids`` and the SDK main-session savings estimate
    (``diagnostics.model_routing``, #675) so they cannot drift (#701)."""
    return (
        msg.type == "assistant"
        and msg.usage is not None
        and msg.model not in SYNTHETIC_MODELS
    )


def compute_token_metrics(
    messages: list[SessionMessage], *, timestamp: datetime | None = None
) -> TokenMetrics:
    """Compute parent-session token usage totals and dollar costs.

    Processes only assistant messages that have usage data. Messages should
    already be deduplicated (streaming snapshots collapsed). Subagent
    contributions are not included here — call
    ``compute_subagent_token_metrics`` separately and merge into the
    parent ``TokenMetrics`` (see ``analytics.pipeline.analyze_session``).

    ``timestamp`` (#546) is the session price date used for date-aware rate lookup;
    ``None`` → latest rate. The pipeline derives it once via ``session_price_timestamp``
    and passes the same value here and to ``compute_subagent_token_metrics`` so parent
    and subagent rows price at one consistent session date.

    Returns:
        TokenMetrics with parent-only totals, per-model breakdown
        (origin="parent"), cost, and cache efficiency.
    """
    by_model: dict[tuple[str, str], ModelTokenBreakdown] = {}
    api_call_count = 0

    for msg in messages:
        usage = msg.usage
        if usage is None or not counts_as_parent_call(msg):
            continue

        api_call_count += 1
        model_name = msg.model or "unknown"
        key = (model_name, "parent")

        breakdown = by_model.get(key)
        if breakdown is None:
            breakdown = ModelTokenBreakdown(model=model_name, origin="parent")
            by_model[key] = breakdown

        _accumulate_usage(breakdown, usage)

    _populate_costs(by_model, timestamp)
    rows = list(by_model.values())
    (
        total_input, total_output, total_cache_creation, total_cache_read,
        total_cost, cache_efficiency,
    ) = _aggregate_totals(rows)

    return TokenMetrics(
        input_tokens=total_input,
        output_tokens=total_output,
        cache_creation_input_tokens=total_cache_creation,
        cache_read_input_tokens=total_cache_read,
        total_cost=total_cost,
        cache_efficiency=cache_efficiency,
        api_call_count=api_call_count,
        by_model=rows,
    )


def compute_subagent_token_metrics(
    traces: list[SubagentTrace], *, timestamp: datetime | None = None
) -> list[ModelTokenBreakdown]:
    """Aggregate subagent-trace token usage into per-model breakdowns.

    Uses ``trace_spend_usage`` -- the trace-level aggregate with turns already
    counted elsewhere in the session removed (#701) -- as the
    authoritative source — per-call ``SubagentToolCall.usage`` is left
    at zero by the parser (see traces/parser.py docstring). Skips
    traces that do not count as spend (``trace_counts_as_spend``: no model, or
    the ``<synthetic>`` sentinel).

    ``timestamp`` (#546) is the parent session's price date, applied to subagent rows
    too so the whole session prices at one consistent date; ``None`` → latest rate.

    Inclusion is ``trace_counts_as_spend`` (shared with per-agent attribution, #646).
    Each row's cost is the sum of its traces' ``price_trace`` costs.

    Returns one ``ModelTokenBreakdown`` per distinct subagent model,
    each carrying ``origin="subagent"``. Costs are populated. The
    caller is responsible for merging these rows into a parent
    ``TokenMetrics``.
    """
    by_model: dict[tuple[str, str], ModelTokenBreakdown] = {}
    for trace in traces:
        if not trace_counts_as_spend(trace) or trace.model is None:
            continue
        usage = trace_spend_usage(trace)
        key = (trace.model, "subagent")
        breakdown = by_model.get(key)
        if breakdown is None:
            breakdown = ModelTokenBreakdown(model=trace.model, origin="subagent")
            by_model[key] = breakdown
        _accumulate_usage(breakdown, usage)
        # Priced per trace and summed (not aggregate-then-price), so per-agent cost
        # reconciles with this row by construction (#646 AC8; see ``_cost_of``).
        cost = price_trace(trace, timestamp=timestamp)
        if cost is None:
            breakdown.unpriced_tokens += usage.total_tokens
        else:
            breakdown.cost += cost

    return list(by_model.values())


def fold_subagent_metrics_in(
    parent: TokenMetrics, subagent_rows: list[ModelTokenBreakdown],
) -> TokenMetrics:
    """Return a new ``TokenMetrics`` with subagent rows folded into parent.

    Top-level totals (input/output/cache + cost) become comprehensive.
    The ``by_model`` list is the union of parent and subagent rows
    (rows with the same ``model`` but different ``origin`` stay
    distinct). Cache efficiency is recomputed from the comprehensive
    totals. When ``subagent_rows`` is empty, returns ``parent`` itself
    (identity preserved) so the merge is free in the common no-trace case.
    """
    if not subagent_rows:
        return parent

    combined_rows = list(parent.by_model) + list(subagent_rows)
    (
        total_input, total_output, total_cache_creation, total_cache_read,
        total_cost, cache_efficiency,
    ) = _aggregate_totals(combined_rows)

    return TokenMetrics(
        input_tokens=total_input,
        output_tokens=total_output,
        cache_creation_input_tokens=total_cache_creation,
        cache_read_input_tokens=total_cache_read,
        total_cost=total_cost,
        cache_efficiency=cache_efficiency,
        api_call_count=parent.api_call_count,
        by_model=combined_rows,
    )
