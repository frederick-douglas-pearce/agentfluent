"""Tests for cost estimation on parent-thread tool-bursts (sub-issue C of #189).

Covers ``pick_alternative_model`` (tier mapping) and ``estimate_burst_cost``
(parent cost + signed savings). The key invariant the architect review
locked in: savings is signed; negative-savings clusters are surfaced,
not clamped to zero.
"""

from __future__ import annotations

import dataclasses
import inspect
from datetime import datetime

import pytest

from agentfluent.analytics import tokens
from agentfluent.analytics.pricing import compute_cost, get_pricing
from agentfluent.analytics.tokens import ModelTokenBreakdown
from agentfluent.core.session import Usage
from agentfluent.diagnostics import parent_workload
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
OPUS = "claude-opus-4-7"
SONNET = "claude-sonnet-4-6"
HAIKU = "claude-haiku-4-5-20251001"
UNPRICED = "not-a-real-model"
assert get_pricing(OPUS) is not None
assert get_pricing(SONNET) is not None
assert get_pricing(HAIKU) is not None
assert get_pricing(UNPRICED) is None


def _burst(
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_creation_input_tokens: int = 0,
    cache_read_input_tokens: int = 0,
    cache_creation_1h_tokens: int = 0,
    model: str = "claude-opus-4-7",
) -> ToolBurst:
    # Mirror the parser fallback: undifferentiated writes land in the 5m
    # bucket; `cache_creation_1h_tokens` carries the 1h portion (#534).
    return ToolBurst(
        preceding_user_text="",
        assistant_text="",
        tool_use_blocks=[],
        usage=Usage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_creation_input_tokens=(
                cache_creation_input_tokens + cache_creation_1h_tokens
            ),
            cache_read_input_tokens=cache_read_input_tokens,
            cache_creation_5m_input_tokens=cache_creation_input_tokens,
            cache_creation_1h_input_tokens=cache_creation_1h_tokens,
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


def _old_formula(
    burst: ToolBurst, parent_model: str, alt_model: str,
) -> tuple[float, float]:
    """The pre-#707 implementation, recomputed independently of ``price_usage``.

    ``compute_cost`` on the parent's observed usage, and on the alternative
    side the cache-dropping counterfactual (cache reads as fresh input, cache
    writes dropped). #707 moved the pricing onto ``price_usage``; the figures
    must not move (AC2), whatever rates the genai-prices pin carries.
    """
    parent_pricing = get_pricing(parent_model)
    alt_pricing = get_pricing(alt_model)
    assert parent_pricing is not None and alt_pricing is not None
    u = burst.usage
    parent_cost = compute_cost(
        parent_pricing,
        u.input_tokens, u.output_tokens,
        u.cache_creation_5m_input_tokens, u.cache_read_input_tokens,
        cache_creation_1h_tokens=u.cache_creation_1h_input_tokens,
    )
    alt_cost = compute_cost(
        alt_pricing, u.input_tokens + u.cache_read_input_tokens, u.output_tokens, 0, 0,
    )
    return (parent_cost, parent_cost - alt_cost)


def _priced(
    burst: ToolBurst, parent_model: str, alt_model: str,
) -> tuple[float, float]:
    estimate = estimate_burst_cost(burst, parent_model=parent_model, alt_model=alt_model)
    assert estimate is not None
    return estimate


class TestEstimateBurstCost:
    def test_modest_cache_produces_positive_savings(self) -> None:
        burst = _burst(
            input_tokens=1000, output_tokens=500,
            cache_creation_input_tokens=200,
            cache_read_input_tokens=500,
        )
        parent_cost, savings = _priced(burst, OPUS, SONNET)
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
        _parent_cost, savings = _priced(burst, OPUS, SONNET)
        assert savings < 0, "negative savings must be preserved, not clamped"

    def test_zero_usage_prices_to_zero_not_none(self) -> None:
        # Priced models with no usage cost $0 — distinct from "unpriced" (None).
        assert estimate_burst_cost(
            _burst(), parent_model=OPUS, alt_model=SONNET,
        ) == (0.0, 0.0)

    def test_parent_cost_includes_cache_read(self) -> None:
        burst = _burst(cache_read_input_tokens=1_000_000)
        parent_cost, _savings = _priced(burst, OPUS, SONNET)
        # Opus cache_read is $0.50 per 1M tokens.
        assert parent_cost == pytest.approx(0.50)

    def test_alt_projection_excludes_cache_creation(self) -> None:
        # cache_creation is a parent-side-only cost; the alt-model
        # projection drops it (a delegated subagent would re-fetch its
        # own context, not pay to write the parent's cache).
        burst = _burst(cache_creation_input_tokens=1_000_000)
        parent_cost, savings = _priced(burst, OPUS, SONNET)
        assert parent_cost == pytest.approx(6.25)  # Opus 5m cache-write rate
        assert savings == pytest.approx(parent_cost)

    def test_parent_cost_uses_1h_cache_write_rate(self) -> None:
        # #534: 1h writes cost 2x base (Opus 10.0/MTok), not the 5m 6.25.
        burst = _burst(cache_creation_1h_tokens=1_000_000)
        parent_cost, savings = _priced(burst, OPUS, SONNET)
        assert parent_cost == pytest.approx(10.0)
        assert savings == pytest.approx(parent_cost)

    def test_alt_prices_cache_reads_at_the_alt_input_rate(self) -> None:
        # The counterfactual: cache reads become fresh input on the alt side.
        burst = _burst(cache_read_input_tokens=1_000_000)
        parent_cost, savings = _priced(burst, OPUS, SONNET)
        sonnet = get_pricing(SONNET)
        assert sonnet is not None
        alt_cost = parent_cost - savings
        assert alt_cost == pytest.approx(sonnet.input)
        assert alt_cost != pytest.approx(sonnet.cache_read)

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
        _parent_cost, savings = _priced(burst, HAIKU, HAIKU)
        assert savings < 0

    @pytest.mark.parametrize(
        ("parent_model", "alt_model"),
        [(OPUS, SONNET), (SONNET, HAIKU), (HAIKU, HAIKU)],
    )
    @pytest.mark.parametrize(
        "burst",
        [
            _burst(input_tokens=1234, output_tokens=567),
            _burst(
                input_tokens=1000, output_tokens=500,
                cache_creation_input_tokens=200, cache_read_input_tokens=500,
            ),
            _burst(
                input_tokens=37, output_tokens=4321,
                cache_creation_input_tokens=9_001,
                cache_creation_1h_tokens=12_345,
                cache_read_input_tokens=250_000,
            ),
            _burst(cache_creation_1h_tokens=1_000_000),
        ],
        ids=["no-cache", "5m-write+read", "5m+1h-write+read", "1h-write-only"],
    )
    def test_figures_unchanged_from_the_pre_707_formula(
        self, burst: ToolBurst, parent_model: str, alt_model: str,
    ) -> None:
        # AC2: routing through price_usage moved the path, not the figures.
        got = _priced(burst, parent_model, alt_model)
        expected = _old_formula(burst, parent_model, alt_model)
        assert got == pytest.approx(expected, rel=1e-12, abs=1e-15)

    @pytest.mark.parametrize(
        ("parent_model", "alt_model"),
        [(UNPRICED, SONNET), (OPUS, UNPRICED), (UNPRICED, UNPRICED)],
    )
    def test_unpriced_model_returns_none(
        self, parent_model: str, alt_model: str,
    ) -> None:
        # None is "no estimate", never "free" (0.0).
        burst = _burst(input_tokens=1000, output_tokens=500)
        assert estimate_burst_cost(
            burst, parent_model=parent_model, alt_model=alt_model,
        ) is None

    @pytest.mark.parametrize(
        ("parent_model", "alt_model"),
        [("", SONNET), (None, SONNET), (OPUS, ""), (OPUS, None)],
    )
    def test_missing_model_id_returns_none_without_a_price_lookup(
        self,
        parent_model: str | None,
        alt_model: str | None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # An empty/None id short-circuits before price_usage: the result must
        # not depend on get_pricing("") happening to return None.
        looked_up: list[str] = []
        real_get_pricing = tokens.get_pricing

        def _spy(model: str, *args: object, **kwargs: object) -> object:
            looked_up.append(model)
            return real_get_pricing(model, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(tokens, "get_pricing", _spy)
        burst = _burst(input_tokens=1000, output_tokens=500)
        assert estimate_burst_cost(
            burst, parent_model=parent_model, alt_model=alt_model,
        ) is None
        assert "" not in looked_up
        assert None not in looked_up

    def test_missing_pricing_logs_at_debug(
        self, caplog: pytest.LogCaptureFixture,
    ) -> None:
        burst = _burst(input_tokens=100)
        with caplog.at_level("DEBUG", logger="agentfluent.diagnostics.parent_workload"):
            estimate_burst_cost(burst, parent_model=UNPRICED, alt_model=SONNET)
        assert any(
            "pricing unavailable" in rec.message and UNPRICED in rec.message
            for rec in caplog.records
        )


class TestEstimateBurstCostPricingPath:
    """AC1: both sides price through ``analytics.tokens._cost_of`` (#707).

    ``_cost_of`` is where per-request levers land (context tiers #682,
    multipliers and surcharges #537-#539), so the guard pins that the
    offload estimate reaches it — not merely that it calls ``price_usage``.
    """

    def test_both_sides_price_through_cost_of(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        calls: list[ModelTokenBreakdown] = []
        real_cost_of = tokens._cost_of

        def _spy(
            breakdown: ModelTokenBreakdown, timestamp: datetime | None = None,
        ) -> float | None:
            calls.append(dataclasses.replace(breakdown))
            return real_cost_of(breakdown, timestamp)

        monkeypatch.setattr(tokens, "_cost_of", _spy)
        burst = _burst(
            input_tokens=1000, output_tokens=500,
            cache_creation_input_tokens=200,
            cache_creation_1h_tokens=300,
            cache_read_input_tokens=4000,
        )
        got = _priced(burst, OPUS, SONNET)

        assert [c.model for c in calls] == [OPUS, SONNET]
        parent, alt = calls
        assert (
            parent.input_tokens, parent.output_tokens,
            parent.cache_creation_5m_tokens, parent.cache_creation_1h_tokens,
            parent.cache_read_input_tokens,
        ) == (1000, 500, 200, 300, 4000)
        # Alt side: the cache-dropping counterfactual reaches _cost_of.
        assert (
            alt.input_tokens, alt.output_tokens,
            alt.cache_creation_5m_tokens, alt.cache_creation_1h_tokens,
            alt.cache_read_input_tokens,
        ) == (5000, 500, 0, 0, 0)
        # Call-through: the same figures as the pre-#707 formula.
        assert got == pytest.approx(_old_formula(burst, OPUS, SONNET), rel=1e-12)

    def test_cost_of_result_is_what_the_estimate_reports(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # A change at _cost_of (e.g. a future tier) reaches the offload figures.
        def _fixed(
            breakdown: ModelTokenBreakdown, timestamp: datetime | None = None,
        ) -> float | None:
            return 7.0 if breakdown.model == OPUS else 3.0

        monkeypatch.setattr(tokens, "_cost_of", _fixed)
        burst = _burst(input_tokens=1, output_tokens=1)
        assert estimate_burst_cost(
            burst, parent_model=OPUS, alt_model=SONNET,
        ) == (7.0, 4.0)

    def test_parent_workload_has_no_pricing_path_of_its_own(self) -> None:
        source = inspect.getsource(parent_workload)
        for name in ("compute_cost", "get_pricing", "ModelPricing"):
            assert name not in source, f"parent_workload.py references {name}"
