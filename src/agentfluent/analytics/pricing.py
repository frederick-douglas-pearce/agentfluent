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
# Source: https://platform.claude.com/docs/en/about-claude/pricing
# Last verified: 2026-04-18
# Cache write prices reflect the 5-minute TTL tier (1.25x input).
# Update this dict when Anthropic changes pricing.
_PRICING: dict[str, ModelPricing] = {
    # Opus 4.5, 4.6, 4.7 share the same pricing tier ($5 / $25 / $6.25 / $0.50).
    "claude-opus-4-7": ModelPricing(
        input=5.0, output=25.0, cache_creation=6.25, cache_read=0.50,
    ),
    "claude-opus-4-6": ModelPricing(
        input=5.0, output=25.0, cache_creation=6.25, cache_read=0.50,
    ),
    "claude-opus-4-5-20251101": ModelPricing(
        input=5.0, output=25.0, cache_creation=6.25, cache_read=0.50,
    ),
    # Sonnet 4, 4.5, 4.6 share the same pricing tier ($3 / $15 / $3.75 / $0.30).
    "claude-sonnet-4-6": ModelPricing(
        input=3.0, output=15.0, cache_creation=3.75, cache_read=0.30,
    ),
    "claude-sonnet-4-5-20250929": ModelPricing(
        input=3.0, output=15.0, cache_creation=3.75, cache_read=0.30,
    ),
    "claude-sonnet-4-20250514": ModelPricing(
        input=3.0, output=15.0, cache_creation=3.75, cache_read=0.30,
    ),
    # Haiku 4.5 is $1 / $5 / $1.25 / $0.10 (distinct from Haiku 3.5's $0.80 / $4).
    "claude-haiku-4-5-20251001": ModelPricing(
        input=1.0, output=5.0, cache_creation=1.25, cache_read=0.10,
    ),
}

# Aliases map short names and variant identifiers to canonical model names.
# Aliases mirror the ID forms used elsewhere in the codebase (e.g.
# diagnostics/delegation.py recommends `claude-haiku-4-5`, the
# undated alias — pricing must resolve the same string).
_ALIASES: dict[str, str] = {
    "opus": "claude-opus-4-7",
    "sonnet": "claude-sonnet-4-6",
    "haiku": "claude-haiku-4-5-20251001",
    "claude-haiku-4-5": "claude-haiku-4-5-20251001",
    "claude-opus-4-7[1m]": "claude-opus-4-7",
    "claude-opus-4-6[1m]": "claude-opus-4-6",
    "claude-sonnet-4-6[1m]": "claude-sonnet-4-6",
}

# Sentinel model names emitted by Claude Code for synthetic/internal messages.
# These are not real API model calls and should be skipped before pricing lookup.
SYNTHETIC_MODELS: frozenset[str] = frozenset({"<synthetic>"})


DEFAULT_MODEL = "claude-sonnet-4-6"


def get_pricing(model: str) -> ModelPricing | None:
    """Look up pricing for a model name.

    Checks exact match first, then aliases. Returns None and logs at DEBUG
    level if the model is unknown. The caller is expected to skip synthetic
    sentinel values (see ``SYNTHETIC_MODELS``) before invoking this function.
    """
    pricing = _PRICING.get(model)
    if pricing:
        return pricing

    canonical = _ALIASES.get(model)
    if canonical:
        return _PRICING.get(canonical)

    logger.debug("Unknown model '%s' -- no pricing available", model)
    return None


def compute_cost(
    pricing: ModelPricing,
    input_tokens: int,
    output_tokens: int,
    cache_creation_input_tokens: int = 0,
    cache_read_input_tokens: int = 0,
) -> float:
    """Compute dollar cost in USD from token counts and pricing rates."""
    return (
        input_tokens * pricing.input
        + output_tokens * pricing.output
        + cache_creation_input_tokens * pricing.cache_creation
        + cache_read_input_tokens * pricing.cache_read
    ) / 1_000_000


def get_known_models() -> list[str]:
    """Return sorted list of all known model names (excluding aliases)."""
    return sorted(_PRICING.keys())
