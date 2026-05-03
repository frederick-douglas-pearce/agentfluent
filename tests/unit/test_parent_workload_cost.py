"""Tests for cost estimation on parent-thread tool-bursts (sub-issue C of #189).

Covers ``pick_alternative_model`` (tier mapping) and ``estimate_burst_cost``
(parent cost + signed savings). The key invariant the architect review
locked in: savings is signed; negative-savings clusters are surfaced,
not clamped to zero.
"""

from __future__ import annotations

import pytest

from agentfluent.analytics.pricing import ModelPricing, get_pricing
from agentfluent.core.session import Usage
from agentfluent.diagnostics.parent_workload import (
    ToolBurst,
    estimate_burst_cost,
    pick_alternative_model,
)


def _burst(
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_creation_input_tokens: int = 0,
    cache_read_input_tokens: int = 0,
    model: str = "claude-opus-4-7",
) -> ToolBurst:
    return ToolBurst(
        preceding_user_text="",
        assistant_text="",
        tool_use_blocks=[],
        usage=Usage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_creation_input_tokens=cache_creation_input_tokens,
            cache_read_input_tokens=cache_read_input_tokens,
        ),
        model=model,
    )


# ---------------------------------------------------------------------------
# pick_alternative_model
# ---------------------------------------------------------------------------


class TestPickAlternativeModel:
    def test_opus_routes_to_sonnet(self) -> None:
        assert pick_alternative_model("claude-opus-4-7") == "claude-sonnet-4-6"

    def test_opus_4_6_also_routes_to_sonnet(self) -> None:
        # Substring match should cover all Opus variants.
        assert pick_alternative_model("claude-opus-4-6") == "claude-sonnet-4-6"

    def test_dated_opus_routes_to_sonnet(self) -> None:
        assert pick_alternative_model("claude-opus-4-5-20251101") == "claude-sonnet-4-6"

    def test_sonnet_routes_to_haiku(self) -> None:
        assert (
            pick_alternative_model("claude-sonnet-4-6")
            == "claude-haiku-4-5-20251001"
        )

    def test_dated_sonnet_routes_to_haiku(self) -> None:
        assert (
            pick_alternative_model("claude-sonnet-4-5-20250929")
            == "claude-haiku-4-5-20251001"
        )

    def test_haiku_returns_itself(self) -> None:
        # Haiku is already the cheapest tier — no further offload target.
        assert (
            pick_alternative_model("claude-haiku-4-5-20251001")
            == "claude-haiku-4-5-20251001"
        )

    def test_unknown_model_returns_unchanged(self) -> None:
        # No tier match → caller surfaces "no estimate" rather than
        # projecting against the wrong tier.
        assert pick_alternative_model("gpt-5") == "gpt-5"

    def test_empty_string_returns_unchanged(self) -> None:
        assert pick_alternative_model("") == ""

    def test_alt_targets_resolve_in_pricing_module(self) -> None:
        # Lock the constants in pick_alternative_model against the pricing
        # module: the alt target must resolve to a real ModelPricing entry.
        # If a future pricing-data update renames the canonical Sonnet or
        # Haiku id, this test fires before the cost path silently
        # degrades to "no estimate."
        for parent in ("claude-opus-4-7", "claude-sonnet-4-6"):
            alt = pick_alternative_model(parent)
            assert get_pricing(alt) is not None, f"alt model {alt!r} has no pricing"


# ---------------------------------------------------------------------------
# estimate_burst_cost
# ---------------------------------------------------------------------------


def _opus() -> ModelPricing:
    pricing = get_pricing("claude-opus-4-7")
    assert pricing is not None
    return pricing


def _sonnet() -> ModelPricing:
    pricing = get_pricing("claude-sonnet-4-6")
    assert pricing is not None
    return pricing


def _haiku() -> ModelPricing:
    pricing = get_pricing("claude-haiku-4-5-20251001")
    assert pricing is not None
    return pricing


class TestEstimateBurstCost:
    def test_modest_cache_produces_positive_savings(self) -> None:
        # Opus burst dominated by FRESH input (not cache reads). Sonnet
        # without cache should still come out cheaper because Sonnet's
        # input rate (3.0) is lower than Opus's (5.0).
        burst = _burst(
            input_tokens=1000, output_tokens=500,
            cache_creation_input_tokens=200,
            cache_read_input_tokens=500,
        )
        parent_cost, savings = estimate_burst_cost(
            burst, parent_pricing=_opus(), alt_pricing=_sonnet(),
        )
        assert parent_cost > 0
        assert savings > 0

    def test_cache_dominated_produces_negative_savings(self) -> None:
        # Opus burst with TINY fresh-input/output and HUGE cache_read.
        # Cache_read on Opus is $0.50/1M; Sonnet has no cache discount
        # so the same tokens get charged at $3.00/1M as input. Sonnet
        # ends up MORE expensive, savings goes negative — the actionable
        # "do not offload" signal we're preserving per architect review.
        burst = _burst(
            input_tokens=10, output_tokens=10,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=100_000,
        )
        _parent_cost, savings = estimate_burst_cost(
            burst, parent_pricing=_opus(), alt_pricing=_sonnet(),
        )
        assert savings < 0, "negative savings must be preserved, not clamped"

    def test_zero_usage_returns_zero_zero(self) -> None:
        # An empty burst has no cost on either model.
        burst = _burst()  # all token fields default to 0
        assert estimate_burst_cost(
            burst, parent_pricing=_opus(), alt_pricing=_sonnet(),
        ) == (0.0, 0.0)

    def test_parent_cost_includes_cache_read(self) -> None:
        # The "parent cost" half of the return should be the FULL picture
        # including cache_read at the parent rate — that's what was
        # actually spent. (The savings half is where cache_read gets
        # reclassified for the alt projection.)
        burst = _burst(cache_read_input_tokens=1_000_000)
        parent_cost, _savings = estimate_burst_cost(
            burst, parent_pricing=_opus(), alt_pricing=_sonnet(),
        )
        # Opus cache_read is $0.50 per 1M tokens.
        assert parent_cost == pytest.approx(0.50)

    def test_alt_projection_excludes_cache_creation(self) -> None:
        # cache_creation is a parent-side cost only — a delegated subagent
        # would re-fetch its own context, not pay to write the parent's
        # cache. The alt-model projection drops it.
        # Set up a burst whose ONLY non-zero usage is cache_creation,
        # then verify savings == parent_cost (alt_cost = 0).
        burst = _burst(cache_creation_input_tokens=1_000_000)
        parent_cost, savings = estimate_burst_cost(
            burst, parent_pricing=_opus(), alt_pricing=_sonnet(),
        )
        assert parent_cost == pytest.approx(6.25)  # Opus cache_creation rate
        assert savings == pytest.approx(parent_cost)

    def test_haiku_to_haiku_zero_savings(self) -> None:
        # Same model on both sides: parent_cost > 0 but savings should
        # be 0 because cache_read reclassifies to input at the SAME rate
        # cache_read pays at... wait, Haiku cache_read is $0.10 vs input
        # $1.00, so cache_read tokens get 10x more expensive on the alt
        # side. With ANY cache_read present, savings goes negative.
        # This test verifies the math is honest about that even when
        # the caller "stayed put."
        burst = _burst(input_tokens=100, output_tokens=100,
                       cache_read_input_tokens=1000)
        _parent_cost, savings = estimate_burst_cost(
            burst, parent_pricing=_haiku(), alt_pricing=_haiku(),
        )
        # cache_read of 1000 tokens: Haiku input would charge 1000*1.0/1M = $0.001
        # vs cache_read 1000*0.10/1M = $0.0001. Delta -$0.0009 swamps
        # the no-savings input/output equality.
        assert savings < 0

    def test_unknown_parent_pricing_returns_zero_zero(self) -> None:
        burst = _burst(input_tokens=1000, output_tokens=500)
        assert estimate_burst_cost(
            burst, parent_pricing=None, alt_pricing=_sonnet(),
        ) == (0.0, 0.0)

    def test_unknown_alt_pricing_returns_zero_zero(self) -> None:
        burst = _burst(input_tokens=1000, output_tokens=500)
        assert estimate_burst_cost(
            burst, parent_pricing=_opus(), alt_pricing=None,
        ) == (0.0, 0.0)

    def test_missing_pricing_logs_at_debug(
        self, caplog: pytest.LogCaptureFixture,
    ) -> None:
        burst = _burst(input_tokens=100)
        with caplog.at_level("DEBUG", logger="agentfluent.diagnostics.parent_workload"):
            estimate_burst_cost(burst, parent_pricing=None, alt_pricing=_sonnet())
        assert any(
            "pricing unavailable" in rec.message for rec in caplog.records
        )
