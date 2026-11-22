"""Prompt-length (context) tier pricing (#682, closes #639).

Upstream genai-prices prices some models by prompt length: every token of a request whose
prompt (``input + cache_creation + cache_read``) exceeds a threshold bills at a higher rate.
These tests pin the adapter's tier table against **genuine** upstream data, the per-request
selection rule (strict ``>``), parity with genai-prices' own ``calc_price``, the regression
lock for requests at or under a threshold, and the non-linearity guard: no figure may pick
a tier from a sum of requests.
"""

from __future__ import annotations

import dataclasses
import logging
import re
import shutil
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import genai_prices
import pytest
from genai_prices.types import Tier, TieredPrices

from agentfluent.analytics import pricing
from agentfluent.analytics._genai_source import _rate_for_prompt, _resolve_rates, _tier_table
from agentfluent.analytics.pipeline import analyze_session
from agentfluent.analytics.pricing import (
    ContextTier,
    ModelPricing,
    compute_cost,
    get_known_models,
    get_pricing,
)
from agentfluent.analytics.tokens import (
    ApiRequest,
    TokenMetrics,
    compute_subagent_token_metrics,
    compute_token_metrics,
    price_requests,
    price_trace,
    prompt_tokens,
)
from agentfluent.core.parser import parse_session
from agentfluent.core.session import Usage
from agentfluent.diagnostics.model_routing import estimate_usage_savings
from agentfluent.diagnostics.parent_workload import ToolBurst, estimate_burst_cost
from agentfluent.traces.models import SubagentTrace, TraceTurnUsage

HAIKU_55 = "claude-haiku-5-5"
OPUS_46 = "claude-opus-4-6"
SONNET_45 = "claude-sonnet-4-5-20250929"
FLAT = "claude-opus-4-8"
BEFORE_CHANGE = datetime(2026, 1, 15, tzinfo=UTC)  # opus/sonnet-4-6 tier still active
AFTER_CHANGE = datetime(2026, 3, 20, tzinfo=UTC)  # flat from 2026-03-13

FIXTURES = Path(__file__).parent.parent / "fixtures"


def _request(prompt: int, *, output: int = 1_000, cache_share: float = 0.9) -> Usage:
    """One request whose prompt size is exactly ``prompt`` (cache reads + fresh input)."""
    cache_read = int(prompt * cache_share)
    return Usage(
        input_tokens=prompt - cache_read, output_tokens=output, cache_read_input_tokens=cache_read,
    )


def _cost(usage: Usage, model: str, timestamp: datetime | None = None) -> float:
    cost = price_requests([ApiRequest.of(usage)], model, timestamp=timestamp)
    assert cost is not None
    return cost


def _priced_at(rates: ModelPricing, usage: Usage) -> float:
    return compute_cost(
        rates,
        usage.input_tokens,
        usage.output_tokens,
        usage.cache_creation_5m_input_tokens,
        usage.cache_read_input_tokens,
        cache_creation_1h_tokens=usage.cache_creation_1h_input_tokens,
    )


class TestAdapterTierTable:
    """The adapter keeps upstream's tiers instead of discarding them (AC1)."""

    def test_haiku_5_5_tier_is_5x_above_100k_on_every_key(self) -> None:
        rates = _resolve_rates(HAIKU_55)
        assert rates is not None
        assert (rates.input, rates.output, rates.cache_write_5m, rates.cache_read) == (
            0.1, 0.5, 0.125, 0.01,
        )
        [tier] = rates.context_tiers
        assert tier.start == 100_000
        assert (
            tier.rates.input, tier.rates.output, tier.rates.cache_write_5m, tier.rates.cache_read,
        ) == (0.5, 2.5, 0.625, 0.05)
        assert tier.rates.context_tiers == ()

    def test_opus_4_6_tier_is_dated(self) -> None:
        before = _resolve_rates(OPUS_46, BEFORE_CHANGE)
        after = _resolve_rates(OPUS_46, AFTER_CHANGE)
        assert before is not None and after is not None
        [tier] = before.context_tiers
        assert tier.start == 200_000
        assert (tier.rates.input, tier.rates.output) == (10.0, 37.5)
        assert after.context_tiers == ()
        # The base rates do not move with the date -- only the tier does.
        assert (before.input, before.output) == (after.input, after.output)

    def test_sonnet_4_5_tier_is_active_at_every_date(self) -> None:
        for when in (None, BEFORE_CHANGE, AFTER_CHANGE):
            rates = _resolve_rates(SONNET_45, when)
            assert rates is not None
            assert [tier.start for tier in rates.context_tiers] == [200_000]

    def test_flat_model_has_no_tiers(self) -> None:
        rates = _resolve_rates(FLAT)
        assert rates is not None
        assert rates.context_tiers == ()

    def test_breakpoint_table_matches_upstream_rule_for_any_shape(self) -> None:
        """Keys tiered at different starts, with several tiers, or flat: the table built from
        the union of starts agrees with genai-prices' per-key rule at every prompt size."""
        values: dict[str, Decimal | TieredPrices | None] = {
            "input_mtok": TieredPrices(
                base=Decimal("1"),
                tiers=[
                    Tier(start=1_000, price=Decimal("2")),
                    Tier(start=5_000, price=Decimal("4")),
                ],
            ),
            "output_mtok": TieredPrices(
                base=Decimal("10"), tiers=[Tier(start=3_000, price=Decimal("20"))],
            ),
            "cache_write_mtok": Decimal("1.25"),
            "cache_read_mtok": TieredPrices(
                base=Decimal("0.1"), tiers=[Tier(start=1_000, price=Decimal("0.3"))],
            ),
        }
        table = _tier_table(values)
        assert [tier.start for tier in table] == [1_000, 3_000, 5_000]
        for prompt in (0, 999, 1_000, 1_001, 2_999, 3_000, 3_001, 5_000, 5_001, 50_000):
            selected = None
            for tier in table:
                if prompt > tier.start:
                    selected = tier.rates
            expected = {key: _rate_for_prompt(value, prompt) for key, value in values.items()}
            got = (
                {
                    "input_mtok": selected.input,
                    "output_mtok": selected.output,
                    "cache_write_mtok": selected.cache_write_5m,
                    "cache_read_mtok": selected.cache_read,
                }
                if selected is not None
                else {key: _rate_for_prompt(value, 0) for key, value in values.items()}
            )
            assert got == expected, prompt


class TestModelPricingTiers:
    def test_for_prompt_is_strictly_greater_than(self) -> None:
        pricing_ = get_pricing(HAIKU_55)
        assert pricing_ is not None
        assert pricing_.for_prompt(100_000) is pricing_
        assert pricing_.for_prompt(100_001).input == 0.5

    def test_tier_1h_cache_write_is_twice_tier_input(self) -> None:
        pricing_ = get_pricing(HAIKU_55)
        assert pricing_ is not None
        assert pricing_.for_prompt(100_001).cache_creation_1h == 1.0  # = upstream's 1h tier

    def test_nested_tiers_are_rejected(self) -> None:
        nested = ModelPricing(
            input=2, output=2, cache_creation_5m=2, cache_read=2,
            context_tiers=(ContextTier(threshold=5, pricing=ModelPricing(3, 3, 3, 3)),),
        )
        with pytest.raises(ValueError, match="flat"):
            ModelPricing(1, 1, 1, 1, context_tiers=(ContextTier(threshold=1, pricing=nested),))

    def test_thresholds_must_ascend(self) -> None:
        flat = ModelPricing(2, 2, 2, 2)
        with pytest.raises(ValueError, match="ascending"):
            ModelPricing(
                1, 1, 1, 1,
                context_tiers=(
                    ContextTier(threshold=10, pricing=flat), ContextTier(threshold=5, pricing=flat),
                ),
            )


class TestGoldenRates:
    """Both tiers and the boundary edge on genuine upstream data (AC2, #639)."""

    @pytest.mark.parametrize(
        ("model", "threshold", "timestamp", "base", "tier"),
        [
            (HAIKU_55, 100_000, None, (0.1, 0.5, 0.01), (0.5, 2.5, 0.05)),
            (OPUS_46, 200_000, BEFORE_CHANGE, (5.0, 25.0, 0.5), (10.0, 37.5, 1.0)),
            (SONNET_45, 200_000, None, (3.0, 15.0, 0.3), (6.0, 22.5, 0.6)),
        ],
        ids=["haiku-5-5", "opus-4-6-dated", "sonnet-4-5"],
    )
    def test_boundary_edge(
        self,
        model: str,
        threshold: int,
        timestamp: datetime | None,
        base: tuple[float, float, float],
        tier: tuple[float, float, float],
    ) -> None:
        def expected(usage: Usage, rates: tuple[float, float, float]) -> float:
            inp, out, read = rates
            return (
                usage.input_tokens * inp + usage.output_tokens * out
                + usage.cache_read_input_tokens * read
            ) / 1e6

        at = _request(threshold)
        over = _request(threshold + 1)
        assert prompt_tokens(at) == threshold and prompt_tokens(over) == threshold + 1
        assert _cost(at, model, timestamp) == pytest.approx(expected(at, base))
        assert _cost(over, model, timestamp) == pytest.approx(expected(over, tier))

    def test_opus_4_6_after_the_change_stays_flat(self) -> None:
        usage = _request(250_000)
        base = get_pricing(OPUS_46, AFTER_CHANGE)
        assert base is not None
        assert _cost(usage, OPUS_46, AFTER_CHANGE) == pytest.approx(_priced_at(base, usage))


class TestUpstreamParity:
    """Per-request cost equals genai-prices' own ``calc_price`` (AC2, architect S2)."""

    @pytest.mark.parametrize(
        ("model", "timestamp"),
        [(HAIKU_55, None), (OPUS_46, BEFORE_CHANGE), (SONNET_45, None), (FLAT, None)],
    )
    @pytest.mark.parametrize(
        "usage",
        [
            Usage(input_tokens=5_000, output_tokens=700, cache_read_input_tokens=20_000),
            # cache reads dominate, at and just over each threshold
            Usage(input_tokens=1, output_tokens=10, cache_read_input_tokens=99_999),
            Usage(input_tokens=2, output_tokens=10, cache_read_input_tokens=99_999),
            Usage(input_tokens=1, output_tokens=10, cache_read_input_tokens=199_999),
            Usage(input_tokens=2, output_tokens=10, cache_read_input_tokens=199_999),
            # 5m and 1h cache writes, over both thresholds
            Usage(
                input_tokens=10_000, output_tokens=3_000, cache_read_input_tokens=180_000,
                cache_creation_input_tokens=40_000, cache_creation_5m_input_tokens=15_000,
                cache_creation_1h_input_tokens=25_000,
            ),
        ],
    )
    def test_matches_calc_price(
        self, model: str, timestamp: datetime | None, usage: Usage,
    ) -> None:
        upstream = genai_prices.calc_price(
            genai_prices.Usage(
                input_tokens=prompt_tokens(usage),
                output_tokens=usage.output_tokens,
                cache_read_tokens=usage.cache_read_input_tokens,
                # genai-prices counts 1h writes inside the cache-write total.
                cache_write_tokens=usage.cache_creation_input_tokens,
                cache_write_1h_tokens=usage.cache_creation_1h_input_tokens,
            ),
            model,
            provider_id="anthropic",
            genai_request_timestamp=timestamp or datetime.now(UTC),
        )
        assert _cost(usage, model, timestamp) == pytest.approx(float(upstream.total_price))


class TestRegressionLock:
    """A request at or under its model's threshold prices exactly as before (#639)."""

    @pytest.mark.parametrize("model", get_known_models())
    def test_under_threshold_is_the_base_rate(self, model: str) -> None:
        pricing_ = get_pricing(model)
        assert pricing_ is not None
        limit = pricing_.context_tiers[0].threshold if pricing_.context_tiers else 1_000_000
        usage = _request(limit)
        flat = dataclasses.replace(pricing_, context_tiers=())
        assert _cost(usage, model) == _priced_at(flat, usage)


class TestNonLinearityGuard:
    """No dollar figure picks a tier from a sum of requests (maintainer, 2026-11-15)."""

    def test_two_requests_under_the_threshold_price_at_base(self) -> None:
        one = _request(60_000)
        base = get_pricing(HAIKU_55)
        assert base is not None
        per_request = price_requests([ApiRequest.of(one), ApiRequest.of(one)], HAIKU_55)
        aggregate_as_one = _cost(one + one, HAIKU_55)
        assert per_request == pytest.approx(2 * _priced_at(base, one))
        assert aggregate_as_one == pytest.approx(5 * per_request)  # what a sum would do

    def test_parent_fold_prices_each_message(self) -> None:
        metrics = compute_token_metrics(parse_session(FIXTURES / "session_context_tier.jsonl"))
        [row] = metrics.by_model
        # 120K-prompt message at the 5x tier + 60K-prompt message at base.
        assert row.cost == pytest.approx(0.0094 + 0.00094)
        assert row.total_tokens == 2_000 + 1_000 + 118_000 + 1_000 + 500 + 59_000

    def test_trace_prices_each_turn(self) -> None:
        turns = [_request(120_000), _request(60_000)]
        trace = SubagentTrace(
            agent_id="a", delegation_prompt="", model=HAIKU_55,
            usage=sum(turns, Usage()),
            turn_usages=[TraceTurnUsage(message_id=f"m{i}", usage=u) for i, u in enumerate(turns)],
        )
        expected = _cost(turns[0], HAIKU_55) + _cost(turns[1], HAIKU_55)
        assert price_trace(trace) == pytest.approx(expected)
        [row] = compute_subagent_token_metrics([trace])
        assert row.cost == pytest.approx(expected)

    def test_deduped_trace_prices_the_kept_turns(self) -> None:
        trace = SubagentTrace(
            agent_id="a", delegation_prompt="", model=HAIKU_55,
            turn_usages=[TraceTurnUsage(message_id="m", usage=_request(120_000))],
        )
        trace.spend_turns = [_request(60_000)]
        assert price_trace(trace) == pytest.approx(_cost(_request(60_000), HAIKU_55))
        assert trace.spend_usage == _request(60_000)

    def test_turnless_trace_prices_its_aggregate_at_base(self) -> None:
        aggregate = _request(150_000)
        trace = SubagentTrace(agent_id="a", delegation_prompt="", model=HAIKU_55, usage=aggregate)
        base = get_pricing(HAIKU_55)
        assert base is not None
        assert price_trace(trace) == pytest.approx(_priced_at(base, aggregate))


class TestReconciliationWithTiers:
    """AC4: Σ per-agent cost + ``trace_coverage`` nested + session_spawned cost ==
    subagent-origin ``by_model`` cost, with every subagent turn over the haiku-5-5 tier."""

    def _tiered_copy(self, tmp_path: Path) -> Path:
        src = FIXTURES / "nested_session"
        dst = tmp_path / "nested_session"
        shutil.copytree(src, dst)
        for trace_file in (dst / "nested-session-1" / "subagents").glob("agent-*.jsonl"):
            text = trace_file.read_text()
            text = text.replace("claude-haiku-4-5-20251001", HAIKU_55)
            text = re.sub(
                r'"cache_read_input_tokens":\s*\d+', '"cache_read_input_tokens":150000', text,
            )
            trace_file.write_text(text)
        return dst / "nested-session-1.jsonl"

    def test_invariant_holds_and_every_turn_is_tiered(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        path = self._tiered_copy(tmp_path)

        def fold(tm: TokenMetrics) -> float:
            return sum(r.cost for r in tm.by_model if r.origin == "subagent")

        result = analyze_session(path)
        agents = sum(
            m.estimated_total_cost_usd for m in result.agent_metrics.by_agent_type.values()
        )
        coverage = result.trace_coverage
        assert coverage is not None
        beyond = coverage.nested.cost + coverage.session_spawned.cost
        assert fold(result.token_metrics) > 0
        assert agents + beyond == pytest.approx(fold(result.token_metrics))

        # haiku-5-5's tier is exactly 5x base on every key, so a fold that tiered every turn
        # is 5x the same fold priced at base.
        monkeypatch.setattr(ModelPricing, "for_prompt", lambda self, _n: self)
        base_fold = fold(analyze_session(path).token_metrics)
        assert fold(result.token_metrics) == pytest.approx(5 * base_fold)


class TestDiagnosticsPerRequest:
    def test_offload_prices_each_message_and_alt_tiers_on_the_original_prompt(self) -> None:
        messages = [_request(120_000, cache_share=0.5), _request(60_000, cache_share=0.5)]
        burst = ToolBurst(
            preceding_user_text="", assistant_text="", tool_use_blocks=[],
            usage=sum(messages, Usage()), usages=messages, model=FLAT,
        )
        estimate = estimate_burst_cost(burst, parent_model=FLAT, alt_model=HAIKU_55)
        assert estimate is not None
        parent_cost, savings = estimate
        assert parent_cost == pytest.approx(sum(_cost(u, FLAT) for u in messages))
        haiku = get_pricing(HAIKU_55)
        assert haiku is not None
        alt = sum(
            _priced_at(
                haiku.for_prompt(prompt_tokens(u)),
                Usage(
                    input_tokens=u.input_tokens + u.cache_read_input_tokens,
                    output_tokens=u.output_tokens,
                ),
            )
            for u in messages
        )
        assert savings == pytest.approx(parent_cost - alt)

    def test_offload_alt_side_tiers_on_full_prompt_not_repriced_input(self) -> None:
        # Fresh input + cache reads is 90K (under 100K), but the original prompt carries
        # 20K of cache writes too: 110K, so the alternative request tiers.
        usage = Usage(
            input_tokens=10_000, output_tokens=1_000, cache_read_input_tokens=80_000,
            cache_creation_input_tokens=20_000,
        )
        burst = ToolBurst(
            preceding_user_text="", assistant_text="", tool_use_blocks=[],
            usage=usage, usages=[usage], model=FLAT,
        )
        estimate = estimate_burst_cost(burst, parent_model=FLAT, alt_model=HAIKU_55)
        assert estimate is not None
        haiku = get_pricing(HAIKU_55)
        assert haiku is not None
        alt_usage = Usage(input_tokens=90_000, output_tokens=1_000)
        alt = _priced_at(haiku.for_prompt(110_000), alt_usage)
        assert alt == pytest.approx(5 * _priced_at(haiku, alt_usage))
        assert estimate[1] == pytest.approx(estimate[0] - alt)

    def test_model_switch_savings_tier_the_alternative_per_turn(self) -> None:
        turn = _request(150_000)
        estimate = estimate_usage_savings([([ApiRequest.of(turn)], FLAT)], HAIKU_55)
        assert estimate is not None
        haiku = get_pricing(HAIKU_55)
        assert haiku is not None
        tiered_alt = _priced_at(haiku.for_prompt(150_000), turn)
        assert estimate.savings_usd == pytest.approx(_cost(turn, FLAT) - tiered_alt)
        assert tiered_alt == pytest.approx(5 * _priced_at(haiku, turn))


class TestUnpriceableWarningDedup:
    """AC6: the "neither upstream nor residual" WARNING fires once per model."""

    def test_warns_once(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
    ) -> None:
        monkeypatch.setattr(pricing, "_WARNED_UNPRICEABLE", set())
        monkeypatch.setattr(pricing, "_resolve_rates", lambda *_a, **_k: None)
        with caplog.at_level(logging.WARNING, logger="agentfluent.analytics.pricing"):
            assert get_pricing(HAIKU_55) is None
            assert get_pricing(HAIKU_55) is None
            assert get_pricing(FLAT) is None
        messages = [r.getMessage() for r in caplog.records if "neither upstream" in r.getMessage()]
        assert len(messages) == 2
        assert sum(HAIKU_55 in m for m in messages) == 1
