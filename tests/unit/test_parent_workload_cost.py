"""Tests for cost estimation on parent-thread tool-bursts (sub-issue C of #189).

Covers ``pick_alternative_model`` (tier mapping) and ``estimate_burst_cost``
(parent cost + signed savings). The key invariant the architect review
locked in: savings is signed; negative-savings clusters are surfaced,
not clamped to zero.
"""

from __future__ import annotations

import pytest

from agentfluent.analytics.pricing import get_pricing
from agentfluent.core.session import Usage
from agentfluent.diagnostics.parent_workload import (
    ToolBurst,
    estimate_burst_cost,
    pick_alternative_model,
)

# Resolve known-model pricing once at import time. The asserts here
# also act as a sanity check that the pricing module still recognises
# these canonical ids — if Anthropic renames one and the pricing module
# isn't updated to match, the import-time assert fires before any test
# silently runs against the wrong pricing.
_OPUS = get_pricing("claude-opus-4-7")
_SONNET = get_pricing("claude-sonnet-4-6")
_HAIKU = get_pricing("claude-haiku-4-5-20251001")
assert _OPUS is not None
assert _SONNET is not None
assert _HAIKU is not None


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
        # MODEL_HAIKU from delegation is the undated alias; both forms
        # resolve to the same pricing entry via _ALIASES.
        assert pick_alternative_model("claude-sonnet-4-6") == "claude-haiku-4-5"

    def test_dated_sonnet_routes_to_haiku(self) -> None:
        assert (
            pick_alternative_model("claude-sonnet-4-5-20250929")
            == "claude-haiku-4-5"
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


class TestEstimateBurstCost:
    def test_modest_cache_produces_positive_savings(self) -> None:
        burst = _burst(
            input_tokens=1000, output_tokens=500,
            cache_creation_input_tokens=200,
            cache_read_input_tokens=500,
        )
        parent_cost, savings = estimate_burst_cost(
            burst, parent_pricing=_OPUS, alt_pricing=_SONNET,
        )
        assert parent_cost > 0
        assert savings > 0

    def test_cache_dominated_produces_negative_savings(self) -> None:
        # Opus cache_read is $0.50/1M; Sonnet has no cache discount so
        # the same tokens get charged at $3.00/1M as input. Sonnet ends
        # up more expensive — the actionable "do not offload" signal.
        burst = _burst(
            input_tokens=10, output_tokens=10,
            cache_read_input_tokens=100_000,
        )
        _parent_cost, savings = estimate_burst_cost(
            burst, parent_pricing=_OPUS, alt_pricing=_SONNET,
        )
        assert savings < 0, "negative savings must be preserved, not clamped"

    def test_zero_usage_returns_zero_zero(self) -> None:
        burst = _burst()
        assert estimate_burst_cost(
            burst, parent_pricing=_OPUS, alt_pricing=_SONNET,
        ) == (0.0, 0.0)

    def test_parent_cost_includes_cache_read(self) -> None:
        burst = _burst(cache_read_input_tokens=1_000_000)
        parent_cost, _savings = estimate_burst_cost(
            burst, parent_pricing=_OPUS, alt_pricing=_SONNET,
        )
        # Opus cache_read is $0.50 per 1M tokens.
        assert parent_cost == pytest.approx(0.50)

    def test_alt_projection_excludes_cache_creation(self) -> None:
        # cache_creation is a parent-side-only cost; the alt-model
        # projection drops it (a delegated subagent would re-fetch its
        # own context, not pay to write the parent's cache).
        burst = _burst(cache_creation_input_tokens=1_000_000)
        parent_cost, savings = estimate_burst_cost(
            burst, parent_pricing=_OPUS, alt_pricing=_SONNET,
        )
        assert parent_cost == pytest.approx(6.25)  # Opus cache_creation rate
        assert savings == pytest.approx(parent_cost)

    def test_haiku_to_haiku_negative_savings_from_cache_reclassification(
        self,
    ) -> None:
        # Same model on both sides — but cache_read still reclassifies as
        # fresh input under the no-cache-benefit projection. Haiku
        # cache_read is $0.10/1M vs input $1.00/1M, so any cache_read
        # produces a 10x cost penalty on the alt side and savings goes
        # negative. The math is honest about reclassification even when
        # the caller "stayed put."
        burst = _burst(input_tokens=100, output_tokens=100,
                       cache_read_input_tokens=1000)
        _parent_cost, savings = estimate_burst_cost(
            burst, parent_pricing=_HAIKU, alt_pricing=_HAIKU,
        )
        assert savings < 0

    def test_unknown_parent_pricing_returns_zero_zero(self) -> None:
        burst = _burst(input_tokens=1000, output_tokens=500)
        assert estimate_burst_cost(
            burst, parent_pricing=None, alt_pricing=_SONNET,
        ) == (0.0, 0.0)

    def test_unknown_alt_pricing_returns_zero_zero(self) -> None:
        burst = _burst(input_tokens=1000, output_tokens=500)
        assert estimate_burst_cost(
            burst, parent_pricing=_OPUS, alt_pricing=None,
        ) == (0.0, 0.0)

    def test_missing_pricing_logs_at_debug(
        self, caplog: pytest.LogCaptureFixture,
    ) -> None:
        burst = _burst(input_tokens=100)
        with caplog.at_level("DEBUG", logger="agentfluent.diagnostics.parent_workload"):
            estimate_burst_cost(burst, parent_pricing=None, alt_pricing=_SONNET)
        assert any(
            "pricing unavailable" in rec.message for rec in caplog.records
        )
