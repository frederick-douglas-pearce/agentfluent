"""Model pricing lookup for token cost estimation.

Maps Anthropic model names to per-token costs (USD per 1M tokens) for
input, output, cache_creation, and cache_read token categories.

Base rates are sourced from genai-prices (upstream, D045) via the isolated
``_genai_source`` adapter; a small documented local residual (``_RESIDUAL``) supplies any
model genai-prices does not cover. This module is the public pricing surface -- nothing
outside ``_genai_source`` imports ``genai_prices`` directly.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from agentfluent.analytics._genai_source import _resolve_rates

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModelPricing:
    """Per-token pricing for a single model (USD per 1M tokens).

    Anthropic prices cache *writes* at two TTL-dependent rates:
    ``cache_creation_5m`` (5-minute tier, 1.25x base input) and
    ``cache_creation_1h`` (1-hour tier, 2x base input). The session JSONL
    splits write tokens between the two via ``usage.cache_creation`` (see
    #534); 1h is commonly the dominant TTL in Claude Code, so billing the
    whole sum at the 5m rate materially under-reports cost.

    ``cache_creation_1h`` is derived as ``2x input`` when left at the
    ``-1.0`` sentinel, so the 2x multiplier has a single source of truth
    and any ``ModelPricing`` built without it still prices 1h writes
    correctly. genai-prices (the upstream pricing source, see
    docs/COST_MODEL.md) models only a single 5m-equivalent
    ``cache_write_mtok`` — the 1h dimension is supplied by this overlay
    and has no upstream field; an adapter mapping must not collapse it
    back onto the 5m rate.
    """

    input: float
    output: float
    cache_creation_5m: float
    cache_read: float
    cache_creation_1h: float = -1.0

    def __post_init__(self) -> None:
        if self.cache_creation_1h < 0:
            object.__setattr__(self, "cache_creation_1h", self.input * 2.0)


# Curated model-id registry: the Anthropic models AgentFluent knows about. Rates are
# sourced upstream-first from genai-prices (D045) via ``_resolve_rates``; ``_RESIDUAL``
# supplies a documented local fallback for any id genai-prices does not cover.
#
# The #545 coverage probe found genai-prices==0.0.71 covers ALL of these ids, with
# base-tier rates identical to the former hand-maintained ``_PRICING`` dict:
#   Opus 4.5/4.6/4.7/4.8  -> $5 / $25 / $6.25 / $0.50
#   Sonnet 4.0/4.5/4.6    -> $3 / $15 / $3.75 / $0.30   (4.5 is context-tiered upstream;
#                                                        base tier taken -- see _genai_source)
#   Haiku 4.5             -> $1 / $5  / $1.25 / $0.10
# so ``_RESIDUAL`` is currently empty. The golden-rate regression test locks these values.
_KNOWN_MODELS: frozenset[str] = frozenset(
    {
        "claude-opus-4-8",
        "claude-opus-4-7",
        "claude-opus-4-6",
        "claude-opus-4-5-20251101",
        "claude-sonnet-4-6",
        "claude-sonnet-4-5-20250929",
        "claude-sonnet-4-20250514",
        "claude-haiku-4-5-20251001",
    }
)

# Documented local residual: model id -> ModelPricing for any model genai-prices lacks.
# Empty as of genai-prices==0.0.71 (see _KNOWN_MODELS above). Add an entry ONLY for a model
# the coverage probe shows upstream does not price -- this is the local-overlay escape
# hatch, not a re-introduction of the hand-maintained rate table.
_RESIDUAL: dict[str, ModelPricing] = {}

# Aliases map short names and variant identifiers to canonical model names.
# Aliases mirror the ID forms used elsewhere in the codebase (e.g.
# diagnostics/delegation.py recommends `claude-haiku-4-5`, the
# undated alias — pricing must resolve the same string).
_ALIASES: dict[str, str] = {
    "opus": "claude-opus-4-8",
    "sonnet": "claude-sonnet-4-6",
    "haiku": "claude-haiku-4-5-20251001",
    "claude-haiku-4-5": "claude-haiku-4-5-20251001",
    "claude-opus-4-8[1m]": "claude-opus-4-8",
    "claude-opus-4-7[1m]": "claude-opus-4-7",
    "claude-opus-4-6[1m]": "claude-opus-4-6",
    "claude-sonnet-4-6[1m]": "claude-sonnet-4-6",
}

# Sentinel model names emitted by Claude Code for synthetic/internal messages.
# These are not real API model calls and should be skipped before pricing lookup.
SYNTHETIC_MODELS: frozenset[str] = frozenset({"<synthetic>"})


DEFAULT_MODEL = "claude-sonnet-4-6"


# Canonical model-id constants for the model-routing recommendation targets (promoted from
# diagnostics/_complexity.py, #252 fold, so pricing and routing share one source of truth).
# Undated to match ``_ALIASES`` resolution.
MODEL_HAIKU = "claude-haiku-4-5"
MODEL_SONNET = "claude-sonnet-4-6"
# MODEL_OPUS intentionally lags the ``opus`` alias (which resolves to claude-opus-4-8): the
# model-routing recommendation *target* is 4-7, a distinct concern from the default-pricing
# alias. Do NOT "fix" this to 4-8 -- it would silently change the recommendation target
# (#252/#545).
MODEL_OPUS = "claude-opus-4-7"


def get_pricing(model: str) -> ModelPricing | None:
    """Look up pricing for a model name.

    Resolves aliases first (short names, ``[1m]`` suffixes), then sources the rate
    upstream-first from genai-prices, falling back to the documented local residual.
    Returns None and logs at DEBUG level if the model is unknown. The caller is expected
    to skip synthetic sentinel values (see ``SYNTHETIC_MODELS``) before invoking this.
    """
    canonical = _ALIASES.get(model, model)
    if canonical not in _KNOWN_MODELS:
        logger.debug("Unknown model '%s' -- no pricing available", model)
        return None

    rates = _resolve_rates(canonical)
    if rates is not None:
        # cache_creation_1h is derived (2x input) by ModelPricing.__post_init__ -- the 1h
        # overlay dimension (#534) is never collapsed onto the upstream 5m cache_write.
        return ModelPricing(
            input=rates.input,
            output=rates.output,
            cache_creation_5m=rates.cache_write_5m,
            cache_read=rates.cache_read,
        )

    residual = _RESIDUAL.get(canonical)
    if residual is not None:
        return residual

    logger.debug("Known model '%s' has neither upstream nor residual pricing", canonical)
    return None


def compute_cost(
    pricing: ModelPricing,
    input_tokens: int,
    output_tokens: int,
    cache_creation_5m_tokens: int = 0,
    cache_read_input_tokens: int = 0,
    cache_creation_1h_tokens: int = 0,
) -> float:
    """Compute dollar cost in USD from token counts and pricing rates.

    The cache-write total is split by TTL: ``cache_creation_5m_tokens`` is
    billed at the 5-minute write rate, ``cache_creation_1h_tokens`` at the
    1-hour rate (2x base). The parameter is named for the 5m bucket (not the
    undifferentiated total) on purpose — passing ``Usage``'s authoritative
    ``cache_creation_input_tokens`` here alongside a separate 1h count would
    double-bill the 1h portion. Callers that only know the undifferentiated
    total pass it here, which prices the whole sum at the 5m rate — the
    documented fallback for sessions whose JSONL lacks the
    ``usage.cache_creation`` TTL split (see #534).
    """
    return (
        input_tokens * pricing.input
        + output_tokens * pricing.output
        + cache_creation_5m_tokens * pricing.cache_creation_5m
        + cache_creation_1h_tokens * pricing.cache_creation_1h
        + cache_read_input_tokens * pricing.cache_read
    ) / 1_000_000


def get_known_models() -> list[str]:
    """Return the sorted curated model set (excluding aliases).

    This is the curated registry AgentFluent supports (``_KNOWN_MODELS``), NOT the full
    genai-prices catalog (which carries many older Anthropic ids).
    """
    return sorted(_KNOWN_MODELS)
