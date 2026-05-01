"""Tests for recommendation aggregation.

Covers the grouping key, count tracking, metric-range synthesis (for
scalar signal types only), severity-based sort order, and the
round-trip through ``DiagnosticsResult`` so the raw recommendations
remain available for verbose drill-down.
"""

from agentfluent.config.models import Severity
from agentfluent.diagnostics.aggregation import aggregate_recommendations
from agentfluent.diagnostics.models import (
    AggregatedRecommendation,
    DiagnosticRecommendation,
    DiagnosticSignal,
    SignalType,
)


def _token_outlier_pair(
    agent_type: str,
    excess_iqrs: float,
    q3_value: float = 5064.0,
    iqr_value: float = 1000.0,
    target: str = "prompt",
) -> tuple[DiagnosticSignal, DiagnosticRecommendation]:
    actual = q3_value + excess_iqrs * iqr_value
    signal = DiagnosticSignal(
        signal_type=SignalType.TOKEN_OUTLIER,
        severity=Severity.WARNING,
        agent_type=agent_type,
        message=f"Agent '{agent_type}' has high token usage ({excess_iqrs:.1f}×IQR).",
        detail={
            "excess_iqrs": excess_iqrs,
            "actual_value": actual,
            "median_value": q3_value - iqr_value / 2,
            "q3_value": q3_value,
            "iqr_value": iqr_value,
            "p95_value": actual,
            "threshold_value": q3_value + 1.5 * iqr_value,
        },
    )
    rec = DiagnosticRecommendation(
        target=target,
        severity=Severity.WARNING,
        message=(
            f"Agent '{agent_type}' has {actual:,.0f} tokens/tool_use, "
            f"{excess_iqrs:.1f}×IQR above Q3 of {q3_value:,.0f}."
        ),
        observation=(
            f"Agent '{agent_type}' uses {actual:,.0f} tokens per call."
        ),
        reason="High token usage suggests broad exploration.",
        action="Add more specific instructions to the agent's prompt.",
        agent_type=agent_type,
        signal_types=[SignalType.TOKEN_OUTLIER],
    )
    return signal, rec


def _retry_loop_pair(
    agent_type: str,
    tool_name: str,
    attempts: int,
    severity: Severity = Severity.WARNING,
) -> tuple[DiagnosticSignal, DiagnosticRecommendation]:
    signal = DiagnosticSignal(
        signal_type=SignalType.RETRY_LOOP,
        severity=severity,
        agent_type=agent_type,
        message=(
            f"Subagent '{agent_type}' retried tool '{tool_name}' {attempts} times."
        ),
        detail={"tool_name": tool_name, "attempts": attempts},
    )
    rec = DiagnosticRecommendation(
        target="prompt",
        severity=severity,
        message=(
            f"Subagent '{agent_type}' retried tool '{tool_name}' {attempts} times. "
            "Add explicit retry / fallback guidance."
        ),
        observation=(
            f"Subagent '{agent_type}' retried '{tool_name}' {attempts} times."
        ),
        reason="Repeated retries indicate missing recovery guidance.",
        action="Add fallback guidance to the agent's prompt body.",
        agent_type=agent_type,
        signal_types=[SignalType.RETRY_LOOP],
    )
    return signal, rec


class TestAggregationKey:
    def test_same_shape_collapses_to_one_row(self) -> None:
        pairs = [
            _token_outlier_pair("Explore", 4.9),
            _token_outlier_pair("Explore", 6.7),
            _token_outlier_pair("Explore", 8.0),
            _token_outlier_pair("Explore", 5.2),
        ]
        aggregated = aggregate_recommendations(pairs)
        assert len(aggregated) == 1
        assert aggregated[0].count == 4
        assert aggregated[0].agent_type == "Explore"
        assert aggregated[0].target == "prompt"

    def test_different_agents_stay_separate(self) -> None:
        pairs = [
            _token_outlier_pair("Explore", 4.9),
            _token_outlier_pair("general-purpose", 3.2),
        ]
        aggregated = aggregate_recommendations(pairs)
        assert len(aggregated) == 2
        by_agent = {a.agent_type: a for a in aggregated}
        assert by_agent["Explore"].count == 1
        assert by_agent["general-purpose"].count == 1

    def test_different_target_stays_separate(self) -> None:
        # Same agent + signal_type but target="tools" vs "prompt" (the
        # TokenOutlierRule fork); must aggregate as two distinct rows.
        pairs = [
            _token_outlier_pair("Explore", 4.9, target="prompt"),
            _token_outlier_pair("Explore", 5.2, target="prompt"),
            _token_outlier_pair("Explore", 7.0, target="tools"),
        ]
        aggregated = aggregate_recommendations(pairs)
        assert len(aggregated) == 2
        targets = {a.target: a.count for a in aggregated}
        assert targets == {"prompt": 2, "tools": 1}

    def test_different_signal_types_stay_separate(self) -> None:
        pairs = [
            _token_outlier_pair("pm", 2.4),
            _retry_loop_pair("pm", "Read", 3),
        ]
        aggregated = aggregate_recommendations(pairs)
        assert len(aggregated) == 2


class TestMetricRange:
    def test_scalar_signals_produce_range(self) -> None:
        pairs = [
            _token_outlier_pair("Explore", 4.9),
            _token_outlier_pair("Explore", 6.7),
            _token_outlier_pair("Explore", 8.0),
            _token_outlier_pair("Explore", 5.2),
        ]
        aggregated = aggregate_recommendations(pairs)
        assert aggregated[0].metric_range == "4.9×–8.0×IQR above Q3"

    def test_single_invocation_shows_point_not_range(self) -> None:
        pairs = [_token_outlier_pair("pm", 3.4)]
        aggregated = aggregate_recommendations(pairs)
        assert aggregated[0].metric_range == "3.4×IQR above Q3"

    def test_non_scalar_signal_has_no_range(self) -> None:
        pairs = [
            _retry_loop_pair("architect", "Read", 3),
            _retry_loop_pair("architect", "Read", 7),
        ]
        aggregated = aggregate_recommendations(pairs)
        assert aggregated[0].metric_range is None


class TestRepresentativeMessage:
    def test_single_invocation_preserves_original_message(self) -> None:
        _, rec = _token_outlier_pair("pm", 3.4)
        aggregated = aggregate_recommendations([(_, rec)])
        assert aggregated[0].representative_message == rec.message

    def test_multi_invocation_includes_signal_type_and_range(self) -> None:
        pairs = [
            _token_outlier_pair("Explore", 4.9),
            _token_outlier_pair("Explore", 6.7),
            _token_outlier_pair("Explore", 8.0),
        ]
        aggregated = aggregate_recommendations(pairs)
        msg = aggregated[0].representative_message
        assert msg.startswith(
            "token_outlier (4.9×–8.0×IQR above Q3):",
        )
        assert "Add more specific instructions" in msg
        # Count is in its own column — must not be duplicated in the prefix.
        assert not msg.startswith("3 ")

    def test_multi_invocation_non_scalar_names_signal_type(self) -> None:
        pairs = [
            _retry_loop_pair("architect", "Read", 3),
            _retry_loop_pair("architect", "Read", 7),
        ]
        aggregated = aggregate_recommendations(pairs)
        msg = aggregated[0].representative_message
        assert msg.startswith("retry_loop:")
        assert not msg.startswith("2 ")

    def test_same_agent_target_different_signals_distinguishable(self) -> None:
        # Anchor the #181 fix: when two aggregated rows share the same
        # (agent, target) but differ in signal type — common for built-in
        # agents where multiple signals route to the same concern template
        # — their representative messages must name the triggering signal
        # so users can tell the rows apart.
        pairs = [
            _token_outlier_pair("Explore", 4.9),
            _token_outlier_pair("Explore", 8.0),
            _retry_loop_pair("Explore", "Read", 3),
            _retry_loop_pair("Explore", "Read", 5),
        ]
        aggregated = aggregate_recommendations(pairs)
        messages = {a.representative_message for a in aggregated}
        assert any("token_outlier" in m for m in messages)
        assert any("retry_loop" in m for m in messages)

    def test_count_one_message_equals_contributing_zero_message(self) -> None:
        # #209 contract: when count == 1, representative_message and
        # contributing_recommendations[0].message carry identical text.
        # JSON consumers can rely on this when deciding which to read.
        pairs = [_token_outlier_pair("pm", 3.4)]
        aggregated = aggregate_recommendations(pairs)
        agg = aggregated[0]
        assert agg.count == 1
        assert agg.representative_message == agg.contributing_recommendations[0].message

    def test_count_gt_one_message_is_synthesized(self) -> None:
        # #209 contract: when count > 1, representative_message is
        # synthesized and may differ from contributing_recommendations[0].
        # Consumers needing the raw signal text must read contributing[0].
        pairs = [
            _token_outlier_pair("Explore", 4.9),
            _token_outlier_pair("Explore", 6.7),
        ]
        aggregated = aggregate_recommendations(pairs)
        agg = aggregated[0]
        assert agg.count == 2
        # Synthesized form names the signal type explicitly; raw
        # contributing message starts with the agent's observation.
        assert agg.representative_message.startswith("token_outlier")
        # Raw message from the rule, not the synthetic prefix.
        assert not agg.contributing_recommendations[0].message.startswith(
            "token_outlier",
        )


class TestSortOrder:
    def test_critical_before_warning(self) -> None:
        pairs = [
            _token_outlier_pair("Explore", 4.9),
            _retry_loop_pair("architect", "Read", 7, severity=Severity.CRITICAL),
        ]
        aggregated = aggregate_recommendations(pairs)
        assert aggregated[0].severity == Severity.CRITICAL
        assert aggregated[1].severity == Severity.WARNING

    def test_higher_count_sorts_first_within_severity(self) -> None:
        pairs = [
            _token_outlier_pair("loner", 3.0),
            _token_outlier_pair("crowd", 4.0),
            _token_outlier_pair("crowd", 5.0),
            _token_outlier_pair("crowd", 6.0),
        ]
        aggregated = aggregate_recommendations(pairs)
        assert aggregated[0].agent_type == "crowd"
        assert aggregated[0].count == 3
        assert aggregated[1].agent_type == "loner"


class TestEvidencePreservation:
    def test_contributing_recommendations_are_attached(self) -> None:
        pairs = [
            _token_outlier_pair("Explore", 4.9),
            _token_outlier_pair("Explore", 8.0),
        ]
        aggregated = aggregate_recommendations(pairs)
        agg = aggregated[0]
        assert len(agg.contributing_recommendations) == 2
        observations = {r.observation for r in agg.contributing_recommendations}
        assert len(observations) == 2

    def test_empty_input_yields_empty_output(self) -> None:
        assert aggregate_recommendations([]) == []


class TestBuiltinPropagation:
    def test_is_builtin_propagates_from_contributing_recommendations(self) -> None:
        sig, rec = _token_outlier_pair("explore", 4.9)
        rec = rec.model_copy(update={"is_builtin": True})
        aggregated = aggregate_recommendations([(sig, rec)])
        assert aggregated[0].is_builtin is True

    def test_custom_agents_aggregate_with_is_builtin_false(self) -> None:
        pairs = [
            _token_outlier_pair("pm", 2.4),
            _token_outlier_pair("pm", 3.1),
        ]
        aggregated = aggregate_recommendations(pairs)
        assert aggregated[0].is_builtin is False


class TestAggregationModel:
    def test_aggregated_recommendation_is_pydantic_serializable(self) -> None:
        pairs = [_token_outlier_pair("pm", 3.4)]
        aggregated = aggregate_recommendations(pairs)
        # Round-trip through JSON shape to catch any non-serializable fields.
        dumped = aggregated[0].model_dump(mode="json")
        assert dumped["agent_type"] == "pm"
        assert dumped["count"] == 1
        assert dumped["metric_range"] == "3.4×IQR above Q3"

    def test_model_instantiates_with_minimal_fields(self) -> None:
        agg = AggregatedRecommendation(
            agent_type="pm",
            target="prompt",
            severity=Severity.WARNING,
            representative_message="ok",
        )
        assert agg.count == 1
        assert agg.metric_range is None
