"""Tests for model pricing lookup."""

import logging

import pytest

from agentfluent.analytics.pricing import (
    SYNTHETIC_MODELS,
    ModelPricing,
    compute_cost,
    get_known_models,
    get_pricing,
)


class TestGetPricing:
    def test_exact_model_name(self) -> None:
        pricing = get_pricing("claude-sonnet-4-20250514")
        assert pricing is not None
        assert pricing.input == 3.0
        assert pricing.output == 15.0

    def test_opus_4_6_model(self) -> None:
        pricing = get_pricing("claude-opus-4-6")
        assert pricing is not None
        assert pricing.input == 5.0
        assert pricing.output == 25.0
        assert pricing.cache_creation == 6.25
        assert pricing.cache_read == 0.50

    def test_opus_4_7_model(self) -> None:
        # Verifies issue #75: claude-opus-4-7 must return non-None pricing.
        pricing = get_pricing("claude-opus-4-7")
        assert pricing is not None
        assert pricing.input == 5.0
        assert pricing.output == 25.0
        assert pricing.cache_creation == 6.25
        assert pricing.cache_read == 0.50

    def test_haiku_model(self) -> None:
        pricing = get_pricing("claude-haiku-4-5-20251001")
        assert pricing is not None
        assert pricing.input == 1.0
        assert pricing.output == 5.0
        assert pricing.cache_creation == 1.25
        assert pricing.cache_read == 0.10

    def test_alias_short_name_opus(self) -> None:
        pricing = get_pricing("opus")
        assert pricing is not None
        # "opus" now resolves to opus-4-7 (current flagship).
        assert pricing.input == 5.0

    def test_alias_with_context_suffix_4_6(self) -> None:
        pricing = get_pricing("claude-opus-4-6[1m]")
        assert pricing is not None
        assert pricing.input == 5.0

    def test_alias_with_context_suffix_4_7(self) -> None:
        pricing = get_pricing("claude-opus-4-7[1m]")
        assert pricing is not None
        assert pricing.input == 5.0

    def test_sonnet_alias(self) -> None:
        pricing = get_pricing("sonnet")
        assert pricing is not None
        assert pricing.input == 3.0

    def test_unknown_model_returns_none(self) -> None:
        assert get_pricing("gpt-4o") is None

    def test_empty_string_returns_none(self) -> None:
        assert get_pricing("") is None

    def test_unknown_model_logs_at_debug_not_warning(
        self, caplog: pytest.LogCaptureFixture,
    ) -> None:
        # Issue #75: the "Unknown model" message should be DEBUG, not WARNING,
        # so it does not clutter stderr on normal CLI runs.
        with caplog.at_level(logging.DEBUG, logger="agentfluent.analytics.pricing"):
            assert get_pricing("some-future-model") is None
        debug_records = [r for r in caplog.records if r.levelno == logging.DEBUG]
        warning_records = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("some-future-model" in r.getMessage() for r in debug_records)
        assert warning_records == []


class TestSyntheticModels:
    def test_synthetic_sentinel_exported(self) -> None:
        # Issue #75: <synthetic> is filtered at the aggregation layer, but the
        # shared frozenset lives in pricing so other consumers can import it.
        assert "<synthetic>" in SYNTHETIC_MODELS


class TestComputeCost:
    def test_basic_cost(self) -> None:
        pricing = ModelPricing(input=3.0, output=15.0, cache_creation=3.75, cache_read=0.30)
        cost = compute_cost(pricing, input_tokens=1_000_000, output_tokens=100_000)
        # 1M * 3.0/1M + 100K * 15.0/1M = 3.0 + 1.5 = 4.5
        assert cost == 4.5

    def test_with_cache_tokens(self) -> None:
        pricing = ModelPricing(input=3.0, output=15.0, cache_creation=3.75, cache_read=0.30)
        cost = compute_cost(
            pricing,
            input_tokens=100_000,
            output_tokens=50_000,
            cache_creation_input_tokens=200_000,
            cache_read_input_tokens=500_000,
        )
        # 100K*3/1M + 50K*15/1M + 200K*3.75/1M + 500K*0.3/1M
        # = 0.3 + 0.75 + 0.75 + 0.15 = 1.95
        assert abs(cost - 1.95) < 0.001

    def test_zero_tokens(self) -> None:
        pricing = ModelPricing(input=3.0, output=15.0, cache_creation=3.75, cache_read=0.30)
        assert compute_cost(pricing, input_tokens=0, output_tokens=0) == 0.0

    def test_opus_pricing(self) -> None:
        pricing = get_pricing("claude-opus-4-6")
        assert pricing is not None
        cost = compute_cost(pricing, input_tokens=1_000_000, output_tokens=1_000_000)
        # 1M * 5/1M + 1M * 25/1M = 5 + 25 = 30
        assert cost == 30.0


class TestGetKnownModels:
    def test_returns_sorted_list(self) -> None:
        models = get_known_models()
        assert isinstance(models, list)
        assert models == sorted(models)
        assert len(models) >= 6

    def test_includes_opus_4_7(self) -> None:
        models = get_known_models()
        assert "claude-opus-4-7" in models

    def test_does_not_include_aliases(self) -> None:
        models = get_known_models()
        assert "opus" not in models
        assert "sonnet" not in models
