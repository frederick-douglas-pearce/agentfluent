"""Per-side ``unpriced_tokens`` on the diff's by-model rows and totals (#723).

``None`` means unknown, never 0: a legacy envelope (pre-v0.12.0 rows, or a v1
dict-shaped ``by_model``) cannot say whether its ``0.0`` cost was unpriced.
"""

from __future__ import annotations

from typing import Any

import pytest

from agentfluent.analytics.tokens import row_unpriced_tokens, sum_unpriced_tokens
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


def _envelope(by_model: list[dict[str, Any]] | dict[str, Any]) -> dict[str, Any]:
    return {
        "session_count": 1,
        "token_metrics": {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "total_cost": 0.0,
            "cache_efficiency": 0.0,
            "by_model": by_model,
        },
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

    @pytest.mark.parametrize("value", [None, "40", -1, True, 4.0])
    def test_malformed_value_is_unknown(self, value: Any) -> None:
        assert row_unpriced_tokens({"unpriced_tokens": value}) is None


class TestSumUnpricedTokens:
    def test_sums_when_every_row_carries_the_field(self) -> None:
        assert sum_unpriced_tokens([{"unpriced_tokens": 10}, {"unpriced_tokens": 5}]) == 15

    def test_any_row_without_the_field_makes_the_sum_unknown(self) -> None:
        assert sum_unpriced_tokens([{"unpriced_tokens": 10}, {}]) is None

    def test_a_null_field_makes_the_sum_unknown(self) -> None:
        assert sum_unpriced_tokens([{"unpriced_tokens": 10}, {"unpriced_tokens": None}]) is None

    def test_empty_is_zero(self) -> None:
        assert sum_unpriced_tokens([]) == 0


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
        assert row.cost_delta == pytest.approx(1.0)  # JSON stays numeric (D029)

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


class TestTokenMetricsDeltaTotals:
    def _delta(self, *pairs: tuple[int | None, int | None]) -> TokenMetricsDelta:
        return TokenMetricsDelta(by_model=[
            ModelTokenDelta(model=f"m{i}", baseline_unpriced_tokens=b, current_unpriced_tokens=c)
            for i, (b, c) in enumerate(pairs)
        ])

    def test_sums_each_side(self) -> None:
        tm = self._delta((10, 0), (5, 0))
        assert (tm.baseline_unpriced_tokens, tm.current_unpriced_tokens) == (15, 0)
        assert not tm.total_cost_delta_measured

    def test_any_unknown_row_makes_the_side_unknown(self) -> None:
        tm = self._delta((10, 0), (None, 0))
        assert tm.baseline_unpriced_tokens is None
        assert tm.current_unpriced_tokens == 0
        assert tm.total_cost_delta_measured

    def test_all_priced_is_measured(self) -> None:
        assert self._delta((0, 0)).total_cost_delta_measured

    def test_totals_are_not_serialized(self) -> None:
        dumped = self._delta((10, 0)).model_dump(mode="json")
        assert "baseline_unpriced_tokens" not in dumped
        assert "total_cost_delta_measured" not in dumped
