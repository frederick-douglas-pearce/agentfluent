"""Cost-coverage comparison for ``diff`` (#713): loaders, per-side and combined status."""

from __future__ import annotations

from typing import Any

import pytest

from agentfluent.analytics.cost_coverage import (
    CostCoverage,
    DiagnosticsCoverage,
    InvocationCoverage,
    PricingCoverage,
    has_cost_gap,
)
from agentfluent.diff.compute import compute_diff
from agentfluent.diff.cost_coverage import (
    cost_coverage_status,
    load_cost_coverage,
    load_unparseable_trace_files,
    side_coverage_status,
)


def _coverage(
    *,
    unpriced: int = 0,
    total: int = 4,
    linked: int = 4,
    spend: int = 4,
    priced: int = 4,
    diagnostics: DiagnosticsCoverage | None = None,
) -> CostCoverage:
    return CostCoverage(
        pricing=PricingCoverage(
            tokens=1000,
            unpriced_tokens=unpriced,
            unpriced_models={"claude-new-1": unpriced} if unpriced else {},
        ),
        invocations=InvocationCoverage(
            total=total, linked=linked, spend=spend, priced=priced,
        ),
        diagnostics=diagnostics,
    )


def _envelope(
    *, cost_coverage: Any = ..., trace_coverage: Any = ...,
) -> dict[str, Any]:
    envelope: dict[str, Any] = {
        "token_metrics": {"by_model": {}},
        "agent_metrics": {"by_agent_type": {}},
    }
    if cost_coverage is not ...:
        envelope["cost_coverage"] = cost_coverage
    if trace_coverage is not ...:
        envelope["trace_coverage"] = trace_coverage
    return envelope


_FULL_BLOCK = _coverage().model_dump(mode="json")
_TRACES_CLEAN = {"unparseable": {"files": 0}}


class TestHasCostGap:
    def test_full(self) -> None:
        assert has_cost_gap(_coverage(), 0) is False

    def test_unpriced_tokens(self) -> None:
        assert has_cost_gap(_coverage(unpriced=10), 0) is True

    def test_unlinked_invocation(self) -> None:
        assert has_cost_gap(_coverage(linked=3, spend=3, priced=3), 0) is True

    def test_unpriced_spend(self) -> None:
        assert has_cost_gap(_coverage(priced=3), 0) is True

    def test_linked_trace_without_model_response_is_not_a_gap(self) -> None:
        assert has_cost_gap(_coverage(spend=3, priced=3), 0) is False

    def test_unparseable_trace_file(self) -> None:
        assert has_cost_gap(_coverage(), 1) is True

    def test_diagnostics_gap_is_out_of_scope(self) -> None:
        coverage = _coverage(
            diagnostics=DiagnosticsCoverage(
                savings_figures=2, partial_figures=1, unpriced_figures=1,
            ),
        )
        assert has_cost_gap(coverage, 0) is False


class TestLoadCostCoverage:
    def test_full_block_round_trips(self) -> None:
        loaded = load_cost_coverage(_envelope(cost_coverage=_FULL_BLOCK))
        assert loaded == _coverage()

    @pytest.mark.parametrize(
        "raw",
        [
            ...,  # absent: envelope from before v0.12.0
            None,
            {},
            {"invocations": {}},  # missing pricing
            {"pricing": {}},  # missing invocations
            {"pricing": [], "invocations": {}},
            {"pricing": {"tokens": "many"}, "invocations": {}},
            "full",
        ],
    )
    def test_unreadable_block_is_none_never_full(self, raw: Any) -> None:
        assert load_cost_coverage(_envelope(cost_coverage=raw)) is None


class TestLoadUnparseableTraceFiles:
    def test_reads_count(self) -> None:
        envelope = _envelope(trace_coverage={"unparseable": {"files": 2}})
        assert load_unparseable_trace_files(envelope) == 2

    @pytest.mark.parametrize(
        "raw",
        [
            ...,
            None,
            {},
            {"unparseable": None},
            {"unparseable": {}},
            {"unparseable": {"files": "2"}},
            {"unparseable": {"files": True}},
            {"unparseable": {"files": -1}},
        ],
    )
    def test_unreadable_count_is_none_never_zero(self, raw: Any) -> None:
        assert load_unparseable_trace_files(_envelope(trace_coverage=raw)) is None


class TestSideStatus:
    def test_full(self) -> None:
        assert side_coverage_status(_coverage(), 0) == "full"

    def test_incomplete(self) -> None:
        assert side_coverage_status(_coverage(unpriced=5), 0) == "incomplete"

    def test_no_block_is_unknown(self) -> None:
        assert side_coverage_status(None, 0) == "unknown"

    def test_no_trace_count_is_unknown_even_with_a_full_block(self) -> None:
        assert side_coverage_status(_coverage(), None) == "unknown"


class TestCombinedStatus:
    @pytest.mark.parametrize(
        ("baseline", "current", "expected"),
        [
            ("full", "full", "full"),
            ("full", "incomplete", "incomplete"),
            ("incomplete", "full", "incomplete"),
            ("incomplete", "incomplete", "incomplete"),
            ("unknown", "full", "unknown"),
            ("full", "unknown", "unknown"),
            ("unknown", "incomplete", "unknown"),
            ("incomplete", "unknown", "unknown"),
            ("unknown", "unknown", "unknown"),
        ],
    )
    def test_matrix(self, baseline: Any, current: Any, expected: str) -> None:
        assert cost_coverage_status(baseline, current) == expected


class TestComputeDiff:
    """AC3's four cases through ``compute_diff`` on envelope dicts."""

    def _diff(self, baseline: dict[str, Any], current: dict[str, Any]) -> Any:
        return compute_diff(baseline, current)

    def test_both_full(self) -> None:
        full = _envelope(cost_coverage=_FULL_BLOCK, trace_coverage=_TRACES_CLEAN)
        result = self._diff(full, full)
        assert result.cost_coverage_status == "full"
        assert result.baseline_cost_coverage == _coverage()
        assert result.current_unparseable_trace_files == 0

    def test_one_side_unpriced(self) -> None:
        full = _envelope(cost_coverage=_FULL_BLOCK, trace_coverage=_TRACES_CLEAN)
        unpriced = _envelope(
            cost_coverage=_coverage(unpriced=40).model_dump(mode="json"),
            trace_coverage=_TRACES_CLEAN,
        )
        result = self._diff(full, unpriced)
        assert result.baseline_cost_coverage_status == "full"
        assert result.current_cost_coverage_status == "incomplete"
        assert result.cost_coverage_status == "incomplete"

    def test_one_side_legacy(self) -> None:
        legacy = _envelope()
        full = _envelope(cost_coverage=_FULL_BLOCK, trace_coverage=_TRACES_CLEAN)
        result = self._diff(legacy, full)
        assert result.baseline_cost_coverage is None
        assert result.baseline_unparseable_trace_files is None
        assert result.cost_coverage_status == "unknown"

    def test_both_partial(self) -> None:
        partial = _envelope(
            cost_coverage=_coverage(linked=3, spend=3, priced=3).model_dump(mode="json"),
            trace_coverage={"unparseable": {"files": 1}},
        )
        result = self._diff(partial, partial)
        assert result.baseline_cost_coverage_status == "incomplete"
        assert result.current_cost_coverage_status == "incomplete"
        assert result.cost_coverage_status == "incomplete"

    def test_coverage_never_sets_regression(self) -> None:
        result = compute_diff(_envelope(), _envelope(), fail_on=None)
        assert result.cost_coverage_status == "unknown"
        assert result.regression_detected is False

    def test_json_carries_status_fields(self) -> None:
        payload = self._diff(_envelope(), _envelope()).model_dump(mode="json")
        assert payload["cost_coverage_status"] == "unknown"
        assert payload["baseline_cost_coverage_status"] == "unknown"
        assert payload["current_cost_coverage"] is None
