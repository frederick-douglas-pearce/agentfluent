"""Per-side ``unpriced_tokens`` on the diff's by-model rows and totals (#723).

``None`` means unknown, never 0: a legacy envelope (pre-v0.12.0 rows, or a v1
dict-shaped ``by_model``) cannot say whether its ``0.0`` cost was unpriced.
"""

from __future__ import annotations

from typing import Any

import pytest

from agentfluent.analytics.tokens import (
    row_unpriced_tokens,
    serialized_total_tokens,
    side_unpriced_tokens,
    sum_unpriced_tokens,
)
from agentfluent.diff import compute_diff
from agentfluent.diff.models import ModelTokenDelta, TokenMetricsDelta

_PRICED = "claude-opus-4-7"
_UNPRICED = "claude-future-9"


def _row(
    model: str, *, cost: float, unpriced: Any = None, origin: str = "parent",
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "model": model,
        "origin": origin,
        "input_tokens": 600,
        "output_tokens": 400,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "cost": cost,
    }
    if unpriced is not None:
        row["unpriced_tokens"] = unpriced
    return row


def _envelope(
    by_model: Any, *, input_tokens: int = 0, omit_by_model: bool = False,
) -> dict[str, Any]:
    token_metrics: dict[str, Any] = {
        "input_tokens": input_tokens,
        "output_tokens": 0,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "total_cost": 0.0,
        "cache_efficiency": 0.0,
        "by_model": by_model,
    }
    if omit_by_model:
        del token_metrics["by_model"]
    return {
        "session_count": 1,
        "token_metrics": token_metrics,
        "agent_metrics": {"by_agent_type": {}, "total_invocations": 0},
        "diagnostics": None,
    }


def _by_model(baseline: Any, current: Any) -> dict[tuple[str, str], ModelTokenDelta]:
    result = compute_diff(_envelope(baseline), _envelope(current))
    return {(r.model, r.origin): r for r in result.token_metrics.by_model}


class TestRowUnpricedTokens:
    def test_reads_a_count(self) -> None:
        assert row_unpriced_tokens({"unpriced_tokens": 40}) == 40

    def test_zero_is_a_measured_zero(self) -> None:
        assert row_unpriced_tokens({"unpriced_tokens": 0}) == 0

    def test_missing_key_is_unknown(self) -> None:
        assert row_unpriced_tokens({}) is None

    @pytest.mark.parametrize("value", [None, "40", -1, True, 4.5, -1.0, float("nan")])
    def test_malformed_value_is_unknown(self, value: Any) -> None:
        assert row_unpriced_tokens({"unpriced_tokens": value}) is None

    def test_whole_number_float_counts(self) -> None:
        count = row_unpriced_tokens({"unpriced_tokens": 10.0})
        assert count == 10
        assert isinstance(count, int)

    @pytest.mark.parametrize("row", ["m", 3, None, ["unpriced_tokens"]])
    def test_non_mapping_row_is_unknown(self, row: Any) -> None:
        assert row_unpriced_tokens(row) is None


class TestSumUnpricedTokens:
    def test_sums_when_every_row_carries_the_field(self) -> None:
        assert sum_unpriced_tokens([{"unpriced_tokens": 10}, {"unpriced_tokens": 5}]) == 15

    def test_any_row_without_the_field_makes_the_sum_unknown(self) -> None:
        assert sum_unpriced_tokens([{"unpriced_tokens": 10}, {}]) is None

    def test_a_null_field_makes_the_sum_unknown(self) -> None:
        assert sum_unpriced_tokens([{"unpriced_tokens": 10}, {"unpriced_tokens": None}]) is None

    def test_empty_is_zero(self) -> None:
        assert sum_unpriced_tokens([]) == 0

    def test_a_non_mapping_row_makes_the_sum_unknown(self) -> None:
        assert sum_unpriced_tokens([{"unpriced_tokens": 10}, "m"]) is None


class TestSerializedTotalTokens:
    def test_sums_the_four_components(self) -> None:
        block = {
            "input_tokens": 1,
            "output_tokens": 20,
            "cache_creation_input_tokens": 300,
            "cache_read_input_tokens": 4000,
            "cost": 9.0,
        }
        assert serialized_total_tokens(block) == 4321

    def test_missing_or_null_components_count_as_zero(self) -> None:
        assert serialized_total_tokens({"input_tokens": 5, "output_tokens": None}) == 5


class TestSideUnpricedTokens:
    def test_sums_the_raw_rows(self) -> None:
        rows = [{"unpriced_tokens": 3}, {"unpriced_tokens": 4}]
        assert side_unpriced_tokens({"by_model": rows}) == 7

    def test_missing_by_model_is_unknown(self) -> None:
        assert side_unpriced_tokens({"input_tokens": 5}) is None

    def test_null_by_model_is_unknown(self) -> None:
        assert side_unpriced_tokens({"by_model": None}) is None

    def test_v1_dict_shaped_by_model_is_unknown(self) -> None:
        assert side_unpriced_tokens({"by_model": {"m": {"unpriced_tokens": 0}}}) is None

    def test_empty_by_model_with_tokens_is_unknown(self) -> None:
        assert side_unpriced_tokens({"input_tokens": 5, "by_model": []}) is None

    def test_empty_by_model_without_tokens_is_a_measured_zero(self) -> None:
        assert side_unpriced_tokens({"input_tokens": 0, "by_model": []}) == 0


class TestDiffByModelUnpriced:
    def test_priced_row_is_a_measured_zero_on_both_sides(self) -> None:
        row = _by_model(
            [_row(_PRICED, cost=1.0, unpriced=0)], [_row(_PRICED, cost=2.0, unpriced=0)],
        )[(_PRICED, "parent")]
        assert (row.baseline_unpriced_tokens, row.current_unpriced_tokens) == (0, 0)
        assert row.cost_delta_measured

    def test_fully_unpriced_row_carries_its_count(self) -> None:
        row = _by_model(
            [_row(_UNPRICED, cost=0.0, unpriced=1000)], [_row(_UNPRICED, cost=0.0, unpriced=1000)],
        )[(_UNPRICED, "parent")]
        assert (row.baseline_unpriced_tokens, row.current_unpriced_tokens) == (1000, 1000)
        assert not row.cost_delta_measured

    def test_partially_unpriced_row_on_one_side(self) -> None:
        row = _by_model(
            [_row(_PRICED, cost=1.0, unpriced=400)], [_row(_PRICED, cost=2.0, unpriced=0)],
        )[(_PRICED, "parent")]
        assert (row.baseline_unpriced_tokens, row.current_unpriced_tokens) == (400, 0)
        assert not row.cost_delta_measured
        assert row.cost_delta == pytest.approx(1.0)  # the released JSON field stays a number

    def test_legacy_rows_are_unknown_never_zero(self) -> None:
        row = _by_model([_row(_PRICED, cost=0.0)], [_row(_PRICED, cost=2.0, unpriced=0)])[
            (_PRICED, "parent")
        ]
        assert row.baseline_unpriced_tokens is None
        assert row.current_unpriced_tokens == 0
        assert row.cost_delta_measured

    def test_model_absent_on_a_field_bearing_side_is_a_measured_zero(self) -> None:
        row = _by_model([], [_row(_UNPRICED, cost=0.0, unpriced=1000)])[(_UNPRICED, "parent")]
        assert row.baseline_unpriced_tokens == 0
        assert row.current_unpriced_tokens == 1000

    def test_model_absent_on_a_legacy_side_is_unknown(self) -> None:
        rows = _by_model(
            [_row(_PRICED, cost=1.0)],
            [_row(_PRICED, cost=1.0, unpriced=0), _row(_UNPRICED, cost=0.0, unpriced=1000)],
        )
        assert rows[(_UNPRICED, "parent")].baseline_unpriced_tokens is None

    def test_v1_dict_shaped_side_is_unknown(self) -> None:
        baseline = {_PRICED: {"input_tokens": 100, "cost": 1.0, "unpriced_tokens": 0}}
        rows = _by_model(baseline, [_row(_UNPRICED, cost=0.0, unpriced=1000)])
        assert rows[(_UNPRICED, "parent")].baseline_unpriced_tokens is None

    def test_null_field_in_a_hand_edited_envelope_is_unknown_not_a_crash(self) -> None:
        row = _by_model(
            [_row(_PRICED, cost=1.0, unpriced=0)],
            [{**_row(_PRICED, cost=1.0), "unpriced_tokens": None}],
        )[(_PRICED, "parent")]
        assert row.current_unpriced_tokens is None
        assert row.baseline_unpriced_tokens == 0

    def test_mixed_side_keeps_present_counts_and_makes_absent_models_unknown(self) -> None:
        rows = _by_model(
            [_row(_PRICED, cost=1.0, unpriced=400), _row("claude-sonnet-4-6", cost=1.0)],
            [_row(_PRICED, cost=1.0, unpriced=0), _row(_UNPRICED, cost=0.0, unpriced=1000)],
        )
        assert rows[(_PRICED, "parent")].baseline_unpriced_tokens == 400
        assert rows[("claude-sonnet-4-6", "parent")].baseline_unpriced_tokens is None
        assert rows[(_UNPRICED, "parent")].baseline_unpriced_tokens is None

    def test_null_count_on_a_multi_row_side_makes_absent_models_unknown(self) -> None:
        rows = _by_model(
            [
                _row(_PRICED, cost=1.0, unpriced=0),
                {**_row("claude-sonnet-4-6", cost=1.0), "unpriced_tokens": None},
            ],
            [_row(_PRICED, cost=1.0, unpriced=0), _row(_UNPRICED, cost=0.0, unpriced=1000)],
        )
        assert rows[(_PRICED, "parent")].baseline_unpriced_tokens == 0
        assert rows[(_UNPRICED, "parent")].baseline_unpriced_tokens is None

    def test_fields_reach_the_json_payload(self) -> None:
        result = compute_diff(
            _envelope([_row(_PRICED, cost=1.0)]),
            _envelope([_row(_UNPRICED, cost=0.0, unpriced=1000)]),
        )
        payload = result.model_dump(mode="json")["token_metrics"]["by_model"]
        unpriced = next(r for r in payload if r["model"] == _UNPRICED)
        assert unpriced["current_unpriced_tokens"] == 1000
        assert unpriced["baseline_unpriced_tokens"] is None
        assert "cost_delta_measured" not in unpriced


class TestCostDeltaMeasured:
    @pytest.mark.parametrize(
        ("baseline", "current", "measured"),
        [
            (0, 0, True),
            (None, None, True),
            (None, 0, True),
            (5, 0, False),
            (0, 5, False),
            (None, 5, False),
            (5, 5, False),
        ],
    )
    def test_truth_table(self, baseline: int | None, current: int | None, measured: bool) -> None:
        row = ModelTokenDelta(
            model=_PRICED, baseline_unpriced_tokens=baseline, current_unpriced_tokens=current,
        )
        assert row.cost_delta_measured is measured


class TestDiffTotalUnpriced:
    def _tm(self, baseline: dict[str, Any], current: dict[str, Any]) -> TokenMetricsDelta:
        return compute_diff(baseline, current).token_metrics

    def test_sums_each_side_from_the_raw_rows(self) -> None:
        tm = self._tm(
            _envelope([
                _row(_PRICED, cost=1.0, unpriced=0), _row(_UNPRICED, cost=0.0, unpriced=1000),
            ]),
            _envelope([_row(_PRICED, cost=1.0, unpriced=0)]),
        )
        assert (tm.baseline_unpriced_tokens, tm.current_unpriced_tokens) == (1000, 0)
        assert not tm.total_cost_delta_measured

    def test_model_less_row_still_counts_in_the_total(self) -> None:
        # The by-model delta drops a row with no model; the Total reads the raw
        # rows, as report does, so its unpriced tokens are not lost (#723).
        model_less = _row(_UNPRICED, cost=0.0, unpriced=10)
        del model_less["model"]
        tm = self._tm(
            _envelope([_row(_PRICED, cost=1.0, unpriced=0), model_less]),
            _envelope([_row(_PRICED, cost=1.0, unpriced=0)]),
        )
        assert [row.model for row in tm.by_model] == [_PRICED]
        assert tm.baseline_unpriced_tokens == 10
        assert not tm.total_cost_delta_measured

    def test_duplicate_rows_both_count_in_the_total(self) -> None:
        tm = self._tm(
            _envelope([_row(_PRICED, cost=1.0, unpriced=4), _row(_PRICED, cost=1.0, unpriced=6)]),
            _envelope([]),
        )
        assert tm.baseline_unpriced_tokens == 10

    def test_any_unknown_row_makes_the_side_unknown(self) -> None:
        tm = self._tm(
            _envelope([_row(_UNPRICED, cost=0.0, unpriced=10), _row(_PRICED, cost=1.0)]),
            _envelope([_row(_PRICED, cost=1.0, unpriced=0)]),
        )
        assert tm.baseline_unpriced_tokens is None
        assert tm.current_unpriced_tokens == 0
        assert tm.total_cost_delta_measured

    def test_missing_by_model_is_unknown_not_zero(self) -> None:
        tm = self._tm(
            _envelope(None, input_tokens=5, omit_by_model=True),
            _envelope([_row(_UNPRICED, cost=0.0, unpriced=1000)]),
        )
        assert tm.baseline_unpriced_tokens is None
        assert tm.by_model[0].baseline_unpriced_tokens is None

    def test_empty_by_model_with_tokens_is_unknown_not_zero(self) -> None:
        tm = self._tm(
            _envelope([], input_tokens=5),
            _envelope([_row(_UNPRICED, cost=0.0, unpriced=1000)]),
        )
        assert tm.baseline_unpriced_tokens is None
        assert tm.by_model[0].baseline_unpriced_tokens is None

    def test_totals_are_serialized_and_the_predicate_is_not(self) -> None:
        dumped = self._tm(
            _envelope([_row(_UNPRICED, cost=0.0, unpriced=1000)]), _envelope([]),
        ).model_dump(mode="json")
        assert dumped["baseline_unpriced_tokens"] == 1000
        assert dumped["current_unpriced_tokens"] == 0
        assert "total_cost_delta_measured" not in dumped

    @pytest.mark.parametrize(
        ("baseline", "current", "measured"),
        [
            (0, 0, True), (None, None, True), (None, 0, True),
            (5, 0, False), (0, 5, False), (None, 5, False),
        ],
    )
    def test_total_cost_delta_measured(
        self, baseline: int | None, current: int | None, measured: bool,
    ) -> None:
        tm = TokenMetricsDelta(baseline_unpriced_tokens=baseline, current_unpriced_tokens=current)
        assert tm.total_cost_delta_measured is measured
