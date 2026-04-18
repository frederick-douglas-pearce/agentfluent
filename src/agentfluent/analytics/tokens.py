"""Token and cost analytics for session analysis.

Computes token usage totals, dollar costs, and cache efficiency from
parsed session messages. Handles mixed-model sessions with per-model
cost breakdown.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agentfluent.analytics.pricing import SYNTHETIC_MODELS, compute_cost, get_pricing
from agentfluent.core.session import SessionMessage


@dataclass
class ModelTokenBreakdown:
    """Token counts and cost for a single model within a session."""

    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    cost: float = 0.0

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
    """Aggregated token usage and cost metrics for a session."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    total_cost: float = 0.0
    cache_efficiency: float = 0.0
    """Cache efficiency as a percentage (0-100).
    Formula: cache_read / (cache_read + input + cache_creation) * 100."""

    api_call_count: int = 0
    """Number of deduplicated assistant messages (API calls)."""

    by_model: dict[str, ModelTokenBreakdown] = field(default_factory=dict)
    """Per-model token breakdown, keyed by model name."""

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_creation_input_tokens
            + self.cache_read_input_tokens
        )


def compute_token_metrics(messages: list[SessionMessage]) -> TokenMetrics:
    """Compute token usage totals and dollar costs from session messages.

    Processes only assistant messages that have usage data. Messages should
    already be deduplicated (streaming snapshots collapsed).

    Handles mixed-model sessions by computing per-model breakdowns and
    summing costs across models.

    Args:
        messages: Parsed and deduplicated session messages.

    Returns:
        TokenMetrics with totals, per-model breakdown, cost, and cache efficiency.
    """
    by_model: dict[str, ModelTokenBreakdown] = {}
    api_call_count = 0

    for msg in messages:
        if msg.type != "assistant" or msg.usage is None:
            continue

        # Skip synthetic/internal sentinel models emitted by Claude Code.
        # These are not real API calls -- no tokens billed, no pricing needed.
        if msg.model in SYNTHETIC_MODELS:
            continue

        api_call_count += 1
        model_name = msg.model or "unknown"
        usage = msg.usage

        breakdown = by_model.get(model_name)
        if breakdown is None:
            breakdown = ModelTokenBreakdown(model=model_name)
            by_model[model_name] = breakdown

        breakdown.input_tokens += usage.input_tokens
        breakdown.output_tokens += usage.output_tokens
        breakdown.cache_creation_input_tokens += usage.cache_creation_input_tokens
        breakdown.cache_read_input_tokens += usage.cache_read_input_tokens

    # Compute per-model costs
    total_cost = 0.0
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
            total_cost += breakdown.cost

    # Aggregate totals
    total_input = sum(b.input_tokens for b in by_model.values())
    total_output = sum(b.output_tokens for b in by_model.values())
    total_cache_creation = sum(b.cache_creation_input_tokens for b in by_model.values())
    total_cache_read = sum(b.cache_read_input_tokens for b in by_model.values())

    # Cache efficiency: cache_read / (cache_read + input + cache_creation)
    cache_denominator = total_cache_read + total_input + total_cache_creation
    cache_efficiency = (
        round(total_cache_read / cache_denominator * 100, 1) if cache_denominator > 0 else 0.0
    )

    return TokenMetrics(
        input_tokens=total_input,
        output_tokens=total_output,
        cache_creation_input_tokens=total_cache_creation,
        cache_read_input_tokens=total_cache_read,
        total_cost=total_cost,
        cache_efficiency=cache_efficiency,
        api_call_count=api_call_count,
        by_model=by_model,
    )
