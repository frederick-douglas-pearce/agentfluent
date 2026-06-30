"""Tests for the shared model-selection helpers in ``_complexity`` (#170)."""

from __future__ import annotations

from agentfluent.diagnostics._complexity import (
    MODEL_HAIKU,
    MODEL_OPUS,
    MODEL_SONNET,
    classify_model_tier,
    faster_tier,
    select_target_model,
)


class TestSelectTargetModel:
    def test_downgrade_overspec_simple(self) -> None:
        # complexity-mismatch overspec path: aim at the simple tier.
        assert select_target_model(MODEL_OPUS, "simple") == MODEL_HAIKU

    def test_upgrade_underspec_moderate(self) -> None:
        # complexity-mismatch underspec path: deliberate one-step-up.
        assert select_target_model(MODEL_HAIKU, "moderate") == MODEL_SONNET

    def test_same_tier_returns_none(self) -> None:
        # AC3: suggested target == current → no recommendation.
        assert select_target_model(MODEL_SONNET, "moderate") is None
        assert select_target_model("claude-sonnet-4-5-20250929", "moderate") is None

    def test_unknown_current_returns_none(self) -> None:
        assert select_target_model(None, "simple") is None
        assert select_target_model("", "simple") is None


class TestFasterTier:
    def test_opus_steps_to_moderate(self) -> None:
        assert faster_tier(MODEL_OPUS) == "moderate"

    def test_sonnet_steps_to_simple(self) -> None:
        assert faster_tier(MODEL_SONNET) == "simple"

    def test_haiku_at_floor_returns_none(self) -> None:
        assert faster_tier(MODEL_HAIKU) is None

    def test_unknown_model_returns_none(self) -> None:
        assert faster_tier(None) is None
        assert faster_tier("") is None

    def test_unrecognized_family_defaults_moderate_then_steps(self) -> None:
        # Unknown family classifies "moderate" → one tier down is simple.
        assert classify_model_tier("claude-experimental-9000") == "moderate"
        assert faster_tier("claude-experimental-9000") == "simple"
