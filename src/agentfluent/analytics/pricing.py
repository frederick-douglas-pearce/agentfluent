"""Model pricing lookup for token cost estimation.

Maps Anthropic model names to per-token costs (USD per 1M tokens) for
input, output, cache_creation, and cache_read token categories.

Base rates are sourced from genai-prices (upstream, D045) via the isolated
``_genai_source`` adapter; a small documented local residual (``_RESIDUAL``) supplies any
model genai-prices does not cover. This module is the public pricing surface -- nothing
outside ``_genai_source`` imports ``genai_prices`` directly.

The base ⊕ overlay merge seam (#547)
------------------------------------
Final per-request cost is ``base (upstream) ⊕ overlay (local)``. genai-prices models only
the *base* rate table; AgentFluent adds the levers it does not model (see
``docs/COST_MODEL.md``) through this seam. The overlay levers fall into **three composition
classes** that compose in one fixed, documented order::

    final = ( (base ⊕ rate_overlays[A]) · tokens ) · Π(multipliers[B]) + Σ(surcharges[C])

- **Class A -- rate-table transforms** (per model+date): modify the per-token rate table
  *before* token×rate. Plug point: ``ModelPricing`` construction in ``get_pricing``. Members:
  1-hour cache write (#534, landed -- ``ModelPricing.__post_init__`` derives it as 2× input);
  fast-mode premium rates (#536, future -- also class A, replaces the rate table).
- **Class B -- per-request multipliers** (per invocation): scale the dollar subtotal. Plug
  point: ``compute_cost(..., request_multipliers=...)``. Members: batch 0.5× / priority (#537),
  data residency US 1.1× (#538). They commute (multiplication).
- **Class C -- additive surcharges** (per invocation): add fixed dollars *after* the
  multipliers (a flat server-tool fee must not be discounted by a 0.5× batch multiplier). Plug
  point: ``compute_cost(..., surcharge_usd=...)``. Members: server-tool / web-search $10/1k
  (#539).

"Is this lever upstream or overlay?" is therefore a one-line answer per lever, and retiring an
overlay when genai-prices adds it upstream is a localized change (drop the class-A transform or
the class-B/C argument at its single plug point). ``compute_cost``'s class-B/C parameters default
to no-op (empty multiplier product = 1.0, zero surcharge), so the base-only path is bit-identical.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from agentfluent.analytics._genai_source import _resolve_rates, classify_upstream_match

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
    correctly. The adapter binds only the upstream 5m-equivalent
    ``cache_write_mtok``, and must never collapse the 1h dimension back
    onto that rate.

    genai-prices *does* carry an upstream 1h field
    (``cache_write_1h_mtok``) at the pinned version. Continuing
    to derive 1h locally is a deliberate deferral to #662 — which owns
    the switch and its price-neutrality proof — not evidence the field
    is unavailable. Earlier text here said it had "no upstream field";
    that was true at the 0.0.71 pin and is false now. **The per-model
    count is deliberately not restated here** — ``_genai_source`` owns
    that number, and keeping a second copy is how this very sentence
    drifted out of sync with its twin (#674).

    This rate table is the **class-A plug point** of the base ⊕ overlay
    seam (see the module docstring): rate-table overlays (the 1h-cache
    derivation below; fast-mode premium rates, #536) apply here, *before*
    token×rate. Keep the 1h derivation in ``__post_init__`` — it is the
    single-source-of-truth invariant that makes every ``ModelPricing``
    valid regardless of construction path; do not relocate it to the
    per-request stages (``compute_cost``), which are classes B and C.
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
# The #674 coverage probe found genai-prices==0.2.3 covers ALL of these ids. The nine curated
# under 0.1.4 (#661) keep byte-identical base-tier rates across the 0.1.4 -> 0.2.3 bump -- that
# bump moved no rate -- and the current (5.x) family joined at it:
#   Opus 4.5/4.6/4.7/4.8  -> $5 / $25 / $6.25 / $0.50
#   Opus 5                -> $5 / $25 / $6.25 / $0.50   (NEW at 0.1.4 -- absent from 0.0.71,
#                                                        which is why it priced at $0; #661)
#   Opus 5.5              -> $4 / $20 / $5.00 / $0.20   (NEW at 0.2.3 -- #674)
#   Sonnet 4.0/4.5/4.6    -> $3 / $15 / $3.75 / $0.30
#   Sonnet 5              -> $2 / $10 / $2.50 / $0.20   (NEW at 0.2.3 -- #674)
#   Sonnet 5.5            -> $2 / $10 / $2.50 / $0.10   (NEW at 0.2.3 -- #674)
#   Haiku 4.5             -> $1 / $5  / $1.25 / $0.10
#   Haiku 5.5             -> $0.10 / $0.50 / $0.125 / $0.01            (NEW at 0.2.3 -- #674)
#   Fable 5.1             -> $10 / $50 / $12.50 / $0.25                (NEW at 0.2.3 -- #674)
#   Fable 5.2             -> $8 / $40 / $10.00 / $0.20                 (NEW at 0.2.3 -- #674)
# so ``_RESIDUAL`` is still empty. The golden-rate regression test locks these values.
#
# CONTEXT TIERS: the rates above are the **standard (base) tier**. Where upstream prices a
# model by prompt length, ``_genai_source._base_rate`` takes ``TieredPrices.base`` and
# DISCARDS the tiers -- a documented contract of that adapter, not an oversight here. The
# contract is stated rather than a list of which ids are tiered, because such a list is what
# went stale: it named the one tiered id it knew and missed a live one.
#   Known reachable case: ``claude-haiku-5-5`` is tiered at **5x above 100K prompt tokens**
#   on all four rate keys, so long-prompt requests to it under-report. Tracked in **#682**;
#   fixing it needs the per-request prompt size at the pricing call site, which is a behavior
#   change with its own golden tests.
#   Not reachable today: sonnet-4-5's >200K tier is historical (upstream retired the 1M beta
#   on 2026-04-30 and >200k now errors), and opus-4-6 / sonnet-4-6 carry theirs only in a
#   pre-2026-03-13 conditional price whose currently-active successor is flat.
#
# Why the 5.x family read $0 before #674, and why ONE fix was not enough: the pin sat at 0.1.4,
# which carries none of those ids, AND this frozenset gated them out before upstream was
# consulted. Both had to change. ``claude-opus-5-5`` was the single most common model id in a
# real corpus at the 2026-11-13 enumeration (165,219 rows), so the defect zeroed the largest
# cost component present. Third recurrence of the shape (``claude-opus-5`` -> #661 was the
# second).
#
# EXACT records only (#674 AC3): an id is curated here only when upstream holds a record whose
# own id equals it (``find_model(x).id == x``). Four ids curated earlier resolve by FAMILY
# PREFIX instead -- ``claude-opus-4-5-20251101`` -> ``claude-opus-4-5``,
# ``claude-sonnet-4-5-20250929`` -> ``claude-sonnet-4-5``, ``claude-sonnet-4-20250514`` ->
# ``claude-sonnet-4-0``, ``claude-haiku-4-5-20251001`` -> ``claude-haiku-4-5``. They are
# REPORTED here, deliberately NOT decurated: they price correctly today and dropping them would
# regress coverage. Prefix matching is #664's structural business, not this list's.
#
# NOTE (#661 architect review, Concern 2): this frozenset is consulted BEFORE upstream, so an id
# missing here still fails closed to None -> $0 even when genai-prices can price it. #664 made
# that miss LOUD rather than silent -- see ``_warn_if_priceable_but_uncurated``, which logs it at
# WARNING (DEBUG only for a legacy id or one upstream cannot price). The $0 itself is unchanged,
# deliberately (option (i), D063(1)). #674 cleared the then-live instances by curation -- do not
# read the curation here as evidence that an absent id is unpriceable.
_KNOWN_MODELS: frozenset[str] = frozenset(
    {
        "claude-opus-5-5",
        "claude-opus-5",
        "claude-opus-4-8",
        "claude-opus-4-7",
        "claude-opus-4-6",
        "claude-opus-4-5-20251101",
        "claude-sonnet-5-5",
        "claude-sonnet-5",
        "claude-sonnet-4-6",
        "claude-sonnet-4-5-20250929",
        "claude-sonnet-4-20250514",
        "claude-haiku-5-5",
        "claude-haiku-4-5-20251001",
        "claude-fable-5-2",
        "claude-fable-5-1",
        # Curated by #664 under the EXACT-RECORD-ONLY criterion (D063(10), maintainer ruling
        # 2026-11-13). Both hold exact upstream records and have zero corpus rows. The earlier
        # criterion also required "a reason to expect it in real data" and named these two as
        # deliberately uncurated; that second conjunct was dropped because it applied D061(3)
        # unevenly -- `claude-fable-5-2` has zero rows and was curated, so the gap read as a rule.
        "claude-fable-5",
        "claude-mythos-5-2",
    }
)

#: Legacy generations held SILENT on the uncurated-but-priceable path (#664 AC2'(c), ratified
#: 2026-11-13). Upstream can still price several of these, so without the carve-out they would
#: each emit a WARNING -- noise on archived sessions rather than a signal about today's lineup.
#:
#: ``test_no_curated_id_is_a_legacy_generation`` (tests/unit/test_pricing.py) imports this
#: constant, so the registry prohibition and this carve-out cannot drift apart. Note that is a
#: DIFFERENT test from the deny-canary ``test_upstream_catalog_not_leaked``, which keeps its own
#: hard-coded id list on purpose and does not read this constant (#664 review r1 corrected an
#: earlier version of this comment, which named the wrong test).
#: It carries ``claude-instant`` as well, which the ratified AC2'(c) wording does not name: that
#: prefix is inert at the current pin (``claude-instant-1`` resolves to no rates, so it takes the
#: absent/DEBUG path regardless) and is listed to keep one definition rather than two. If a future
#: pin starts pricing it, this constant -- not a second list -- is where that is decided.
LEGACY_MODEL_PREFIXES: tuple[str, ...] = ("claude-v1", "claude-2", "claude-instant", "claude-3-")

#: Canonical ids already warned about on the uncurated-but-priceable path, so the WARNING fires
#: once per distinct id per process (#664 AC2'). Deliberately a plain set with no lock and no
#: production reset API: nothing in ``src/`` uses threads, and a check-then-add race would only
#: duplicate a log line. Tests isolate it by monkeypatching this binding -- see the autouse
#: fixture in tests/unit/test_pricing.py -- which keeps them order-independent without adding a
#: test-only surface to the package.
_WARNED_UNCURATED: set[str] = set()

# Documented local residual: model id -> ModelPricing for any model genai-prices lacks.
# Empty as of genai-prices==0.2.3 (see _KNOWN_MODELS above): upstream holds an exact record for
# every curated id, so #674 added none. Add an entry ONLY for a model
# the coverage probe shows upstream does not price -- this is the local-overlay escape
# hatch, not a re-introduction of the hand-maintained rate table.
_RESIDUAL: dict[str, ModelPricing] = {}

# Aliases map short names and variant identifiers to canonical model names.
# Aliases mirror the ID forms used elsewhere in the codebase (e.g.
# diagnostics/delegation.py recommends `claude-haiku-4-5`, the
# undated alias — pricing must resolve the same string).
#
# The bare short names are PINNED to one generation each, not to "the newest". ``fable`` was
# added by #674 because 7 corpus rows carry it and it priced at $0; it points at Fable 5.1, the
# generation actually present (4,468 rows, vs 0 for 5.2). Note the known limit of this scheme,
# which #674 did NOT change: every bare name resolves to an older generation than the one now
# in use, so those rows price at the wrong generation's rate. ``haiku`` is the widest gap --
# it resolves to 4.5 at $1/$5 while ``claude-haiku-5-5`` is $0.10/$0.50, a **10x** over-price
# (11 corpus rows); ``sonnet`` -> 4.6 is 1.5x over (35 rows) and ``opus`` -> 4.8 is 1.25x over
# (163 rows). Dollar impact is small at these volumes, but note the ranking is by RATIO and
# the row counts run the other way. That staleness is its own concern, not this issue's.
#
# ``[1m]`` entries resolve the 1M-context variant string to its base id, which prices it at the
# base tier (``_genai_source._base_rate`` discards context tiers for every model). The entries
# for the ids #674 added are DEFENSIVE: no ``[1m]`` form of any model appears in the corpus
# today. They are inert if never emitted, and the alternative -- omitting them -- is the exact
# fail-closed-to-$0 shape this issue exists to remove.
_ALIASES: dict[str, str] = {
    "opus": "claude-opus-4-8",
    "sonnet": "claude-sonnet-4-6",
    "haiku": "claude-haiku-4-5-20251001",
    "fable": "claude-fable-5-1",
    "claude-haiku-4-5": "claude-haiku-4-5-20251001",
    "claude-opus-5-5[1m]": "claude-opus-5-5",
    "claude-opus-5[1m]": "claude-opus-5",
    "claude-opus-4-8[1m]": "claude-opus-4-8",
    "claude-opus-4-7[1m]": "claude-opus-4-7",
    "claude-opus-4-6[1m]": "claude-opus-4-6",
    "claude-sonnet-5-5[1m]": "claude-sonnet-5-5",
    "claude-sonnet-5[1m]": "claude-sonnet-5",
    "claude-sonnet-4-6[1m]": "claude-sonnet-4-6",
    "claude-haiku-5-5[1m]": "claude-haiku-5-5",
    "claude-fable-5-2[1m]": "claude-fable-5-2",
    "claude-fable-5-1[1m]": "claude-fable-5-1",
    # #664: the two ids curated there get their `[1m]` forms too. Omitting them is the exact
    # fail-closed-to-$0 shape this issue exists to remove. Like most `[1m]` keys here, these two
    # are ABSENT upstream in `[1m]` form, so they take the DEBUG path where the uncurated-model
    # WARNING cannot see them -- the alias is the only thing that makes them price.
    "claude-fable-5[1m]": "claude-fable-5",
    "claude-mythos-5-2[1m]": "claude-mythos-5-2",
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


def _warn_if_priceable_but_uncurated(canonical: str, timestamp: datetime | None) -> None:
    """Log the uncurated-model miss at the right level (#664).

    ``_KNOWN_MODELS`` is consulted BEFORE upstream, so an id upstream *can* price still returns
    ``None`` -> ``$0``. That is left unchanged deliberately -- option (i) in #664, D063(1): this
    function changes only the **signal**, never the number, which is what keeps the fix
    cost-neutral and out of #646/#674's cost-attribution window (D062(8), D057(10)).

    Three outcomes, by ascending severity:

    * **legacy id** -> DEBUG. Ratified carve-out (AC2'(c)); see ``LEGACY_MODEL_PREFIXES``.
    * **upstream cannot price it** -> DEBUG. An id we never claimed to support and nobody can
      price is not a defect in this registry. Disclosing the run-level consequence is #688's job.
    * **upstream CAN price it** -> WARNING, once per id. This is the #664 defect: the allow-list
      is the only thing zeroing a model whose rate was available all along.

    Checked before the upstream call so a legacy id costs no lookup, and so the carve-out cannot
    be defeated by a future pin that starts pricing one.
    """
    if canonical.startswith(LEGACY_MODEL_PREFIXES):
        logger.debug("Legacy model '%s' -- deliberately not curated, no pricing", canonical)
        return

    kind, matched = classify_upstream_match(canonical, timestamp)
    if kind == "absent":
        logger.debug("Unknown model '%s' -- no pricing available", canonical)
        return

    if canonical in _WARNED_UNCURATED:
        return
    _WARNED_UNCURATED.add(canonical)

    if kind == "exact":
        logger.warning(
            "Model '%s' is priceable upstream but is not curated -- it will contribute $0 to "
            "every cost figure. Add it to _KNOWN_MODELS, or alias it onto a curated id.",
            canonical,
        )
    else:
        logger.warning(
            "Model '%s' is not curated and is NOT priced: upstream matched a different record "
            "('%s'), so the match is inexact and its rates are not assumed to apply. Curate "
            "'%s' explicitly if that rate is correct for it.",
            canonical,
            matched,
            canonical,
        )


def get_pricing(model: str, timestamp: datetime | None = None) -> ModelPricing | None:
    """Look up pricing for a model name.

    Resolves aliases first (short names, ``[1m]`` suffixes), then sources the rate
    upstream-first from genai-prices, falling back to the documented local residual.
    Returns None for an unknown model. **The log level depends on why it is unknown**
    (#664, see ``_warn_if_priceable_but_uncurated``): WARNING when genai-prices *could* have
    priced it and only our curation is missing, DEBUG for a legacy id or one upstream cannot
    price either. The caller is expected to skip synthetic sentinel values (see
    ``SYNTHETIC_MODELS``) before invoking this.

    **Raises** ``AttributeError`` if genai-prices has *deregistered* one of the required price
    keys, which breaks the rate binding for every model at once (see
    ``_genai_source._required_rate``). This is deliberate and is NOT the unknown-model path:
    returning ``None`` there would price a broken binding at ``$0``, the failure this module's
    #661 fix exists to remove. The exact pin in ``pyproject.toml`` keeps it unreachable short of
    a loosened pin or a broken install, and it is unrecoverable when it does happen — pricing
    cannot be done at all — so it surfaces rather than degrading.

    ``timestamp`` selects the base rate in effect on that date via genai-prices' dated
    constraints (#546); ``None``/omitted → the latest rate, preserving every existing
    caller. Note (#546 falsifier finding, re-confirmed at genai-prices==0.1.4 in #661 and again
    at 0.2.3 in #674, and again in #664 for the two ids curated there -- across all seventeen
    curated ids): no model in
    ``_KNOWN_MODELS`` currently carries a dated *base-rate* change -- the only dated
    Anthropic constraints move the >200K context tier, which the adapter discards (see
    ``_genai_source._base_rate``) -- so a timestamp is presently date-invariant at base-rate
    granularity. The parameter is wired so a future upstream dated base-rate change applies
    automatically; the forward-compat guard test in ``tests`` is the tripwire.
    """
    canonical = _ALIASES.get(model, model)
    if canonical not in _KNOWN_MODELS:
        _warn_if_priceable_but_uncurated(canonical, timestamp)
        return None

    rates = _resolve_rates(canonical, timestamp)
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

    # WARNING, not DEBUG: this is a *curated* id -- we asserted we support it -- that still
    # cannot be priced, so it contributes $0 to every cost figure. Partial upstream coverage
    # (a model missing one required key) lands here too, and ``_RESIDUAL`` is empty by design,
    # so nothing downstream would otherwise surface it. The truly-unknown-model path above
    # stays at DEBUG -- an id we never claimed to support is not a defect.
    #
    # Note the limit of this signal: it would NOT have caught #661 itself. `claude-opus-5` was
    # missing from `_KNOWN_MODELS`, so it exited at the uncurated branch and never reached here.
    # Curated-but-unpriceable is the case this covers; uncurated-but-priceable is handled by
    # `_warn_if_priceable_but_uncurated` (#664), which now warns rather than staying silent.
    logger.warning(
        "Known model '%s' has neither upstream nor residual pricing -- it will contribute $0 "
        "to every cost figure. Upstream coverage may have changed; check _KNOWN_MODELS and "
        "_RESIDUAL against the pinned genai-prices version.",
        canonical,
    )
    return None


def compute_cost(
    pricing: ModelPricing,
    input_tokens: int,
    output_tokens: int,
    cache_creation_5m_tokens: int = 0,
    cache_read_input_tokens: int = 0,
    cache_creation_1h_tokens: int = 0,
    request_multipliers: Sequence[float] = (),
    surcharge_usd: float = 0.0,
) -> float:
    """Compute final per-request dollar cost in USD (the base ⊕ overlay merge point, #547).

    This is the single merge point where the upstream **base** rate table (``pricing``,
    already carrying any class-A rate-table overlay assembled in ``get_pricing``) combines
    with the local **per-request overlay** levers to produce the final cost, in the fixed
    documented order (see the module docstring)::

        final = (Σ rate·token / 1e6) · Π(request_multipliers) + surcharge_usd
                └─── base ⊕ class-A ───┘   └── class B ──┘       └─ class C ─┘

    - ``request_multipliers`` (**class B**, per-request multipliers): scale the token-derived
      dollar subtotal. Empty (the default) → an empty product of ``1.0`` → no scaling. Plug
      point for batch 0.5× / priority (#537) and data residency US 1.1× (#538); they commute.
    - ``surcharge_usd`` (**class C**, additive surcharge): fixed dollars added *after* the
      multipliers, so a flat fee is never discounted by a batch multiplier. Default ``0.0`` →
      no surcharge. Plug point for server-tool / web-search $10/1k (#539).

    With both at their no-op defaults the overlay stages are exactly identity
    (``subtotal · 1.0 + 0.0 == subtotal`` in IEEE-754), so every existing caller prices
    bit-identically — this is a pure refactor of the prior flat rate×token sum.

    The cache-write total is split by TTL: ``cache_creation_5m_tokens`` is billed at the
    5-minute write rate, ``cache_creation_1h_tokens`` at the 1-hour rate (2x base, class A).
    The parameter is named for the 5m bucket (not the undifferentiated total) on purpose —
    passing ``Usage``'s authoritative ``cache_creation_input_tokens`` here alongside a
    separate 1h count would double-bill the 1h portion. Callers that only know the
    undifferentiated total pass it here, which prices the whole sum at the 5m rate — the
    documented fallback for sessions whose JSONL lacks the ``usage.cache_creation`` TTL split
    (see #534).
    """
    subtotal_usd = (
        input_tokens * pricing.input
        + output_tokens * pricing.output
        + cache_creation_5m_tokens * pricing.cache_creation_5m
        + cache_creation_1h_tokens * pricing.cache_creation_1h
        + cache_read_input_tokens * pricing.cache_read
    ) / 1_000_000
    # class B (multipliers) then class C (surcharge), in the documented stacking order.
    return subtotal_usd * math.prod(request_multipliers) + surcharge_usd


def get_known_models() -> list[str]:
    """Return the sorted curated model set (excluding aliases).

    This is the curated registry AgentFluent supports (``_KNOWN_MODELS``), NOT the full
    genai-prices catalog (which carries many older Anthropic ids).
    """
    return sorted(_KNOWN_MODELS)
