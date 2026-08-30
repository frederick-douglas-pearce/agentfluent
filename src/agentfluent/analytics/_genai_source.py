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

Collapsing the second into ``None`` would price every model at ``$0`` while reporting success,
so a required key never gets a silent default. ``getattr(price, key, None)`` is correct only for
*genuinely optional* keys -- the first of those is #662's ``cache_write_1h_mtok``, which this
module deliberately does not read (see ``UpstreamRates``).

**What the first row does NOT buy you.** ``_RESIDUAL`` is the documented escape hatch, but it is
currently ``{}`` (``pricing._RESIDUAL``), so a *curated* model missing one required key resolves
to ``None`` for the whole model and prices at ``$0`` -- the other three rates are discarded with
it. That is a narrower version of the same silent-underreporting hazard, one model at a time.
``pricing.get_pricing`` therefore logs that specific case at **WARNING**, not DEBUG: a model we
curated but cannot price is always a defect worth surfacing, whether the cause is partial
upstream coverage or an empty residual.
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

    ``cache_write_5m`` binds the upstream ``cache_write_mtok`` (the 5-minute-equivalent write
    rate). The 1-hour dimension (#534) is supplied by the local overlay
    (``ModelPricing.__post_init__`` derives it as 2x input) and this adapter must never collapse
    1h onto the 5m rate.

    **As of genai-prices 0.1.4 there IS an upstream 1h field** -- ``cache_write_1h_mtok``,
    populated on 19 of 21 Anthropic models including all nine curated ones. Not reading it is a
    deliberate deferral to #662 (which owns the switch from derived to upstream-sourced, and its
    price-neutrality proof), **not** an absence. Earlier revisions of this docstring said the
    field did not exist; that was true at the 0.0.71 pin and is false now, which is exactly why
    the non-collapse invariant stopped being vacuous and got a real guard
    (``test_cache_write_binds_5m_field_not_1h``).
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


# The price keys this adapter REQUIRES upstream to supply -- a contract, not a convenience list.
# The resolver iterates it and the tests import it rather than restating the list (which is how a
# test set silently drifts out of sync with the code it covers).
#
# **Membership is not self-enforcing -- do not add a key here expecting it to become required.**
# The resolver reads every member through ``_required_rate`` (so *deregistering* any member
# raises), but the ``None`` check below it names four locals explicitly and is **not** derived
# from this tuple. Adding a fifth member therefore changes no pricing at all: the key is read and
# discarded. That inertness is the hazard -- the edit looks load-bearing, so the two
# representations drift and the key that was meant to become required silently isn't.
#
# Concretely, for #662: read ``cache_write_1h_mtok`` on a separate optional path via
# ``getattr(price, key, None)`` -- correct precisely because the key is allowed to be absent
# (upstream populates it on 19 of 21 Anthropic models). ``test_required_price_keys_is_pinned``
# fails on any edit to this tuple, so a change here has to be deliberate.
REQUIRED_PRICE_KEYS: tuple[str, ...] = (
    "input_mtok",
    "output_mtok",
    "cache_write_mtok",
    "cache_read_mtok",
)


def _required_rate(price: ModelPrice, key: str) -> float | None:
    """Read a **required** upstream rate key, failing loudly if it is deregistered.

    Distinguishes the two states the dynamic price-key registry exposes (see the module
    docstring's table): a *registered but unset* key resolves to ``None`` -- a legitimate
    per-model gap the caller handles by falling through to the local residual -- while a
    *deregistered* key raises ``AttributeError``, meaning the adapter's binding contract has
    broken for every model at once.

    The second is logged at WARNING and re-raised rather than flattened to ``None``: returning
    ``None`` would hand a broken binding to ``pricing.get_pricing``, which maps it to an empty
    residual and then to ``$0``. That path does warn, but it names the *model* -- so a binding
    broken for all of them would read as one model losing coverage. The exact pin in
    ``pyproject.toml`` is what keeps this unreachable in practice; the loudness is for the pin
    being loosened or the install being broken.
    """
    try:
        value = getattr(price, key)
    except AttributeError:
        logger.warning(
            "genai-prices deregistered the required price key '%s' -- the _genai_source "
            "binding is broken for every model, not just this one; refusing to price as $0",
            key,
        )
        raise
    # Deliberately OUTSIDE the try: only the attribute lookup can signal a deregistration.
    # An AttributeError raised inside ``_base_rate`` (an unexpected *value* shape) would
    # otherwise be reported as a registry break, pointing the reader at the wrong layer.
    return _base_rate(value)


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
    # Every required key is read (not short-circuited) so a deregistration anywhere in the set
    # still raises, even when an earlier key is merely unset.
    rates = {key: _required_rate(price, key) for key in REQUIRED_PRICE_KEYS}
    input_rate = rates["input_mtok"]
    output_rate = rates["output_mtok"]
    cache_write = rates["cache_write_mtok"]
    cache_read = rates["cache_read_mtok"]
    if input_rate is None or output_rate is None or cache_write is None or cache_read is None:
        # Partial upstream coverage. ``pricing.get_pricing`` surfaces this at WARNING rather
        # than DEBUG -- see the module docstring's "What the first row does NOT buy you".
        return None
    return UpstreamRates(
        input=input_rate,
        output=output_rate,
        cache_write_5m=cache_write,
        cache_read=cache_read,
    )
