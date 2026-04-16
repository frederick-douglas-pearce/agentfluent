"""Model pricing lookup for token cost estimation.

Maps Anthropic model names to per-token costs (USD per 1M tokens) for
input, output, cache_creation, and cache_read token categories.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModelPricing:
    """Per-token pricing for a single model (USD per 1M tokens)."""

    input: float
    output: float
    cache_creation: float
    cache_read: float


# Pricing data: USD per 1M tokens.
# Update this dict when Anthropic changes pricing.
_PRICING: dict[str, ModelPricing] = {
    "claude-opus-4-6": ModelPricing(
        input=15.0, output=75.0, cache_creation=18.75, cache_read=1.875,
    ),
    "claude-opus-4-5-20251101": ModelPricing(
        input=15.0, output=75.0, cache_creation=18.75, cache_read=1.875,
    ),
    "claude-sonnet-4-6": ModelPricing(
        input=3.0, output=15.0, cache_creation=3.75, cache_read=0.30,
    ),
    "claude-sonnet-4-5-20250929": ModelPricing(
        input=3.0, output=15.0, cache_creation=3.75, cache_read=0.30,
    ),
    "claude-sonnet-4-20250514": ModelPricing(
        input=3.0, output=15.0, cache_creation=3.75, cache_read=0.30,
    ),
    "claude-haiku-4-5-20251001": ModelPricing(
        input=0.80, output=4.0, cache_creation=1.0, cache_read=0.08,
    ),
}

# Aliases map short names and variant identifiers to canonical model names.
_ALIASES: dict[str, str] = {
    "opus": "claude-opus-4-6",
    "sonnet": "claude-sonnet-4-6",
    "haiku": "claude-haiku-4-5-20251001",
    "claude-opus-4-6[1m]": "claude-opus-4-6",
    "claude-sonnet-4-6[1m]": "claude-sonnet-4-6",
}

DEFAULT_MODEL = "claude-sonnet-4-6"


def get_pricing(model: str) -> ModelPricing | None:
    """Look up pricing for a model name.

    Checks exact match first, then aliases. Returns None with a warning
    if the model is unknown.
    """
    pricing = _PRICING.get(model)
    if pricing:
        return pricing

    canonical = _ALIASES.get(model)
    if canonical:
        return _PRICING.get(canonical)

    logger.warning("Unknown model '%s' -- no pricing available", model)
    return None


def compute_cost(
    pricing: ModelPricing,
    input_tokens: int,
    output_tokens: int,
    cache_creation_input_tokens: int = 0,
    cache_read_input_tokens: int = 0,
) -> float:
    """Compute dollar cost from token counts and pricing.

    Args:
        pricing: Per-token rates for the model.
        input_tokens: Number of input tokens.
        output_tokens: Number of output tokens.
        cache_creation_input_tokens: Number of cache creation tokens.
        cache_read_input_tokens: Number of cache read tokens.

    Returns:
        Total cost in USD.
    """
    return (
        input_tokens * pricing.input
        + output_tokens * pricing.output
        + cache_creation_input_tokens * pricing.cache_creation
        + cache_read_input_tokens * pricing.cache_read
    ) / 1_000_000


def get_known_models() -> list[str]:
    """Return sorted list of all known model names (excluding aliases)."""
    return sorted(_PRICING.keys())
