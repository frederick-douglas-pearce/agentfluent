"""Token and cost analytics for session analysis.

Computes token usage totals, dollar costs, and cache efficiency from
parsed session messages. Handles mixed-model sessions with per-model
cost breakdown and parent-vs-subagent origin attribution.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from agentfluent.analytics.pricing import SYNTHETIC_MODELS, compute_cost, get_pricing
from agentfluent.core.session import SessionMessage

if TYPE_CHECKING:
    from agentfluent.traces.models import SubagentTrace

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
    cost: float = 0.0
    origin: Origin = "parent"

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
    instead (see ``SubagentTrace.usage``)."""

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


def _populate_costs(by_model: dict[tuple[str, str], ModelTokenBreakdown]) -> float:
    """Compute per-row costs in place, return the summed total."""
    total = 0.0
    for breakdown in by_model.values():
        pricing = get_pricing(breakdown.model)
        if pricing:
            breakdown.cost = compute_cost(
                pricing,
                breakdown.input_tokens,
                breakdown.output_tokens,
                breakdown.cache_creation_input_tokens,
                breakdown.cache_read_input_tokens,
            )
            total += breakdown.cost
    return total


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


def compute_token_metrics(messages: list[SessionMessage]) -> TokenMetrics:
    """Compute parent-session token usage totals and dollar costs.

    Processes only assistant messages that have usage data. Messages should
    already be deduplicated (streaming snapshots collapsed). Subagent
    contributions are not included here — call
    ``compute_subagent_token_metrics`` separately and merge into the
    parent ``TokenMetrics`` (see ``analytics.pipeline.analyze_session``).

    Returns:
        TokenMetrics with parent-only totals, per-model breakdown
        (origin="parent"), cost, and cache efficiency.
    """
    by_model: dict[tuple[str, str], ModelTokenBreakdown] = {}
    api_call_count = 0

    for msg in messages:
        if msg.type != "assistant" or msg.usage is None:
            continue
        # <synthetic> is a Claude Code sentinel — no real API call, no pricing.
        if msg.model in SYNTHETIC_MODELS:
            continue

        api_call_count += 1
        model_name = msg.model or "unknown"
        usage = msg.usage
        key = (model_name, "parent")

        breakdown = by_model.get(key)
        if breakdown is None:
            breakdown = ModelTokenBreakdown(model=model_name, origin="parent")
            by_model[key] = breakdown

        breakdown.input_tokens += usage.input_tokens
        breakdown.output_tokens += usage.output_tokens
        breakdown.cache_creation_input_tokens += usage.cache_creation_input_tokens
        breakdown.cache_read_input_tokens += usage.cache_read_input_tokens

    _populate_costs(by_model)
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
    traces: list[SubagentTrace],
) -> list[ModelTokenBreakdown]:
    """Aggregate subagent-trace token usage into per-model breakdowns.

    Uses ``SubagentTrace.usage`` (the trace-level aggregate) as the
    authoritative source — per-call ``SubagentToolCall.usage`` is left
    at zero by the parser (see traces/parser.py docstring). Skips
    traces with no model and traces whose usage is wholly zero (no
    assistant messages).

    Returns one ``ModelTokenBreakdown`` per distinct subagent model,
    each carrying ``origin="subagent"``. Costs are populated. The
    caller is responsible for merging these rows into a parent
    ``TokenMetrics``.
    """
    by_model: dict[tuple[str, str], ModelTokenBreakdown] = {}
    for trace in traces:
        if trace.model is None or trace.model in SYNTHETIC_MODELS:
            continue
        usage = trace.usage
        if usage is None:
            continue
        key = (trace.model, "subagent")
        breakdown = by_model.get(key)
        if breakdown is None:
            breakdown = ModelTokenBreakdown(model=trace.model, origin="subagent")
            by_model[key] = breakdown
        breakdown.input_tokens += usage.input_tokens
        breakdown.output_tokens += usage.output_tokens
        breakdown.cache_creation_input_tokens += usage.cache_creation_input_tokens
        breakdown.cache_read_input_tokens += usage.cache_read_input_tokens

    _populate_costs(by_model)
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
