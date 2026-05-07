"""Unit tests for ``agentfluent.diff.compute``.

Exercises the pure compute layer with synthetic envelope dicts. The
fixtures intentionally include an empty diagnostics section, missing
fields, and signal-type list ordering inversions to lock down the
guarantees called out in architect review (#199): frozenset
normalization of the grouping key, JSON-roundtripped severity strings,
and additive forward-compat defaults.
"""

from __future__ import annotations

from typing import Any

import pytest

from agentfluent.config.models import Severity
from agentfluent.diff import compute_diff
from agentfluent.diff.compute import _rec_key
from agentfluent.diff.models import DiffResult


def _envelope(
    *,
    aggregated_recs: list[dict[str, Any]] | None = None,
    by_model: dict[str, dict[str, Any]] | list[dict[str, Any]] | None = None,
    by_agent: dict[str, dict[str, Any]] | None = None,
    total_cost: float = 0.0,
    cache_efficiency: float = 0.0,
    session_count: int = 1,
) -> dict[str, Any]:
    by_model_payload: dict[str, Any] | list[Any]
    if by_model is None:
        # Default to v2 list shape for new tests; legacy dict tests pass
        # explicitly.
        by_model_payload = []
    else:
        by_model_payload = by_model
    return {
        "session_count": session_count,
        "token_metrics": {
            "input_tokens": 100,
            "output_tokens": 200,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "total_cost": total_cost,
            "cache_efficiency": cache_efficiency,
            "by_model": by_model_payload,
        },
        "agent_metrics": {
            "by_agent_type": by_agent or {},
            "total_invocations": 0,
        },
        "diagnostics": {
            "aggregated_recommendations": aggregated_recs or [],
        } if aggregated_recs is not None else None,
    }


def _agg_rec(
    *,
    agent_type: str | None,
    target: str,
    signal_types: list[str],
    severity: str = "warning",
    count: int = 1,
    priority_score: float = 10.0,
    representative_message: str = "Sample finding.",
    is_builtin: bool = False,
) -> dict[str, Any]:
    return {
        "agent_type": agent_type,
        "target": target,
        "signal_types": signal_types,
        "severity": severity,
        "count": count,
        "priority_score": priority_score,
        "representative_message": representative_message,
        "is_builtin": is_builtin,
    }


class TestRecommendationDeltas:
    def test_new_recommendation_appears_in_current_only(self) -> None:
        baseline = _envelope(aggregated_recs=[])
        current = _envelope(aggregated_recs=[
            _agg_rec(agent_type="pm", target="prompt", signal_types=["retry_loop"]),
        ])

        result = compute_diff(baseline, current)

        assert result.new_count == 1
        assert result.resolved_count == 0
        assert result.persisting_count == 0
        new_rec = result.recommendations[0]
        assert new_rec.status == "new"
        assert new_rec.agent_type == "pm"
        assert new_rec.target == "prompt"
        assert new_rec.current_count == 1
        assert new_rec.baseline_count == 0
        assert new_rec.count_delta == 1

    def test_resolved_recommendation_only_in_baseline(self) -> None:
        baseline = _envelope(aggregated_recs=[
            _agg_rec(agent_type="pm", target="prompt", signal_types=["retry_loop"]),
        ])
        current = _envelope(aggregated_recs=[])

        result = compute_diff(baseline, current)

        assert result.resolved_count == 1
        assert result.recommendations[0].status == "resolved"
        assert result.recommendations[0].count_delta == -1

    def test_persisting_recommendation_in_both_with_count_delta(self) -> None:
        baseline = _envelope(aggregated_recs=[
            _agg_rec(
                agent_type="pm", target="prompt", signal_types=["retry_loop"],
                count=10, priority_score=15.0,
            ),
        ])
        current = _envelope(aggregated_recs=[
            _agg_rec(
                agent_type="pm", target="prompt", signal_types=["retry_loop"],
                count=4, priority_score=8.0,
            ),
        ])

        result = compute_diff(baseline, current)

        assert result.persisting_count == 1
        rec = result.recommendations[0]
        assert rec.status == "persisting"
        assert rec.count_delta == -6
        assert rec.priority_score_delta == -7.0

    def test_signal_type_order_does_not_split_keys(self) -> None:
        """Architect-flagged: frozenset normalization, not list order."""
        baseline = _envelope(aggregated_recs=[
            _agg_rec(
                agent_type="pm",
                target="prompt",
                signal_types=["retry_loop", "tool_error_sequence"],
            ),
        ])
        current = _envelope(aggregated_recs=[
            _agg_rec(
                agent_type="pm",
                target="prompt",
                signal_types=["tool_error_sequence", "retry_loop"],
            ),
        ])

        result = compute_diff(baseline, current)

        # Same finding in different signal_types order MUST be persisting,
        # not resolved+new.
        assert result.new_count == 0
        assert result.resolved_count == 0
        assert result.persisting_count == 1

    def test_none_agent_type_keeps_cross_cutting_findings_separate(self) -> None:
        baseline = _envelope(aggregated_recs=[
            _agg_rec(
                agent_type=None, target="mcp", signal_types=["mcp_unused_server"],
            ),
        ])
        current = _envelope(aggregated_recs=[
            _agg_rec(
                agent_type=None, target="mcp", signal_types=["mcp_unused_server"],
            ),
        ])

        result = compute_diff(baseline, current)

        assert result.persisting_count == 1
        assert result.recommendations[0].agent_type is None

    def test_rec_key_matches_aggregation_key_shape(self) -> None:
        """Architect-flagged: the diff's join key must be byte-compatible
        with ``diagnostics.aggregation._aggregation_key`` so a future
        refactor that uses the latter directly stays sound.
        """
        rec = _agg_rec(
            agent_type="pm", target="prompt",
            signal_types=["retry_loop", "tool_error_sequence"],
        )
        key = _rec_key(rec)
        assert isinstance(key[2], frozenset)
        assert key[0] == "pm"
        assert key[1] == "prompt"


class TestRegressionDetection:
    def test_no_regression_when_fail_on_disabled(self) -> None:
        current = _envelope(aggregated_recs=[
            _agg_rec(
                agent_type="pm", target="prompt", signal_types=["retry_loop"],
                severity="critical",
            ),
        ])
        result = compute_diff(_envelope(aggregated_recs=[]), current, fail_on=None)
        assert result.regression_detected is False

    def test_regression_when_new_meets_threshold(self) -> None:
        current = _envelope(aggregated_recs=[
            _agg_rec(
                agent_type="pm", target="prompt", signal_types=["retry_loop"],
                severity="warning",
            ),
        ])
        result = compute_diff(
            _envelope(aggregated_recs=[]), current, fail_on=Severity.WARNING,
        )
        assert result.regression_detected is True

    def test_no_regression_when_new_below_threshold(self) -> None:
        current = _envelope(aggregated_recs=[
            _agg_rec(
                agent_type="pm", target="prompt", signal_types=["retry_loop"],
                severity="info",
            ),
        ])
        result = compute_diff(
            _envelope(aggregated_recs=[]), current, fail_on=Severity.WARNING,
        )
        assert result.regression_detected is False

    def test_persisting_priority_increase_does_not_trigger_regression(self) -> None:
        """v0.5: priority_score regressions on persisting recs are surfaced
        but do NOT fail the diff (PRD-deferred to v0.6)."""
        baseline = _envelope(aggregated_recs=[
            _agg_rec(
                agent_type="pm", target="prompt", signal_types=["retry_loop"],
                priority_score=5.0, severity="critical",
            ),
        ])
        current = _envelope(aggregated_recs=[
            _agg_rec(
                agent_type="pm", target="prompt", signal_types=["retry_loop"],
                priority_score=99.0, severity="critical",
            ),
        ])

        result = compute_diff(baseline, current, fail_on=Severity.CRITICAL)

        assert result.regression_detected is False
        rec = result.recommendations[0]
        assert rec.priority_score_delta == 94.0

    def test_resolved_recommendation_does_not_trigger_regression(self) -> None:
        baseline = _envelope(aggregated_recs=[
            _agg_rec(
                agent_type="pm", target="prompt", signal_types=["retry_loop"],
                severity="critical",
            ),
        ])
        current = _envelope(aggregated_recs=[])
        result = compute_diff(baseline, current, fail_on=Severity.CRITICAL)
        assert result.regression_detected is False


class TestTokenMetricsDelta:
    def test_total_cost_and_tokens_delta(self) -> None:
        baseline = _envelope(total_cost=1.50, cache_efficiency=42.0)
        current = _envelope(total_cost=2.10, cache_efficiency=55.0)
        result = compute_diff(baseline, current)
        assert result.token_metrics.total_cost_delta == pytest.approx(0.6)
        assert result.token_metrics.cache_efficiency_delta == pytest.approx(13.0)

    def test_per_model_delta_handles_added_and_removed_models(self) -> None:
        baseline = _envelope(by_model={
            "claude-opus-4-7": {
                "input_tokens": 100, "output_tokens": 200,
                "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
                "cost": 1.0,
            },
        })
        current = _envelope(by_model={
            "claude-sonnet-4-6": {
                "input_tokens": 50, "output_tokens": 100,
                "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
                "cost": 0.30,
            },
        })

        result = compute_diff(baseline, current)
        models = {row.model: row for row in result.token_metrics.by_model}
        assert models["claude-opus-4-7"].current_cost == 0.0
        assert models["claude-opus-4-7"].cost_delta == -1.0
        assert models["claude-sonnet-4-6"].baseline_cost == 0.0
        assert models["claude-sonnet-4-6"].cost_delta == 0.30

    def test_legacy_dict_envelope_diffs_against_v2_list_envelope(self) -> None:
        # Cross-version: pre-#227 saved JSON used a dict keyed by model
        # (no origin). The compat shim treats those rows as parent. A
        # v2 envelope with the same parent row should diff to zero.
        baseline_legacy = _envelope(by_model={
            "claude-opus-4-7": {
                "input_tokens": 100, "output_tokens": 200,
                "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
                "cost": 1.0,
            },
        })
        current_v2 = _envelope(by_model=[
            {
                "model": "claude-opus-4-7", "origin": "parent",
                "input_tokens": 100, "output_tokens": 200,
                "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
                "cost": 1.0,
            },
        ])

        result = compute_diff(baseline_legacy, current_v2)
        rows = result.token_metrics.by_model
        assert len(rows) == 1
        assert rows[0].model == "claude-opus-4-7"
        assert rows[0].origin == "parent"
        assert rows[0].cost_delta == 0.0
        assert rows[0].total_tokens_delta == 0

    def test_v2_list_envelope_distinguishes_parent_and_subagent(self) -> None:
        # Same model used by parent and subagent → two distinct rows in
        # the diff output, keyed by (model, origin).
        baseline = _envelope(by_model=[])
        current = _envelope(by_model=[
            {
                "model": "claude-opus-4-7", "origin": "parent",
                "input_tokens": 100, "output_tokens": 0,
                "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
                "cost": 0.5,
            },
            {
                "model": "claude-opus-4-7", "origin": "subagent",
                "input_tokens": 50, "output_tokens": 0,
                "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
                "cost": 0.25,
            },
        ])

        result = compute_diff(baseline, current)
        keyed = {(r.model, r.origin): r for r in result.token_metrics.by_model}
        assert keyed[("claude-opus-4-7", "parent")].cost_delta == pytest.approx(0.5)
        assert keyed[("claude-opus-4-7", "subagent")].cost_delta == pytest.approx(0.25)


class TestAgentTypeDelta:
    def test_per_agent_invocation_count_delta(self) -> None:
        baseline = _envelope(by_agent={
            "general-purpose": {
                "agent_type": "general-purpose", "is_builtin": True,
                "invocation_count": 10, "total_tokens": 5000,
                "estimated_total_cost_usd": 0.50,
            },
        })
        current = _envelope(by_agent={
            "general-purpose": {
                "agent_type": "general-purpose", "is_builtin": True,
                "invocation_count": 4, "total_tokens": 2000,
                "estimated_total_cost_usd": 0.20,
            },
        })

        result = compute_diff(baseline, current)
        delta = next(d for d in result.by_agent_type if d.agent_type == "general-purpose")
        assert delta.invocation_count_delta == -6
        assert delta.total_tokens_delta == -3000
        assert delta.estimated_cost_delta_usd == -0.30


class TestForwardCompat:
    def test_missing_diagnostics_section_yields_empty_recs(self) -> None:
        baseline: dict[str, Any] = {
            "session_count": 1,
            "token_metrics": {
                "input_tokens": 0, "output_tokens": 0,
                "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
                "total_cost": 0.0, "cache_efficiency": 0.0, "by_model": {},
            },
            "agent_metrics": {"by_agent_type": {}, "total_invocations": 0},
            "diagnostics": None,
        }
        current = baseline
        result = compute_diff(baseline, current)
        assert result.new_count == 0
        assert result.resolved_count == 0
        assert result.persisting_count == 0

    def test_missing_priority_score_defaults_to_zero(self) -> None:
        rec = {
            "agent_type": "pm",
            "target": "prompt",
            "signal_types": ["retry_loop"],
            "severity": "warning",
            "count": 1,
            "representative_message": "x",
        }
        baseline = _envelope(aggregated_recs=[rec])
        current = _envelope(aggregated_recs=[rec])
        result = compute_diff(baseline, current)
        assert result.recommendations[0].priority_score_delta == 0.0

    def test_window_field_on_one_side_does_not_break_diff(self) -> None:
        """``window`` (#298) is an additive optional envelope field;
        ``compute_diff`` ignores it. Pre-#298 baselines without ``window``
        must still diff cleanly against current envelopes that include it.
        """
        baseline = _envelope(aggregated_recs=[
            _agg_rec(agent_type="pm", target="prompt", signal_types=["retry_loop"]),
        ])
        current = _envelope(aggregated_recs=[
            _agg_rec(agent_type="pm", target="prompt", signal_types=["retry_loop"]),
        ])
        # Simulate a current envelope produced after #298 landed: the
        # caller adds a window block; the loader and compute layer must
        # not care.
        current["window"] = {
            "since": "2026-04-01T00:00:00+00:00",
            "until": "2026-05-01T00:00:00+00:00",
            "session_count_before_filter": 5,
            "session_count_after_filter": 1,
        }

        result = compute_diff(baseline, current)

        assert result.persisting_count == 1
        assert result.new_count == 0
        assert result.resolved_count == 0


class TestDiffResultSerialization:
    def test_model_dump_round_trips(self) -> None:
        baseline = _envelope(aggregated_recs=[
            _agg_rec(agent_type="pm", target="prompt", signal_types=["retry_loop"]),
        ])
        current = _envelope(aggregated_recs=[])
        result = compute_diff(baseline, current, fail_on=Severity.WARNING)

        dumped = result.model_dump(mode="json")
        # Re-validate ensures every field is JSON-serializable.
        round_tripped = DiffResult.model_validate(dumped)
        assert round_tripped.resolved_count == 1
        assert round_tripped.fail_on == Severity.WARNING
