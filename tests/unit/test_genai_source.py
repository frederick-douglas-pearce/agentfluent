"""Tests for the isolated genai-prices adapter (analytics/_genai_source.py).

Covers the internal-record binding, base-tier extraction, the "5m-equivalent cache-write"
assumption the 1h derivation rests on, and the local-first (no-network) contract.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from agentfluent.analytics import pricing
from agentfluent.analytics._genai_source import (
    UpstreamRates,
    _base_rate,
    _resolve_rates,
)

# The curated ids AgentFluent knows (== _KNOWN_MODELS); all upstream-covered at 0.0.71.
_COVERED = [
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
