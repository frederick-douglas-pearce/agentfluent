"""Tests for recommendation aggregation.

Covers the grouping key, count tracking, metric-range synthesis (for
scalar signal types only), severity-based sort order, and the
round-trip through ``DiagnosticsResult`` so the raw recommendations
remain available for verbose drill-down.
"""

from agentfluent.config.models import Severity
from agentfluent.diagnostics.aggregation import (
    SIGNAL_AXIS_MAP,
    aggregate_recommendations,
)
from agentfluent.diagnostics.models import (
    AggregatedRecommendation,
    Axis,
    DiagnosticRecommendation,
    DiagnosticSignal,
    SignalType,
)


class TestSignalAxisMap:
    """Drift-prevention contract on ``SIGNAL_AXIS_MAP``.

    Per architect review on #269, every ``SignalType`` must map to
    exactly one ``Axis``. A future contributor adding a ``SignalType``
    without a corresponding map entry will fail this test in CI rather
    than producing silently dropped axis attribution downstream.
    """

    def test_map_covers_every_signal_type(self) -> None:
        assert set(SIGNAL_AXIS_MAP.keys()) == set(SignalType)

    def test_every_value_is_an_axis(self) -> None:
        assert all(isinstance(v, Axis) for v in SIGNAL_AXIS_MAP.values())


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
        assert agg.priority_score == 0.0


def _model_mismatch_pair(
    agent_type: str,
    estimated_savings_usd: float,
    invocation_count: int = 5,
) -> tuple[DiagnosticSignal, DiagnosticRecommendation]:
    signal = DiagnosticSignal(
        signal_type=SignalType.MODEL_MISMATCH,
        severity=Severity.WARNING,
        agent_type=agent_type,
        message=f"Overspec'd model: {agent_type} runs on opus.",
        detail={
            "mismatch_type": "overspec",
            "current_model": "claude-opus-4-7",
            "recommended_model": "claude-haiku-4-5",
            "complexity_tier": "simple",
            "invocation_count": invocation_count,
            "estimated_savings_usd": estimated_savings_usd,
        },
    )
    rec = DiagnosticRecommendation(
        target="model",
        severity=Severity.WARNING,
        message=f"Switch '{agent_type}' to Haiku — saves ~${estimated_savings_usd:.2f}.",
        observation=f"'{agent_type}' is overspec'd on Opus.",
        reason="Simple workload doesn't justify Opus pricing.",
        action="Update the agent's frontmatter to model: claude-haiku-4-5.",
        agent_type=agent_type,
        signal_types=[SignalType.MODEL_MISMATCH],
    )
    return signal, rec


def _stuck_pattern_pair(
    agent_type: str,
    severity: Severity = Severity.WARNING,
) -> tuple[DiagnosticSignal, DiagnosticRecommendation]:
    signal = DiagnosticSignal(
        signal_type=SignalType.STUCK_PATTERN,
        severity=severity,
        agent_type=agent_type,
        message=f"Subagent '{agent_type}' got stuck after 5 retries.",
        detail={"stuck_count": 5, "tool_calls": []},
    )
    rec = DiagnosticRecommendation(
        target="prompt",
        severity=severity,
        message=f"Add fallback guidance to '{agent_type}'.",
        observation="Stuck after 5 retries.",
        reason="No recovery guidance.",
        action="Add explicit fallback paths.",
        agent_type=agent_type,
        signal_types=[SignalType.STUCK_PATTERN],
    )
    return signal, rec


class TestPriorityScore:
    """Priority scoring (#172). Severity dominates; trace evidence and
    cost impact serve as tiebreakers within a severity tier."""

    def test_severity_dominates_count(self) -> None:
        # 1 critical vs 100 warnings: critical wins despite the count.
        critical_pair = _retry_loop_pair("pm", "Bash", 5, severity=Severity.CRITICAL)
        warning_pairs = [_token_outlier_pair(f"a{i}", 2.0) for i in range(100)]
        # Use distinct agent_types so the warnings stay across rows;
        # otherwise they'd aggregate into one row with count=100.
        aggregated = aggregate_recommendations([critical_pair, *warning_pairs])
        # First row by priority desc must be the critical, regardless
        # of its count of 1.
        assert aggregated[0].severity == Severity.CRITICAL

    def test_trace_evidence_outranks_metadata_at_same_severity(self) -> None:
        # Two warnings: one carries trace-level STUCK_PATTERN, the
        # other is a metadata-only TOKEN_OUTLIER. Same count (1).
        # Trace one wins via the W_TRACE boost.
        trace_pair = _stuck_pattern_pair("pm")
        metadata_pair = _token_outlier_pair("explore", 2.0)
        aggregated = aggregate_recommendations([trace_pair, metadata_pair])
        assert aggregated[0].agent_type == "pm"
        assert aggregated[0].priority_score > aggregated[1].priority_score

    def test_higher_savings_outranks_lower_within_same_severity(self) -> None:
        # Two MODEL_MISMATCH warnings; the larger-savings one ranks
        # higher (cost impact is a same-severity tiebreaker).
        big_pair = _model_mismatch_pair("explore", 50.0)
        small_pair = _model_mismatch_pair("pm", 5.0)
        aggregated = aggregate_recommendations([big_pair, small_pair])
        assert aggregated[0].agent_type == "explore"
        assert aggregated[0].priority_score > aggregated[1].priority_score

    def test_count_grows_score_via_log1p_not_linearly(self) -> None:
        # Count=10 should NOT score 10x count=1 — log1p damps.
        # We verify the scores are ordered correctly but the ratio
        # is bounded (log1p(10) / log1p(1) ≈ 3.46).
        single = _token_outlier_pair("a", 2.0)
        many = [_token_outlier_pair("b", 2.0) for _ in range(10)]
        aggregated = aggregate_recommendations([single, *many])
        single_row = next(a for a in aggregated if a.agent_type == "a")
        many_row = next(a for a in aggregated if a.agent_type == "b")
        assert many_row.count == 10
        assert single_row.count == 1
        # Bounded growth: many's score is less than 10× single's score.
        assert many_row.priority_score < 10 * single_row.priority_score
        # But strictly higher.
        assert many_row.priority_score > single_row.priority_score

    def test_aggregated_list_sorted_by_priority_desc(self) -> None:
        pairs = [
            _retry_loop_pair("pm", "Bash", 3, severity=Severity.CRITICAL),
            _token_outlier_pair("explore", 2.0),  # warning, no trace
            _stuck_pattern_pair("architect"),     # warning, trace
            _model_mismatch_pair("plan", 25.0),   # warning, savings
        ]
        aggregated = aggregate_recommendations(pairs)
        scores = [a.priority_score for a in aggregated]
        assert scores == sorted(scores, reverse=True)
        # Critical first.
        assert aggregated[0].severity == Severity.CRITICAL
