"""Tests for token and cost analytics."""

import logging

import pytest

from agentfluent.analytics.tokens import (
    ModelTokenBreakdown,
    TokenMetrics,
    compute_subagent_token_metrics,
    compute_token_metrics,
    fold_subagent_metrics_in,
)
from agentfluent.core.session import ContentBlock, SessionMessage, Usage
from agentfluent.traces.models import SubagentTrace


def _by_model(metrics: TokenMetrics, model: str, origin: str = "parent") -> ModelTokenBreakdown:
    """Find the breakdown row matching ``(model, origin)`` or fail."""
    for row in metrics.by_model:
        if row.model == model and row.origin == origin:
            return row
    raise AssertionError(
        f"by_model has no ({model!r}, {origin!r}); rows: "
        f"{[(r.model, r.origin) for r in metrics.by_model]}",
    )


def _model_names(metrics: TokenMetrics) -> set[str]:
    return {row.model for row in metrics.by_model}


def _assistant(
    model: str = "claude-sonnet-4-20250514",
    input_tokens: int = 100,
    output_tokens: int = 50,
    cache_creation: int = 0,
    cache_read: int = 0,
) -> SessionMessage:
    """Helper to create an assistant message with usage."""
    return SessionMessage(
        type="assistant",
        model=model,
        usage=Usage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_creation_input_tokens=cache_creation,
            cache_read_input_tokens=cache_read,
        ),
        content_blocks=[ContentBlock(type="text", text="response")],
    )


class TestComputeTokenMetrics:
    def test_single_message_totals(self) -> None:
        messages = [_assistant(input_tokens=1000, output_tokens=200)]
        metrics = compute_token_metrics(messages)
        assert metrics.input_tokens == 1000
        assert metrics.output_tokens == 200
        assert metrics.total_tokens == 1200
        assert metrics.api_call_count == 1

    def test_multiple_messages_sum(self) -> None:
        messages = [
            _assistant(input_tokens=100, output_tokens=50),
            _assistant(input_tokens=200, output_tokens=100),
        ]
        metrics = compute_token_metrics(messages)
        assert metrics.input_tokens == 300
        assert metrics.output_tokens == 150
        assert metrics.api_call_count == 2

    def test_cache_tokens_included(self) -> None:
        messages = [
            _assistant(
                input_tokens=100, output_tokens=50, cache_creation=500, cache_read=300,
            ),
        ]
        metrics = compute_token_metrics(messages)
        assert metrics.cache_creation_input_tokens == 500
        assert metrics.cache_read_input_tokens == 300
        assert metrics.total_tokens == 950

    def test_empty_messages(self) -> None:
        metrics = compute_token_metrics([])
        assert metrics.total_tokens == 0
        assert metrics.total_cost == 0.0
        assert metrics.api_call_count == 0
        assert metrics.by_model == []

    def test_skips_non_assistant(self) -> None:
        messages = [
            SessionMessage(type="user"),
            _assistant(input_tokens=100, output_tokens=50),
            SessionMessage(type="user"),
        ]
        metrics = compute_token_metrics(messages)
        assert metrics.input_tokens == 100
        assert metrics.api_call_count == 1

    def test_skips_assistant_without_usage(self) -> None:
        messages = [
            SessionMessage(type="assistant", model="claude-sonnet-4-20250514"),
            _assistant(input_tokens=100, output_tokens=50),
        ]
        metrics = compute_token_metrics(messages)
        assert metrics.input_tokens == 100
        assert metrics.api_call_count == 1


class TestCostComputation:
    def test_sonnet_cost(self) -> None:
        # Sonnet: input=3/1M, output=15/1M
        messages = [_assistant(
            model="claude-sonnet-4-20250514",
            input_tokens=1_000_000,
            output_tokens=100_000,
        )]
        metrics = compute_token_metrics(messages)
        # 1M * 3/1M + 100K * 15/1M = 3.0 + 1.5 = 4.5
        assert abs(metrics.total_cost - 4.5) < 0.001

    def test_opus_cost(self) -> None:
        # Opus 4.6 (as of 2026-04): input=5/1M, output=25/1M
        messages = [_assistant(
            model="claude-opus-4-6",
            input_tokens=1_000_000,
            output_tokens=100_000,
        )]
        metrics = compute_token_metrics(messages)
        # 1M * 5/1M + 100K * 25/1M = 5.0 + 2.5 = 7.5
        assert abs(metrics.total_cost - 7.5) < 0.001

    def test_opus_4_7_cost(self) -> None:
        # Issue #75: opus-4-7 has known pricing and produces non-zero cost.
        messages = [_assistant(
            model="claude-opus-4-7",
            input_tokens=1_000_000,
            output_tokens=100_000,
        )]
        metrics = compute_token_metrics(messages)
        # 1M * 5/1M + 100K * 25/1M = 5.0 + 2.5 = 7.5
        assert abs(metrics.total_cost - 7.5) < 0.001
        assert "claude-opus-4-7" in _model_names(metrics)

    def test_unknown_model_zero_cost(self) -> None:
        messages = [_assistant(model="unknown-model", input_tokens=1000, output_tokens=500)]
        metrics = compute_token_metrics(messages)
        assert metrics.total_cost == 0.0
        assert metrics.input_tokens == 1000

    def test_mixed_model_cost(self) -> None:
        messages = [
            _assistant(model="claude-sonnet-4-20250514", input_tokens=1_000_000, output_tokens=0),
            _assistant(model="claude-opus-4-6", input_tokens=1_000_000, output_tokens=0),
        ]
        metrics = compute_token_metrics(messages)
        # Sonnet: 1M * 3/1M = 3.0, Opus 4.6: 1M * 5/1M = 5.0
        assert abs(metrics.total_cost - 8.0) < 0.001
        assert len(metrics.by_model) == 2


class TestSyntheticFiltering:
    def test_synthetic_model_skipped(self, caplog: pytest.LogCaptureFixture) -> None:
        # Issue #75: <synthetic> is a Claude Code sentinel, not a real model.
        # It must be filtered before pricing lookup -- no counter bump,
        # no pricing warning, no entry in by_model.
        messages = [_assistant(
            model="<synthetic>",
            input_tokens=1000,
            output_tokens=500,
        )]
        with caplog.at_level(logging.DEBUG, logger="agentfluent.analytics.pricing"):
            metrics = compute_token_metrics(messages)
        assert metrics.input_tokens == 0
        assert metrics.output_tokens == 0
        assert metrics.total_cost == 0.0
        assert metrics.api_call_count == 0
        assert "<synthetic>" not in _model_names(metrics)
        # No "Unknown model" log entry at any level.
        assert not any(
            "<synthetic>" in r.getMessage() for r in caplog.records
        )

    def test_synthetic_mixed_with_real_model(self) -> None:
        # A real message alongside a synthetic one: real one is counted,
        # synthetic is silently dropped.
        messages = [
            _assistant(model="<synthetic>", input_tokens=999, output_tokens=999),
            _assistant(model="claude-sonnet-4-6", input_tokens=100, output_tokens=50),
        ]
        metrics = compute_token_metrics(messages)
        assert metrics.input_tokens == 100
        assert metrics.output_tokens == 50
        assert metrics.api_call_count == 1
        assert _model_names(metrics) == {"claude-sonnet-4-6"}


class TestPerModelBreakdown:
    def test_single_model(self) -> None:
        messages = [
            _assistant(model="claude-sonnet-4-20250514", input_tokens=100, output_tokens=50),
            _assistant(model="claude-sonnet-4-20250514", input_tokens=200, output_tokens=100),
        ]
        metrics = compute_token_metrics(messages)
        assert len(metrics.by_model) == 1
        breakdown = _by_model(metrics, "claude-sonnet-4-20250514")
        assert breakdown.input_tokens == 300
        assert breakdown.output_tokens == 150

    def test_multiple_models(self) -> None:
        messages = [
            _assistant(model="claude-sonnet-4-20250514", input_tokens=100, output_tokens=50),
            _assistant(model="claude-opus-4-6", input_tokens=200, output_tokens=100),
        ]
        metrics = compute_token_metrics(messages)
        assert len(metrics.by_model) == 2
        assert _by_model(metrics, "claude-sonnet-4-20250514").input_tokens == 100
        assert _by_model(metrics, "claude-opus-4-6").input_tokens == 200

    def test_model_breakdown_total_tokens(self) -> None:
        messages = [
            _assistant(input_tokens=100, output_tokens=50, cache_creation=200, cache_read=300),
        ]
        metrics = compute_token_metrics(messages)
        breakdown = _by_model(metrics, "claude-sonnet-4-20250514")
        assert breakdown.total_tokens == 650

    def test_missing_model_uses_unknown(self) -> None:
        msg = SessionMessage(
            type="assistant",
            model=None,
            usage=Usage(input_tokens=100, output_tokens=50),
        )
        metrics = compute_token_metrics([msg])
        assert "unknown" in _model_names(metrics)

    def test_parent_origin_set_on_breakdowns(self) -> None:
        # #227: every parent breakdown row carries origin="parent" so the
        # downstream JSON envelope is unambiguous even before subagent
        # contributions land.
        messages = [_assistant(input_tokens=100, output_tokens=50)]
        metrics = compute_token_metrics(messages)
        assert all(r.origin == "parent" for r in metrics.by_model)


class TestCacheEfficiency:
    def test_cache_efficiency_formula(self) -> None:
        # cache_read / (cache_read + input + cache_creation) * 100
        messages = [
            _assistant(input_tokens=100, output_tokens=50, cache_creation=200, cache_read=300),
        ]
        metrics = compute_token_metrics(messages)
        # 300 / (300 + 100 + 200) = 300/600 = 50%
        assert metrics.cache_efficiency == 50.0

    def test_zero_cache_efficiency(self) -> None:
        messages = [_assistant(input_tokens=100, output_tokens=50)]
        metrics = compute_token_metrics(messages)
        assert metrics.cache_efficiency == 0.0

    def test_high_cache_efficiency(self) -> None:
        messages = [
            _assistant(input_tokens=10, output_tokens=50, cache_creation=0, cache_read=990),
        ]
        metrics = compute_token_metrics(messages)
        # 990 / (990 + 10 + 0) = 99%
        assert metrics.cache_efficiency == 99.0

    def test_empty_session_zero_efficiency(self) -> None:
        metrics = compute_token_metrics([])
        assert metrics.cache_efficiency == 0.0


def _trace(
    model: str | None,
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_creation: int = 0,
    cache_read: int = 0,
) -> SubagentTrace:
    return SubagentTrace(
        agent_id="ag",
        agent_type="general-purpose",
        delegation_prompt="p",
        model=model,
        usage=Usage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_creation_input_tokens=cache_creation,
            cache_read_input_tokens=cache_read,
        ),
    )


class TestComputeSubagentTokenMetrics:
    """Coverage for the subagent-side aggregator that #227 wires in."""

    def test_returns_subagent_origin(self) -> None:
        rows = compute_subagent_token_metrics([
            _trace("claude-haiku-4-5-20251001", input_tokens=100, output_tokens=50),
        ])
        assert len(rows) == 1
        assert rows[0].model == "claude-haiku-4-5-20251001"
        assert rows[0].origin == "subagent"

    def test_skips_traces_with_no_model(self) -> None:
        rows = compute_subagent_token_metrics([
            _trace(None, input_tokens=999, output_tokens=999),
        ])
        # No model means no usable cost or breakdown row — skip.
        assert rows == []

    def test_aggregates_same_model_across_traces(self) -> None:
        rows = compute_subagent_token_metrics([
            _trace("claude-haiku-4-5-20251001", input_tokens=100, output_tokens=50),
            _trace("claude-haiku-4-5-20251001", input_tokens=200, output_tokens=100),
        ])
        assert len(rows) == 1
        assert rows[0].input_tokens == 300
        assert rows[0].output_tokens == 150


class TestFoldSubagentMetricsIn:
    """Combined comprehensiveness — top-level totals reflect parent + subagent."""

    def test_no_subagent_rows_returns_parent_unchanged(self) -> None:
        parent = compute_token_metrics([
            _assistant(input_tokens=100, output_tokens=50),
        ])
        result = fold_subagent_metrics_in(parent, [])
        # Parent was returned as-is (object identity preserved is fine).
        assert result is parent

    def test_top_level_tokens_become_comprehensive(self) -> None:
        parent = compute_token_metrics([
            _assistant(
                model="claude-opus-4-7", input_tokens=100, output_tokens=50,
            ),
        ])
        subagent_rows = compute_subagent_token_metrics([
            _trace(
                "claude-haiku-4-5-20251001", input_tokens=200, output_tokens=100,
            ),
        ])
        result = fold_subagent_metrics_in(parent, subagent_rows)
        assert result.input_tokens == 300
        assert result.output_tokens == 150
        # Parent and subagent rows kept distinct.
        assert {(r.model, r.origin) for r in result.by_model} == {
            ("claude-opus-4-7", "parent"),
            ("claude-haiku-4-5-20251001", "subagent"),
        }

    def test_total_cost_includes_subagent(self) -> None:
        # Sonnet parent + Haiku subagent — both real models with pricing.
        parent = compute_token_metrics([
            _assistant(
                model="claude-sonnet-4-6", input_tokens=1_000_000, output_tokens=0,
            ),
        ])
        subagent_rows = compute_subagent_token_metrics([
            _trace(
                "claude-haiku-4-5-20251001",
                input_tokens=1_000_000, output_tokens=0,
            ),
        ])
        result = fold_subagent_metrics_in(parent, subagent_rows)
        # Comprehensive = parent_cost + subagent_cost (both > 0).
        assert result.total_cost > parent.total_cost
