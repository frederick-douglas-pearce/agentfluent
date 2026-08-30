"""Tests for model pricing lookup."""

import logging
from datetime import UTC, datetime

import pytest

from agentfluent.analytics import pricing
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
        assert pricing.cache_creation_5m == 6.25
        assert pricing.cache_read == 0.50

    def test_opus_4_8_model(self) -> None:
        # opus-4-8 (current flagship) shares the $5/$25 opus tier; 1h = 2x input.
        pricing = get_pricing("claude-opus-4-8")
        assert pricing is not None
        assert pricing.input == 5.0
        assert pricing.output == 25.0
        assert pricing.cache_creation_5m == 6.25
        assert pricing.cache_creation_1h == 10.0
        assert pricing.cache_read == 0.50

    def test_opus_4_8_context_suffix_alias(self) -> None:
        pricing = get_pricing("claude-opus-4-8[1m]")
        assert pricing is not None
        assert pricing.input == 5.0

    def test_opus_4_7_model(self) -> None:
        # Verifies issue #75: claude-opus-4-7 must return non-None pricing.
        pricing = get_pricing("claude-opus-4-7")
        assert pricing is not None
        assert pricing.input == 5.0
        assert pricing.output == 25.0
        assert pricing.cache_creation_5m == 6.25
        assert pricing.cache_read == 0.50

    def test_haiku_model(self) -> None:
        pricing = get_pricing("claude-haiku-4-5-20251001")
        assert pricing is not None
        assert pricing.input == 1.0
        assert pricing.output == 5.0
        assert pricing.cache_creation_5m == 1.25
        assert pricing.cache_read == 0.10

    def test_alias_short_name_opus(self) -> None:
        pricing = get_pricing("opus")
        assert pricing is not None
        # "opus" now resolves to opus-4-8 (current flagship).
        assert pricing.input == 5.0
        assert pricing.cache_creation_1h == 10.0

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

    def test_curated_but_unpriceable_logs_at_warning_not_debug(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
    ) -> None:
        # The other half of the split above, and the one that matters for #661. An id we
        # *curated* -- asserted we support -- that still cannot be priced contributes $0 to
        # every cost figure. ``_RESIDUAL`` is empty by design, so this log line is the only
        # signal that ever fires; leaving it at DEBUG beside the unknown-model path is
        # exactly what kept claude-opus-5's $0 invisible across ~37% of the corpus.
        monkeypatch.setattr(pricing, "_resolve_rates", lambda *a, **kw: None)
        monkeypatch.setattr(pricing, "_RESIDUAL", {})
        with caplog.at_level(logging.DEBUG, logger="agentfluent.analytics.pricing"):
            assert get_pricing("claude-opus-5") is None
        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("claude-opus-5" in r.getMessage() for r in warnings), (
            "a curated model that cannot be priced must surface at WARNING, not DEBUG"
        )


class TestSyntheticModels:
    def test_synthetic_sentinel_exported(self) -> None:
        # Issue #75: <synthetic> is filtered at the aggregation layer, but the
        # shared frozenset lives in pricing so other consumers can import it.
        assert "<synthetic>" in SYNTHETIC_MODELS


class TestComputeCost:
    def test_basic_cost(self) -> None:
        pricing = ModelPricing(input=3.0, output=15.0, cache_creation_5m=3.75, cache_read=0.30)
        cost = compute_cost(pricing, input_tokens=1_000_000, output_tokens=100_000)
        # 1M * 3.0/1M + 100K * 15.0/1M = 3.0 + 1.5 = 4.5
        assert cost == 4.5

    def test_with_cache_tokens(self) -> None:
        pricing = ModelPricing(input=3.0, output=15.0, cache_creation_5m=3.75, cache_read=0.30)
        cost = compute_cost(
            pricing,
            input_tokens=100_000,
            output_tokens=50_000,
            cache_creation_5m_tokens=200_000,
            cache_read_input_tokens=500_000,
        )
        # 100K*3/1M + 50K*15/1M + 200K*3.75/1M + 500K*0.3/1M
        # = 0.3 + 0.75 + 0.75 + 0.15 = 1.95
        assert abs(cost - 1.95) < 0.001

    def test_zero_tokens(self) -> None:
        pricing = ModelPricing(input=3.0, output=15.0, cache_creation_5m=3.75, cache_read=0.30)
        assert compute_cost(pricing, input_tokens=0, output_tokens=0) == 0.0

    def test_opus_pricing(self) -> None:
        pricing = get_pricing("claude-opus-4-6")
        assert pricing is not None
        cost = compute_cost(pricing, input_tokens=1_000_000, output_tokens=1_000_000)
        # 1M * 5/1M + 1M * 25/1M = 5 + 25 = 30
        assert cost == 30.0

    def test_cache_write_5m_only(self) -> None:
        # #534: 5-minute writes priced at 1.25x base input (opus 6.25/MTok).
        pricing = get_pricing("claude-opus-4-6")
        assert pricing is not None
        cost = compute_cost(
            pricing, input_tokens=0, output_tokens=0,
            cache_creation_5m_tokens=1_000_000,
        )
        assert abs(cost - 6.25) < 0.001

    def test_cache_write_1h_only(self) -> None:
        # #534: 1-hour writes priced at 2x base input (opus 10.0/MTok), NOT 6.25.
        pricing = get_pricing("claude-opus-4-6")
        assert pricing is not None
        cost = compute_cost(
            pricing, input_tokens=0, output_tokens=0,
            cache_creation_1h_tokens=1_000_000,
        )
        assert abs(cost - 10.0) < 0.001

    def test_cache_write_mixed_5m_and_1h(self) -> None:
        # #534: each TTL bucket billed at its own rate.
        pricing = get_pricing("claude-opus-4-6")
        assert pricing is not None
        cost = compute_cost(
            pricing, input_tokens=0, output_tokens=0,
            cache_creation_5m_tokens=1_000_000,   # 5m -> 6.25
            cache_creation_1h_tokens=1_000_000,   # 1h -> 10.0
        )
        assert abs(cost - 16.25) < 0.001

    def test_cache_creation_1h_derived_from_input(self) -> None:
        # #534: 1h rate is derived as 2x input when left at the sentinel,
        # so any ModelPricing built without it still prices 1h correctly.
        pricing = ModelPricing(
            input=3.0, output=15.0, cache_creation_5m=3.75, cache_read=0.30,
        )
        assert pricing.cache_creation_1h == 6.0

    def test_cache_creation_1h_explicit_override(self) -> None:
        # A non-sentinel value is preserved (not overwritten by the 2x rule).
        pricing = ModelPricing(
            input=3.0, output=15.0, cache_creation_5m=3.75, cache_read=0.30,
            cache_creation_1h=9.0,
        )
        assert pricing.cache_creation_1h == 9.0

    def test_all_known_models_carry_2x_1h_rate(self) -> None:
        # #534 regression guard: every seeded model prices 1h at 2x base input.
        for name in get_known_models():
            pricing = get_pricing(name)
            assert pricing is not None
            assert pricing.cache_creation_1h == pricing.input * 2.0


class TestOverlaySeam:
    """#547: the base ⊕ overlay merge seam in ``compute_cost``.

    Three composition classes stack in one documented order::

        final = (Σ rate·token / 1e6) · Π(request_multipliers[B]) + surcharge_usd[C]

    Class A (rate-level, e.g. the 1h cache write) is folded into ``ModelPricing`` upstream of
    here. The AC's base-only / base+single-overlay / base+stacked-overlay cases are exercised
    below; the stacked case combines the real shipped class-A lever (1h cache) with a
    **test-injected** class-B multiplier and class-C surcharge — proving the seam composes ≥2
    overlays across classes without pulling #536–539 (the future levers) into scope.
    """

    def _opus(self) -> ModelPricing:
        pricing = get_pricing("claude-opus-4-6")
        assert pricing is not None
        return pricing

    def test_base_only_no_overlays(self) -> None:
        # Base-only: no class-A 1h tokens, no class-B multipliers, no class-C surcharge.
        pricing = self._opus()
        cost = compute_cost(pricing, input_tokens=1_000_000, output_tokens=1_000_000)
        # 1M*5/1M + 1M*25/1M = 30.0
        assert cost == 30.0

    def test_base_plus_single_overlay_class_a_1h(self) -> None:
        # Single overlay: the real, shipped class-A lever (1h cache = 2× input, #534).
        pricing = self._opus()
        cost = compute_cost(
            pricing, input_tokens=1_000_000, output_tokens=0,
            cache_creation_1h_tokens=1_000_000,
        )
        # (1M*5 + 1M*10)/1M = 15.0  (1h priced at 2× input, not the 5m rate)
        assert cost == 15.0

    def test_base_plus_stacked_overlay_a_b_c(self) -> None:
        # Stacked across classes: real class-A 1h + injected class-B multiplier + class-C surcharge.
        pricing = self._opus()
        cost = compute_cost(
            pricing, input_tokens=1_000_000, output_tokens=0,
            cache_creation_1h_tokens=1_000_000,     # class A (real): subtotal -> 15.0
            request_multipliers=(0.5,),             # class B: 15.0 * 0.5 = 7.5
            surcharge_usd=2.0,                      # class C: 7.5 + 2.0 = 9.5
        )
        assert cost == pytest.approx(9.5)

    def test_no_op_defaults_are_bit_identical(self) -> None:
        # The seam's overlay stages default to exact identity: passing the no-op class-B/C
        # values must equal omitting them, for every golden model.
        for name in get_known_models():
            pricing = get_pricing(name)
            assert pricing is not None
            omitted = compute_cost(
                pricing, input_tokens=123_456, output_tokens=7_890,
                cache_creation_5m_tokens=1_000, cache_read_input_tokens=42_000,
                cache_creation_1h_tokens=5_000,
            )
            explicit_noop = compute_cost(
                pricing, input_tokens=123_456, output_tokens=7_890,
                cache_creation_5m_tokens=1_000, cache_read_input_tokens=42_000,
                cache_creation_1h_tokens=5_000,
                request_multipliers=(), surcharge_usd=0.0,
            )
            assert omitted == explicit_noop  # exact, not approx

    def test_class_b_multipliers_commute(self) -> None:
        # Per-request multipliers compose by multiplication → order-independent.
        pricing = self._opus()
        a = compute_cost(pricing, input_tokens=1_000_000, output_tokens=0,
                         request_multipliers=(0.5, 1.1))
        b = compute_cost(pricing, input_tokens=1_000_000, output_tokens=0,
                         request_multipliers=(1.1, 0.5))
        assert a == b

    def test_empty_multiplier_product_is_one(self) -> None:
        # An empty ``request_multipliers`` is an empty product (1.0), i.e. no scaling.
        pricing = self._opus()
        scaled = compute_cost(pricing, input_tokens=1_000_000, output_tokens=0,
                              request_multipliers=())
        unscaled = compute_cost(pricing, input_tokens=1_000_000, output_tokens=0)
        assert scaled == unscaled == 5.0

    def test_surcharge_added_after_multipliers_not_before(self) -> None:
        # Load-bearing ordering: a flat class-C surcharge must NOT be discounted by a
        # class-B multiplier. final = subtotal·Π(mult) + surcharge, never (subtotal+surcharge)·Π.
        pricing = self._opus()
        cost = compute_cost(
            pricing, input_tokens=1_000_000, output_tokens=0,   # subtotal 5.0
            request_multipliers=(0.5,), surcharge_usd=10.0,
        )
        assert cost == pytest.approx(5.0 * 0.5 + 10.0)          # 12.5, not (5.0+10.0)*0.5=7.5
        assert cost != pytest.approx((5.0 + 10.0) * 0.5)

    @pytest.mark.parametrize("model", get_known_models())
    def test_default_path_matches_pre_seam_flat_sum(self, model: str) -> None:
        # Bit-identity regression: the default (no-overlay) ``compute_cost`` equals the
        # pre-#547 flat rate×token sum exactly — the seam is a pure refactor.
        pricing = get_pricing(model)
        assert pricing is not None
        it, ot, c5, cr, c1 = 111_111, 22_222, 3_333, 44_444, 5_555
        pre_seam = (
            it * pricing.input
            + ot * pricing.output
            + c5 * pricing.cache_creation_5m
            + c1 * pricing.cache_creation_1h
            + cr * pricing.cache_read
        ) / 1_000_000
        got = compute_cost(
            pricing, input_tokens=it, output_tokens=ot,
            cache_creation_5m_tokens=c5, cache_read_input_tokens=cr,
            cache_creation_1h_tokens=c1,
        )
        assert got == pre_seam  # exact


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


# Golden base rates (USD per 1M tokens): input, output, cache_creation_5m, cache_read.
# These are the pre-migration `_PRICING` values -- the regression lock for the genai-prices
# swap (#545). COVERAGE PROBE (genai-prices==0.1.4, 2026-08-30, #661): all 9 ids below are
# covered upstream with base-tier rates equal to these values; the local `_RESIDUAL` is
# therefore empty. `claude-sonnet-4-5-20250929` is context-tiered upstream (>200K surcharge);
# the adapter takes the standard (base) tier. If a pin bump changes any value, this test
# goes red -- that is the intended gate for #82.
#
# `claude-opus-5` joined at the 0.1.4 bump (#661). It is the ONE intentional delta of that
# bump and it is one-directional: opus-5 went None -> real pricing (it was $0 before, on ~37%
# of a real corpus). Every other id below is byte-identical across 0.0.71 -> 0.1.4, which is
# what makes that PR's cost-neutrality claim checkable here rather than merely asserted.
_GOLDEN_RATES: dict[str, tuple[float, float, float, float]] = {
    "claude-opus-5": (5.0, 25.0, 6.25, 0.50),
    "claude-opus-4-8": (5.0, 25.0, 6.25, 0.50),
    "claude-opus-4-7": (5.0, 25.0, 6.25, 0.50),
    "claude-opus-4-6": (5.0, 25.0, 6.25, 0.50),
    "claude-opus-4-5-20251101": (5.0, 25.0, 6.25, 0.50),
    "claude-sonnet-4-6": (3.0, 15.0, 3.75, 0.30),
    "claude-sonnet-4-5-20250929": (3.0, 15.0, 3.75, 0.30),
    "claude-sonnet-4-20250514": (3.0, 15.0, 3.75, 0.30),
    "claude-haiku-4-5-20251001": (1.0, 5.0, 1.25, 0.10),
}


class TestGoldenRateRegression:
    """The no-op bar: post-migration rates equal the pre-migration `_PRICING` values."""

    @pytest.mark.parametrize("model,expected", list(_GOLDEN_RATES.items()))
    def test_rate_equals_pre_migration(
        self, model: str, expected: tuple[float, float, float, float]
    ) -> None:
        pricing = get_pricing(model)
        assert pricing is not None, f"{model} lost pricing in the migration"
        got = (
            pricing.input,
            pricing.output,
            pricing.cache_creation_5m,
            pricing.cache_read,
        )
        assert got == expected

    def test_golden_set_is_exactly_the_curated_registry(self) -> None:
        # Every known model has a golden lock, and no golden id is unknown.
        assert set(_GOLDEN_RATES) == set(get_known_models())


class TestCuratedRegistry:
    def test_known_models_matches_the_golden_set(self) -> None:
        # Deliberately count-agnostic: the registry grows (8 -> 9 at #661), and renaming a
        # magic number on every addition is churn that asserts nothing extra.
        assert get_known_models() == sorted(_GOLDEN_RATES)

    def test_no_curated_id_is_a_legacy_generation(self) -> None:
        # UNIVERSAL invariant over the whole curated set, restored family-agnostically. The
        # original form (`all(m.startswith("claude-opus-4") or ...)`) had to be widened for
        # every new family and broke outright on `claude-opus-5`; this denies the legacy
        # generations by prefix instead, so a new family needs no edit and a dated legacy id
        # (`claude-3-opus-20240229`, `claude-instant-1`) is still caught.
        legacy_prefixes = ("claude-v1", "claude-2", "claude-instant", "claude-3-")
        leaked = [m for m in get_known_models() if m.startswith(legacy_prefixes)]
        assert leaked == [], f"legacy generation(s) leaked into the curated registry: {leaked}"

    @pytest.mark.parametrize(
        "legacy_id",
        [
            "claude-v1",
            "claude-2",
            "claude-3-haiku",
            "claude-3-sonnet",
            "claude-3-opus-latest",
            "claude-3-5-sonnet",
            "claude-3-5-haiku-latest",
            "claude-3-7-sonnet-latest",
            "claude-opus-4-0",
            "claude-opus-4-1",
        ],
    )
    def test_upstream_catalog_not_leaked(self, legacy_id: str) -> None:
        # genai-prices carries many ids AgentFluent deliberately does not curate. Asserted as a
        # DENY-canary over known-legacy ids rather than as an allow-prefix over the curated set
        # (#661 architect review): a `startswith("claude-...-4")` predicate has to be widened for
        # every new family (it broke on `claude-opus-5`), and widening it carelessly is exactly
        # how an id nobody vetted slips in.
        #
        # NOT listed: `claude-sonnet-4-0`. genai-prices resolves our curated dated id
        # `claude-sonnet-4-20250514` to that upstream model id, so it is very much curated --
        # naming it here would misinform, and would turn red as a phantom "legacy leak" the
        # moment `_KNOWN_MODELS` normalizes to undated ids (as opus-4-6/4-7/4-8 already have).
        #
        # Deliberately ABSENT from this list: `claude-sonnet-5` and `claude-fable-5`. Those are
        # current models that are simply uncurated -- genai-prices prices both -- so asserting
        # they must stay out would lock in the silent-$0 defect tracked by the #661 architect
        # review's Concern 2, not guard against it. This canary is for *legacy* ids only.
        assert legacy_id not in get_known_models()

    @pytest.mark.parametrize("canonical", ["claude-opus-4-7", "claude-sonnet-4-6",
                                           "claude-haiku-4-5-20251001"])
    def test_canonical_targets_never_none(self, canonical: str) -> None:
        # model_routing savings math breaks on a None -- recommendation targets must price.
        assert get_pricing(canonical) is not None


class TestOpus5Regression:
    """#661: `claude-opus-5` priced at $0 -- ~37% of a real corpus, logged at DEBUG only.

    Pins the defect at the surface a user actually hits (`get_pricing`), not at the adapter.
    Two independent things had to be true for the bug, so both are asserted: the id must be
    present upstream (the `genai-prices` pin -- it is absent from 0.0.71 entirely), and it must
    be in the curated `_KNOWN_MODELS` allow-list, which `get_pricing` consults FIRST and which
    fails closed to None.
    """

    @pytest.mark.parametrize("model_id", ["claude-opus-5", "claude-opus-5[1m]"])
    def test_opus_5_is_priced(self, model_id: str) -> None:
        pricing = get_pricing(model_id)
        assert pricing is not None, (
            f"{model_id} resolved to None -- it would contribute $0 to every cost figure, "
            "silently (the lookup miss logs at DEBUG). This is #661 regressing."
        )
        assert pricing.input == 5.0
        assert pricing.output == 25.0
        assert pricing.cache_creation_5m == 6.25
        assert pricing.cache_read == 0.50

    def test_opus_5_1h_write_is_derived_not_upstream(self) -> None:
        # genai-prices 0.1.4 DOES carry `cache_write_1h_mtok` for this model, but the adapter
        # deliberately does not read it (that is #662). The 1h rate must still come from the
        # local 2x-input derivation -- same number here, different provenance, and the guard
        # that stops #662's field being absorbed early.
        pricing = get_pricing("claude-opus-5")
        assert pricing is not None
        assert pricing.cache_creation_1h == 10.0
        assert pricing.cache_creation_1h == pytest.approx(2.0 * pricing.input)

    def test_opus_5_context_suffix_resolves_to_same_rates(self) -> None:
        assert get_pricing("claude-opus-5[1m]") == get_pricing("claude-opus-5")


class TestCacheCreation1hNonCollapse:
    """The 1h dimension (#534) is derived (2x input), never collapsed onto the 5m rate."""

    @pytest.mark.parametrize("model", list(_GOLDEN_RATES))
    def test_1h_is_2x_input_and_distinct_from_5m(self, model: str) -> None:
        pricing = get_pricing(model)
        assert pricing is not None
        assert pricing.cache_creation_1h == pytest.approx(2.0 * pricing.input)
        assert pricing.cache_creation_1h != pricing.cache_creation_5m


class TestModelConstants:
    """#252 fold: MODEL_* live in pricing.py; the re-export chain stays green."""

    def test_single_source_across_chain(self) -> None:
        from agentfluent.analytics import pricing as p
        from agentfluent.diagnostics import _complexity, delegation

        assert (
            p.MODEL_OPUS
            == _complexity.MODEL_OPUS
            == delegation.MODEL_OPUS
            == "claude-opus-4-7"
        )
        assert (
            p.MODEL_SONNET
            == _complexity.MODEL_SONNET
            == delegation.MODEL_SONNET
            == "claude-sonnet-4-6"
        )
        assert (
            p.MODEL_HAIKU
            == _complexity.MODEL_HAIKU
            == delegation.MODEL_HAIKU
            == "claude-haiku-4-5"
        )

    def test_model_opus_intentionally_lags_opus_alias(self) -> None:
        from agentfluent.analytics.pricing import MODEL_OPUS

        # MODEL_OPUS (routing target) differs from the `opus` default-pricing alias (-> 4-8).
        assert MODEL_OPUS == "claude-opus-4-7"
        assert get_pricing("opus") is not None  # alias still resolves (to 4-8)
        assert get_pricing(MODEL_OPUS) is not None


class TestResidualFallback:
    """The documented local-overlay escape hatch when genai-prices lacks a model.

    ``_RESIDUAL`` is empty at genai-prices==0.1.4 (full upstream coverage), so these
    tests simulate an upstream miss to lock the fallback contract for a future uncovered
    model.
    """

    def test_residual_used_when_upstream_misses(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from agentfluent.analytics import pricing

        sentinel = ModelPricing(
            input=9.0, output=9.0, cache_creation_5m=9.0, cache_read=9.0
        )
        # Known model, but force the upstream lookup to miss and supply a residual entry.
        monkeypatch.setattr(pricing, "_resolve_rates", lambda ref, timestamp=None: None)
        monkeypatch.setitem(pricing._RESIDUAL, "claude-opus-4-8", sentinel)
        assert pricing.get_pricing("claude-opus-4-8") is sentinel

    def test_known_but_uncovered_returns_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from agentfluent.analytics import pricing

        # Known id, no upstream, no residual -> None (degrade gracefully, never crash).
        monkeypatch.setattr(pricing, "_resolve_rates", lambda ref, timestamp=None: None)
        assert pricing.get_pricing("claude-opus-4-8") is None


class TestDateAwareLookup:
    """#546 date-aware base-rate lookup: timestamp threads to the resolver; None → latest.

    These test the *mechanism*, not a fabricated rate change (Fred's Notes forbid the
    latter, and the falsifier confirmed no priced model carries a dated base-rate change).
    """

    def test_timestamp_none_omitted_is_latest(self) -> None:
        # Omitting the timestamp and passing None both mean "latest" and must agree.
        assert get_pricing("claude-opus-4-6") == get_pricing("claude-opus-4-6", None)

    def test_timestamp_forwarded_to_resolver(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # AC#5(b): the caller-supplied timestamp actually reaches _resolve_rates.
        from agentfluent.analytics import pricing

        seen: dict[str, object] = {}

        def _spy(ref: str, timestamp: datetime | None = None) -> None:
            seen["ref"] = ref
            seen["timestamp"] = timestamp
            return None  # force residual/None path; we only care about the args

        monkeypatch.setattr(pricing, "_resolve_rates", _spy)
        when = datetime(2026, 2, 1, tzinfo=UTC)
        pricing.get_pricing("claude-opus-4-6", when)
        assert seen["ref"] == "claude-opus-4-6"
        assert seen["timestamp"] is when

    def test_timestamp_forwarded_through_alias(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # An alias resolves to canonical first, then the timestamp still threads through.
        from agentfluent.analytics import pricing

        seen: dict[str, object] = {}
        monkeypatch.setattr(
            pricing,
            "_resolve_rates",
            lambda ref, timestamp=None: seen.update(ref=ref, timestamp=timestamp),
        )
        when = datetime(2026, 5, 1, tzinfo=UTC)
        pricing.get_pricing("opus", when)  # alias -> claude-opus-4-8
        assert seen["ref"] == "claude-opus-4-8"
        assert seen["timestamp"] is when

    @pytest.mark.parametrize("model", ["claude-opus-4-6", "claude-sonnet-4-6"])
    def test_either_side_of_genuine_constraint_resolves(self, model: str) -> None:
        # AC#5(d): opus-4-6 / sonnet-4-6 carry a genuine StartDateConstraint(2026-03-13);
        # dates on both sides must resolve without error (they price the same base today).
        before = get_pricing(model, datetime(2026, 1, 1, tzinfo=UTC))
        after = get_pricing(model, datetime(2026, 6, 1, tzinfo=UTC))
        assert before is not None and after is not None


class TestForwardCompatGuard:
    """#546 tripwire: no priced model has a dated BASE-rate change today.

    If genai-prices ever ships a dated Anthropic base-rate change for a model we price,
    this fails loudly — the signal to write the user-facing 'historical costs now differ'
    note (docs/COST_MODEL.md) and activate #543's cross-date-delta caveat for `diff`.
    """

    _EARLY = datetime(2024, 6, 1, tzinfo=UTC)

    @pytest.mark.parametrize("model", list(_GOLDEN_RATES))
    def test_base_rate_is_date_invariant(self, model: str) -> None:
        early = get_pricing(model, self._EARLY)
        latest = get_pricing(model, None)
        assert early is not None and latest is not None
        early_tuple = (
            early.input, early.output, early.cache_creation_5m, early.cache_read
        )
        latest_tuple = (
            latest.input, latest.output, latest.cache_creation_5m, latest.cache_read
        )
        assert early_tuple == latest_tuple, (
            f"{model} base rate changed by date ({early_tuple} != {latest_tuple}). "
            "genai-prices now carries a dated base-rate change for a priced model — "
            "date-aware pricing (#546) is no longer inert. Write the historical-cost "
            "restatement note in docs/COST_MODEL.md and activate #543's diff caveat."
        )
