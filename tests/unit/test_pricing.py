"""Tests for model pricing lookup."""

from agentfluent.analytics.pricing import (
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

    def test_opus_model(self) -> None:
        pricing = get_pricing("claude-opus-4-6")
        assert pricing is not None
        assert pricing.input == 15.0
        assert pricing.output == 75.0
        assert pricing.cache_creation == 18.75
        assert pricing.cache_read == 1.875

    def test_haiku_model(self) -> None:
        pricing = get_pricing("claude-haiku-4-5-20251001")
        assert pricing is not None
        assert pricing.input == 0.80
        assert pricing.output == 4.0

    def test_alias_short_name(self) -> None:
        pricing = get_pricing("opus")
        assert pricing is not None
        assert pricing.input == 15.0

    def test_alias_with_context_suffix(self) -> None:
        pricing = get_pricing("claude-opus-4-6[1m]")
        assert pricing is not None
        assert pricing.input == 15.0

    def test_sonnet_alias(self) -> None:
        pricing = get_pricing("sonnet")
        assert pricing is not None
        assert pricing.input == 3.0

    def test_unknown_model_returns_none(self) -> None:
        assert get_pricing("gpt-4o") is None

    def test_empty_string_returns_none(self) -> None:
        assert get_pricing("") is None


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
        # 1M * 15/1M + 1M * 75/1M = 15 + 75 = 90
        assert cost == 90.0


class TestGetKnownModels:
    def test_returns_sorted_list(self) -> None:
        models = get_known_models()
        assert isinstance(models, list)
        assert models == sorted(models)
        assert len(models) >= 6

    def test_does_not_include_aliases(self) -> None:
        models = get_known_models()
        assert "opus" not in models
        assert "sonnet" not in models
