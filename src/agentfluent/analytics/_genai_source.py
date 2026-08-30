"""Isolated genai-prices contact point -- the base side of the base ⊕ overlay seam.

This module is the **only** place in AgentFluent that imports from ``genai_prices``
(architect Concern 1, #545). Everything else consumes ``ModelPricing`` via
``analytics.pricing``; a genai-prices bump therefore touches one file, not many. It is
also the base side of the pricing seam formalized in #547 -- overlay levers (1h cache,
fast mode, batch/priority, ...) apply *after* the base rates this module returns.

**Static-only contract (local-first, D045 / CLAUDE.md):** this module reads only the
bundled static snapshot (``genai_prices.data.providers``) and never constructs
``UpdatePrices`` -- whose opt-in hourly GitHub fetch would introduce background network
egress, a posture violation. No network I/O occurs during rate resolution.

**Internal-surface binding (why the pin is exact):** the per-token rate table lives on
genai-prices' *internal* record (``Provider.find_model`` -> ``ModelInfo.get_prices`` ->
``ModelPrice.input_mtok`` / ...), which is outside its ``__all__`` and carries no
pre-1.0 stability promise. The public ``calc_price`` returns a computed dollar *total*,
not a rate table, and cannot supply the 5m/1h cache split ``compute_cost`` needs -- so
we bind to the internal record and pin ``genai-prices`` exactly in ``pyproject.toml``.

**The dynamic price-key registry (genai-prices >=0.1.x, #661).** ``ModelPrice`` is no longer a
fixed-field dataclass; it takes ``**price_kwargs`` over a unit registry and resolves keys through
``__getattr__``. Named-key access still works, and it now distinguishes two upstream states the
old dataclass could not tell apart. **They must never be collapsed:**

===========================  =====================================  ==========================
upstream state               meaning                                handling here
===========================  =====================================  ==========================
registered but unset         a legitimate per-model gap             ``None`` -> caller falls
                                                                    through to ``_RESIDUAL``
**deregistered**             **the binding is broken for EVERY      **loud**: WARNING, re-raise
                             model, not just this one**
===========================  =====================================  ==========================

Collapsing the second into ``None`` would route a broken binding down the same silent
``$0``-logged-at-DEBUG path that #661 exists to fix, so a required key never gets a silent
default. ``getattr(price, key, None)`` is correct only for *genuinely optional* keys -- the
first of those is #662's ``cache_write_1h_mtok``, which this module deliberately does not read
(see ``UpstreamRates``).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from genai_prices.data import (  # internal, non-``__all__`` surface -- see module docstring
    ModelPrice,
    TieredPrices,
    providers,
)

logger = logging.getLogger(__name__)

_ANTHROPIC = next((p for p in providers if p.id == "anthropic"), None)


@dataclass(frozen=True)
class UpstreamRates:
    """Anthropic base rates from genai-prices, in USD per 1M tokens.

    genai-prices models a *single* ``cache_write_mtok`` (the 5-minute-equivalent write
    rate); the 1-hour cache-write dimension (#534) has no upstream field and is supplied
    by the local overlay -- the adapter must never collapse 1h onto this 5m rate.
    """

    input: float
    output: float
    cache_write_5m: float
    cache_read: float


def _base_rate(value: Decimal | TieredPrices | None) -> float | None:
    """Return the standard (below-first-tier) rate as a float.

    A genai-prices rate field is either a scalar ``Decimal`` (flat pricing) or a
    ``TieredPrices(base, tiers)`` where ``base`` is the standard-context rate and
    ``tiers`` add context-length surcharges (e.g. the >200K tier on Sonnet). AgentFluent's
    ``_PRICING`` historically encoded the standard rate, so #545 resolves to ``base``;
    context-tier-aware pricing is future overlay work (``ModelPricing`` has no tier slot).
    """
    if value is None:
        return None
    if isinstance(value, TieredPrices):
        return float(value.base)
    return float(value)  # scalar Decimal


def _required_rate(price: ModelPrice, key: str) -> float | None:
    """Read a **required** upstream rate key, failing loudly if it is deregistered.

    Distinguishes the two states the dynamic price-key registry exposes (see the module
    docstring's table): a *registered but unset* key resolves to ``None`` -- a legitimate
    per-model gap the caller handles by falling through to the local residual -- while a
    *deregistered* key raises ``AttributeError``, meaning the adapter's binding contract has
    broken for every model at once.

    The second is logged at WARNING and re-raised rather than flattened to ``None``: returning
    ``None`` would hand a broken binding to ``pricing.get_pricing``, which maps it to an empty
    residual and then to ``$0`` logged at DEBUG -- silent catastrophic under-reporting, the exact
    failure class #661 exists to fix. The exact pin in ``pyproject.toml`` is what keeps this
    unreachable in practice; the loudness is for the pin being loosened or the install being
    broken.
    """
    try:
        return _base_rate(getattr(price, key))
    except AttributeError:
        logger.warning(
            "genai-prices deregistered the required price key '%s' -- the _genai_source "
            "binding is broken for every model, not just this one; refusing to price as $0",
            key,
        )
        raise


def _resolve_rates(
    model_ref: str, timestamp: datetime | None = None
) -> UpstreamRates | None:
    """Resolve Anthropic base rates for ``model_ref`` from the static snapshot.

    ``timestamp`` selects the rate in effect on that date via genai-prices' dated
    constraints; ``None`` -> the current rate. #545 always passes ``None`` (date-aware
    lookup is #546), but the parameter is wired now so #546 is a plumb-through rather than
    a re-architecture. Returns ``None`` when the model is not covered upstream (the caller
    then falls back to the documented local residual).

    Raises ``AttributeError`` -- deliberately, and only -- if upstream has *deregistered* one of
    the four required price keys, which breaks the binding for every model at once; see
    ``_required_rate``. A model upstream simply does not price still returns ``None``.
    """
    if _ANTHROPIC is None:  # pragma: no cover -- broken genai-prices install
        return None
    model = _ANTHROPIC.find_model(model_ref)
    if model is None:
        return None
    when = timestamp if timestamp is not None else datetime.now(UTC)
    price: ModelPrice = model.get_prices(when)
    # Required keys only. ``cache_write_1h_mtok`` is populated upstream as of 0.1.4 but is
    # deliberately NOT read here: sourcing 1h from upstream is #662, and reading it into
    # ``cache_write_5m`` would be exactly the 5m/1h collapse this adapter must never perform
    # (guarded by ``test_cache_write_binds_5m_field_not_1h`` in tests/unit/test_genai_source.py).
    input_rate = _required_rate(price, "input_mtok")
    output_rate = _required_rate(price, "output_mtok")
    cache_write = _required_rate(price, "cache_write_mtok")
    cache_read = _required_rate(price, "cache_read_mtok")
    if input_rate is None or output_rate is None or cache_write is None or cache_read is None:
        return None
    return UpstreamRates(
        input=input_rate,
        output=output_rate,
        cache_write_5m=cache_write,
        cache_read=cache_read,
    )
