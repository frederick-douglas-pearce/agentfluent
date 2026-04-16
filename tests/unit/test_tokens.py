"""Tests for token and cost analytics."""

from agentfluent.analytics.tokens import TokenMetrics, compute_token_metrics
from agentfluent.core.session import ContentBlock, SessionMessage, Usage


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
        assert metrics.by_model == {}

    def test_skips_non_assistant(self) -> None:
        messages = [
            SessionMessage(type="user"),
            _assistant(input_tokens=100, output_tokens=50),
            SessionMessage(type="tool_result", tool_use_id="t1"),
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
        # Opus: input=15/1M, output=75/1M
        messages = [_assistant(
            model="claude-opus-4-6",
            input_tokens=1_000_000,
            output_tokens=100_000,
        )]
        metrics = compute_token_metrics(messages)
        # 1M * 15/1M + 100K * 75/1M = 15.0 + 7.5 = 22.5
        assert abs(metrics.total_cost - 22.5) < 0.001

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
        # Sonnet: 1M * 3/1M = 3.0, Opus: 1M * 15/1M = 15.0
        assert abs(metrics.total_cost - 18.0) < 0.001
        assert len(metrics.by_model) == 2


class TestPerModelBreakdown:
    def test_single_model(self) -> None:
        messages = [
            _assistant(model="claude-sonnet-4-20250514", input_tokens=100, output_tokens=50),
            _assistant(model="claude-sonnet-4-20250514", input_tokens=200, output_tokens=100),
        ]
        metrics = compute_token_metrics(messages)
        assert len(metrics.by_model) == 1
        breakdown = metrics.by_model["claude-sonnet-4-20250514"]
        assert breakdown.input_tokens == 300
        assert breakdown.output_tokens == 150

    def test_multiple_models(self) -> None:
        messages = [
            _assistant(model="claude-sonnet-4-20250514", input_tokens=100, output_tokens=50),
            _assistant(model="claude-opus-4-6", input_tokens=200, output_tokens=100),
        ]
        metrics = compute_token_metrics(messages)
        assert len(metrics.by_model) == 2
        assert metrics.by_model["claude-sonnet-4-20250514"].input_tokens == 100
        assert metrics.by_model["claude-opus-4-6"].input_tokens == 200

    def test_model_breakdown_total_tokens(self) -> None:
        messages = [
            _assistant(input_tokens=100, output_tokens=50, cache_creation=200, cache_read=300),
        ]
        metrics = compute_token_metrics(messages)
        breakdown = metrics.by_model["claude-sonnet-4-20250514"]
        assert breakdown.total_tokens == 650

    def test_missing_model_uses_unknown(self) -> None:
        msg = SessionMessage(
            type="assistant",
            model=None,
            usage=Usage(input_tokens=100, output_tokens=50),
        )
        metrics = compute_token_metrics([msg])
        assert "unknown" in metrics.by_model


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
