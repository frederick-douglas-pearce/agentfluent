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

# The curated ids AgentFluent knows (== _KNOWN_MODELS); all upstream-covered at 0.1.4 (#661).
_COVERED = [
    "claude-opus-5",
    "claude-opus-4-8",
    "claude-opus-4-7",
    "claude-opus-4-6",
    "claude-opus-4-5-20251101",
    "claude-sonnet-4-6",
    "claude-sonnet-4-5-20250929",
    "claude-sonnet-4-20250514",
    "claude-haiku-4-5-20251001",
]


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
    `get_pricing`'s empty-`_RESIDUAL` path and out as $0 logged at DEBUG: silent catastrophic
    under-reporting, structurally the same defect #661 fixes.
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
        # the obvious first move is to add that key here. It must not be: the key is populated
        # on 19 of 21 Anthropic models, so promoting it to *required* turns an optional field
        # into a global kill-switch -- the two models without it would resolve to None for all
        # four rates and price at $0. That is #661's own defect, reintroduced by #661's fix.
        # Optional keys belong on a ``getattr(price, key, None)`` path, not in this tuple.
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
