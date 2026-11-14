"""Tests for the isolated genai-prices adapter (analytics/_genai_source.py).

Covers the internal-record binding, base-tier extraction, the "5m-equivalent cache-write"
assumption the 1h derivation rests on, and the local-first (no-network) contract.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from agentfluent.analytics import pricing
from agentfluent.analytics._genai_source import (
    _ANTHROPIC,
    REQUIRED_PRICE_KEYS,
    ModelPrice,
    UpstreamRates,
    _base_rate,
    _required_rate,
    _resolve_rates,
)
from agentfluent.analytics.pricing import get_known_models

# The curated ids AgentFluent knows. **Imported, never restated** -- the convention this file
# already states for `REQUIRED_PRICE_KEYS` below, applied to the registry too (#674 review).
# These tests ask "does every curated id resolve upstream", so the registry IS the right set,
# and a hand-synced copy silently stops covering any id someone forgets to add to it. That is
# not hypothetical: the previous literal had to be edited by hand in two files at #661 and
# again at #674.
_COVERED = sorted(get_known_models())


class TestResolveRates:
    @pytest.mark.parametrize("model", _COVERED)
    def test_covered_model_resolves(self, model: str) -> None:
        rates = _resolve_rates(model)
        assert isinstance(rates, UpstreamRates)
        assert rates.input > 0
        assert rates.output > 0
        assert rates.cache_read > 0

    def test_unknown_model_returns_none(self) -> None:
        assert _resolve_rates("definitely-not-a-real-model-xyz") is None

    @pytest.mark.parametrize("model", _COVERED)
    def test_cache_write_is_5m_equivalent(self, model: str) -> None:
        # Validates the upstream assumption that the single genai-prices ``cache_write_mtok``
        # is the 5-minute rate (1.25x input) -- the basis the 1h derivation (2x input) trusts.
        # If upstream ever ships a different cache-write basis, this fails loudly.
        rates = _resolve_rates(model)
        assert rates is not None
        assert rates.cache_write_5m == pytest.approx(1.25 * rates.input)


class TestCacheWriteFieldBinding:
    """AC3 at the 0.1.x shape: 5m is bound to `cache_write_mtok`, never to `cache_write_1h_mtok`.

    Under genai-prices==0.0.71 this could not be written -- there was no upstream 1h field, so
    "never collapse 1h onto 5m" was vacuous. 0.1.4 populates `cache_write_1h_mtok` right beside
    `cache_write_mtok`, so the hazard is live and gets a real guard.

    Complements rather than duplicates `test_cache_write_is_5m_equivalent` above: that one pins
    the 5m rate to a RATIO (1.25x input); this one pins the FIELD the adapter reads. A collapse
    onto the 1h key would be caught by the ratio test only by coincidence of arithmetic, and
    would not say which field was wrong.

    Survives #662 unchanged: when 1h is sourced upstream it lands on `cache_creation_1h`, and
    `cache_write_5m` must still equal 6.25 rather than 10.0.
    """

    def _upstream_price(self, model: str) -> ModelPrice:
        # Reuses the adapter's own module-global provider rather than re-scanning `providers`,
        # so the guard is bound to the exact object `_resolve_rates` reads.
        assert _ANTHROPIC is not None
        model_info = _ANTHROPIC.find_model(model)
        assert model_info is not None
        return model_info.get_prices(datetime.now(UTC))

    @pytest.mark.parametrize("model", _COVERED)
    def test_cache_write_binds_5m_field_not_1h(self, model: str) -> None:
        upstream = self._upstream_price(model)
        rates = _resolve_rates(model)
        assert rates is not None

        five_m = _base_rate(upstream.cache_write_mtok)
        one_h = _base_rate(getattr(upstream, "cache_write_1h_mtok", None))

        if one_h is None:
            # Upstream carries no 1h rate for this model, so there is no adjacent field to
            # collapse onto and nothing for this case to discriminate. Skip rather than fail:
            # the adapter has no dependency on the 1h key, so reddening CI over its absence
            # would report an upstream data change as a defect in code that never reads it.
            # `test_guard_is_live_on_at_least_one_model` below is what stops every case
            # skipping silently.
            pytest.skip(f"{model}: upstream carries no cache_write_1h_mtok to discriminate")

        assert five_m != one_h, f"{model}: upstream 5m and 1h coincide; guard cannot discriminate"
        assert rates.cache_write_5m == five_m
        assert rates.cache_write_5m != one_h

    def test_guard_is_live_on_at_least_one_model(self) -> None:
        # Liveness backstop for the per-model skip above: if upstream ever stopped populating
        # `cache_write_1h_mtok` everywhere, every case would skip and the non-collapse guard
        # would pass by vacuity. This fails instead.
        discriminating = [
            m
            for m in _COVERED
            if _base_rate(getattr(self._upstream_price(m), "cache_write_1h_mtok", None))
            is not None
        ]
        assert discriminating, (
            "no curated model carries an upstream cache_write_1h_mtok -- the 5m/1h "
            "non-collapse guard is now vacuous; re-check the upstream shape before trusting it."
        )


class _StubPrice:
    """Stands in for genai-prices' `ModelPrice` at the 0.1.x dynamic-registry contract.

    A key passed to the constructor resolves to its value; a key named in `registered` but not
    supplied resolves to None (registered-but-unset); anything else raises AttributeError
    (deregistered). That three-way behavior is exactly what `_required_rate` discriminates on.
    """

    def __init__(self, registered: frozenset[str], **prices: object) -> None:
        self._registered = registered
        self._prices = prices

    def __getattr__(self, name: str) -> object:
        prices = self.__dict__["_prices"]
        if name in prices:
            return prices[name]
        if name in self.__dict__["_registered"]:
            return None
        raise AttributeError(name)


class TestRequiredRateFailsLoud:
    """A DEREGISTERED required key must be loud; a registered-but-unset one must not be.

    The distinction is the whole point (#661 architect review, Concern 1). Collapsing both to
    None -- which `getattr(price, key, None)` would do -- routes a broken binding into
    `get_pricing`'s empty-`_RESIDUAL` path and out as $0: structurally the same
    under-reporting defect #661 fixes.
    """

    # Imported, never restated: a local copy is exactly how a test set drifts out of sync
    # with the code it covers (#663 review).
    _REQUIRED = frozenset(REQUIRED_PRICE_KEYS)

    def test_present_key_resolves_to_its_value(self) -> None:
        # Exercises the stub's value branch, so all three states of the contract it models are
        # pinned -- not just the two failure ones.
        price = _StubPrice(self._REQUIRED, input_mtok=Decimal("5"))
        assert _required_rate(price, "input_mtok") == 5.0  # type: ignore[arg-type]

    def test_registered_but_unset_resolves_to_none_quietly(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        # A legitimate per-model gap: upstream knows the key, this model has no value for it.
        price = _StubPrice(self._REQUIRED)
        with caplog.at_level(logging.WARNING):
            assert _required_rate(price, "cache_read_mtok") is None  # type: ignore[arg-type]
        assert caplog.records == [], "a legitimate per-model gap must not warn"

    def test_deregistered_key_warns_and_raises(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        # The binding contract broke for EVERY model -- never silently $0.
        price = _StubPrice(frozenset())
        with caplog.at_level(logging.WARNING), pytest.raises(AttributeError):
            _required_rate(price, "input_mtok")  # type: ignore[arg-type]
        assert any(
            rec.levelno >= logging.WARNING and "input_mtok" in rec.getMessage()
            for rec in caplog.records
        ), "a deregistered required key must be surfaced at WARNING, not DEBUG"

    def test_deregistered_key_does_not_degrade_to_zero_cost(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The end-to-end shape of the failure this guards: `get_pricing` must NOT hand back a
        # priced-at-nothing result. It raises instead of returning None, because None here is
        # indistinguishable from "unknown model" and prices as $0.
        from agentfluent.analytics import _genai_source as source

        class _BrokenModel:
            def get_prices(self, _when: object) -> _StubPrice:
                return _StubPrice(frozenset())

        class _BrokenProvider:
            @staticmethod
            def find_model(_ref: str) -> _BrokenModel:
                return _BrokenModel()

        monkeypatch.setattr(source, "_ANTHROPIC", _BrokenProvider())
        with pytest.raises(AttributeError):
            pricing.get_pricing("claude-opus-4-8")


class TestRequiredPriceKeysMembership:
    """The required set is a contract, not a convenience list (#661 -> #662 tripwire)."""

    def test_required_price_keys_is_pinned(self) -> None:
        # This set fails asymmetrically. REMOVING a key is already loud: the resolver reads
        # named locals out of the dict it builds, so a removal raises KeyError. ADDING one is
        # the silent direction -- and it is the direction the next issue on this file walks.
        #
        # #662 retires the derived 1h cache-write onto upstream's ``cache_write_1h_mtok``, and
        # the obvious first move is to add that key here. That edit is INERT -- the resolver's
        # None check names four locals, not this tuple, so the fifth key is read and discarded
        # and every model prices exactly as before. Looking load-bearing while doing nothing is
        # why this pin exists: it forces the edit to be argued rather than assumed.
        assert REQUIRED_PRICE_KEYS == (
            "input_mtok",
            "output_mtok",
            "cache_write_mtok",
            "cache_read_mtok",
        ), (
            "REQUIRED_PRICE_KEYS changed. A key added here becomes mandatory for EVERY model; "
            "add one only if upstream populates it on every curated model. Optional keys "
            "(cache_write_1h_mtok, #662) go on a getattr(..., None) path -- see the constant's "
            "docstring in analytics/_genai_source.py."
        )

    def test_every_required_key_is_read_by_the_resolver(self) -> None:
        # Pins the *behaviour* the tuple promises, not just its contents: each member must be
        # read for a covered model, so the set cannot quietly become decorative.
        assert _ANTHROPIC is not None, "genai-prices has no anthropic provider record"
        model = _ANTHROPIC.find_model("claude-opus-5")
        assert model is not None, "claude-opus-5 is curated but absent upstream"
        price = model.get_prices(datetime.now(UTC))
        for key in REQUIRED_PRICE_KEYS:
            assert _required_rate(price, key) is not None, (
                f"{key} is declared required but resolves to None on a curated model"
            )


class TestBaseRate:
    def test_scalar_decimal_passthrough(self) -> None:
        assert _base_rate(Decimal("3.5")) == 3.5

    def test_none_stays_none(self) -> None:
        assert _base_rate(None) is None

    def test_tiered_takes_base_not_surcharge(self) -> None:
        # A context-tiered field (e.g. Sonnet's >200K surcharge) must resolve to the base
        # (standard-context) rate, which is what _PRICING historically encoded.
        from genai_prices.data import Tier, TieredPrices

        tiered = TieredPrices(
            base=Decimal("3"), tiers=[Tier(start=200000, price=Decimal("6"))]
        )
        assert _base_rate(tiered) == 3.0


class TestLocalFirstContract:
    def test_no_network_during_resolution(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import socket

        def _boom(*_args: object, **_kwargs: object) -> None:
            raise AssertionError("network I/O attempted during pricing resolution")

        monkeypatch.setattr(socket, "socket", _boom)
        monkeypatch.setattr(socket, "create_connection", _boom)

        assert _resolve_rates("claude-opus-4-8") is not None
        assert pricing.get_pricing("claude-opus-4-8") is not None

    def test_update_prices_never_constructed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import genai_prices

        class _Boom:
            def __init__(self, *_args: object, **_kwargs: object) -> None:
                raise AssertionError("UpdatePrices was constructed (posture violation)")

        monkeypatch.setattr(genai_prices, "UpdatePrices", _Boom)
        assert pricing.get_pricing("claude-sonnet-4-6") is not None

    def test_source_never_calls_update_prices(self) -> None:
        import inspect

        from agentfluent.analytics import _genai_source

        src = inspect.getsource(_genai_source)
        # "UpdatePrices" may appear in the docstring contract, but never as a call.
        assert "UpdatePrices(" not in src


class TestUpstreamMatchModes:
    """Which upstream records accept a NON-identical id, DERIVED from the catalog (#664).

    This class exists because the same mistake was made twice in #664, in opposite directions:
    first "upstream strips date suffixes only" (generalized from six 5.x canonical-dashed ids),
    then "family prefix is 3.x/4.x, dated regex is 5.x alone" (generalized from canonical-dashed
    spellings). Both were false, and a third prose attempt would most likely fail the same way.

    So the fact is no longer *described* anywhere -- it is **derived from the pinned catalog and
    asserted here**, and the decision log points at this class instead of restating it. If a pin
    bump changes which records accept a prefix, this fails and names them, rather than leaving a
    sentence somewhere to quietly go stale.
    """

    @staticmethod
    def _clause_kinds(clause: object) -> set[str]:
        """Flatten a match-clause tree to the set of leaf clause type names."""
        name = type(clause).__name__
        if name == "ClauseOr":
            kinds: set[str] = set()
            for sub in clause.or_:  # type: ignore[attr-defined]
                kinds |= TestUpstreamMatchModes._clause_kinds(sub)
            return kinds
        if name == "ClauseAnd":
            kinds = set()
            for sub in clause.and_:  # type: ignore[attr-defined]
                kinds |= TestUpstreamMatchModes._clause_kinds(sub)
            return kinds
        return {name}

    #: The ONLY Anthropic records that accept no prefix spelling at the pinned version. Everything
    #: else accepts at least one, so **no generation is prefix-free** -- which is the fact both
    #: earlier prose characterizations got wrong.
    PREFIX_FREE = frozenset(
        {
            "claude-fable-5",
            "claude-fable-5-1",
            "claude-fable-5-2",
            "claude-mythos-5-2",
            "claude-v1",
        }
    )

    def test_prefix_free_records_are_exactly_the_documented_set(self) -> None:
        assert _ANTHROPIC is not None
        prefix_free = {
            m.id
            for m in _ANTHROPIC.models
            if "ClauseStartsWith" not in self._clause_kinds(m.match)
        }
        assert prefix_free == set(self.PREFIX_FREE), (
            "the set of Anthropic records accepting no prefix spelling has changed upstream.\n"
            f"  now prefix-free: {sorted(prefix_free)}\n"
            f"  expected:       {sorted(self.PREFIX_FREE)}\n"
            "Update PREFIX_FREE *and* re-read D063(2): the pricing-gate decision (#664) turns on "
            "whether a fabricated id can borrow a sibling's rate."
        )

    def test_no_generation_is_prefix_free(self) -> None:
        # The specific shape both wrong characterizations asserted: that some whole generation
        # matches only by equality/dated-regex and is therefore safe from fabricated bumps.
        assert _ANTHROPIC is not None
        for family in ("claude-3", "claude-opus-4", "claude-sonnet-4", "claude-opus-5",
                       "claude-sonnet-5", "claude-haiku-5"):
            records = [m.id for m in _ANTHROPIC.models if m.id.startswith(family)]
            assert records, f"no records for {family} -- the catalog shape changed"
            accepting = [r for r in records if r not in self.PREFIX_FREE]
            assert accepting, (
                f"{family} is now entirely prefix-free. Both of #664's wrong prose "
                "characterizations claimed exactly this about some generation; if it has become "
                "true, D063(2) needs re-deriving rather than editing."
            )

    @pytest.mark.parametrize(
        ("fabricated", "borrows_from"),
        [
            # Canonical dashed form -- the 4.x shape that refuted attempt 1.
            ("claude-opus-4-19", "claude-opus-4-1"),
            ("claude-sonnet-4-69", "claude-sonnet-4-6"),
            # 5.x in NON-canonical spellings -- the shape that refuted attempt 2. Attempt 2 said
            # 5.x was dated-regex-only and so safe; these price.
            ("claude-5-sonnet-9", "claude-sonnet-5"),
            ("claude-haiku-5.5-9", "claude-haiku-5-5"),
            ("claude-opus-5.0-77", "claude-opus-5"),
        ],
    )
    def test_fabricated_ids_borrow_a_sibling_rate(
        self, fabricated: str, borrows_from: str
    ) -> None:
        # The consequence that matters, asserted rather than described: upstream hands back a full,
        # plausible rate set for an id that does not exist. This is why naive option (ii) --
        # pricing anything upstream covers -- was rejected (D063(2)).
        assert _ANTHROPIC is not None
        matched = _ANTHROPIC.find_model(fabricated)
        assert matched is not None and matched.id == borrows_from
        rates = _resolve_rates(fabricated)
        assert rates is not None
        assert rates.input > 0 and rates.output > 0
        # ...and the curation gate still refuses to price it. That is option (i) holding, and it
        # is the reason none of this was ever a live mispricing.
        assert pricing.get_pricing(fabricated) is None

    def test_prefix_free_records_reject_a_fabricated_bump(self) -> None:
        # The other half: the five prefix-free records really are safe, which is what makes the
        # distinction worth drawing at all.
        for fabricated in ("claude-fable-5-9", "claude-mythos-5-2-9"):
            assert _ANTHROPIC is not None
            assert _ANTHROPIC.find_model(fabricated) is None
            assert _resolve_rates(fabricated) is None
