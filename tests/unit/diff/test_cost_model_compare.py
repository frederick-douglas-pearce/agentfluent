"""``diff`` cost-model comparison (#543): loading stamps and classifying them."""

from __future__ import annotations

from typing import Any

import pytest

from agentfluent.analytics.cost_model import CostModelStamp
from agentfluent.diff import compute_diff
from agentfluent.diff.models import DiffResult


def _stamp(pin: str | None = "0.2.3", method: int | None = 1) -> dict[str, Any]:
    return {"genai_prices_version": pin, "cost_method_version": method}


def _env(
    *, stamp: Any = None, version: str | None = None, include_stamp: bool = True,
) -> dict[str, Any]:
    envelope: dict[str, Any] = {
        "session_count": 1,
        "token_metrics": {"total_cost": 1.0, "by_model": []},
        "agent_metrics": {"by_agent_type": {}},
        "diagnostics": None,
    }
    if include_stamp and stamp is not None:
        envelope["cost_model"] = stamp
    if version is not None:
        envelope["diagnostics_version"] = version
    return envelope


class TestCompare:
    def test_match(self) -> None:
        result = compute_diff(_env(stamp=_stamp()), _env(stamp=_stamp()))
        assert result.cost_model_status == "match"
        assert result.cost_model_changes == []
        assert result.baseline_cost_model == CostModelStamp(
            genai_prices_version="0.2.3", cost_method_version=1,
        )

    def test_pricing_table_mismatch(self) -> None:
        result = compute_diff(
            _env(stamp=_stamp(pin="0.1.4")), _env(stamp=_stamp(pin="0.2.3")),
        )
        assert result.cost_model_status == "mismatch"
        assert result.cost_model_changes == ["pricing_table"]

    def test_cost_method_mismatch_with_same_pin(self) -> None:
        """AC5: a method change raises the caveat even when the pin is unchanged."""
        result = compute_diff(
            _env(stamp=_stamp(method=1)), _env(stamp=_stamp(method=2)),
        )
        assert result.cost_model_status == "mismatch"
        assert result.cost_model_changes == ["cost_method"]

    def test_both_components_differ(self) -> None:
        result = compute_diff(
            _env(stamp=_stamp(pin="0.1.4", method=1)),
            _env(stamp=_stamp(pin="0.2.3", method=2)),
        )
        assert result.cost_model_changes == ["pricing_table", "cost_method"]

    def test_baseline_unstamped_is_unknown(self) -> None:
        result = compute_diff(_env(version="0.11.0"), _env(stamp=_stamp(), version="0.12.0"))
        assert result.baseline_cost_model is None
        assert result.cost_model_status == "unknown"
        assert result.cost_model_changes == []

    def test_current_unstamped_is_unknown(self) -> None:
        result = compute_diff(_env(stamp=_stamp()), _env())
        assert result.current_cost_model is None
        assert result.cost_model_status == "unknown"

    @pytest.mark.parametrize(
        ("baseline_version", "current_version"),
        [(None, None), ("0.11.0", None), (None, "0.11.0"), ("0.10.0", "0.11.0")],
    )
    def test_both_unstamped_unknown_unless_same_release(
        self, baseline_version: str | None, current_version: str | None,
    ) -> None:
        result = compute_diff(
            _env(version=baseline_version), _env(version=current_version),
        )
        assert result.cost_model_status == "unknown"

    def test_both_unstamped_same_release_matches(self) -> None:
        result = compute_diff(_env(version="0.11.0"), _env(version="0.11.0"))
        assert result.cost_model_status == "match"

    @pytest.mark.parametrize(
        ("baseline", "current"),
        [
            (_stamp(pin=None), _stamp()),
            (_stamp(), _stamp(pin=None)),
            (_stamp(method=None), _stamp(method=None)),
            (_stamp(pin=None), _stamp(pin=None)),
        ],
    )
    def test_none_component_is_unknown_not_equal(
        self, baseline: dict[str, Any], current: dict[str, Any],
    ) -> None:
        result = compute_diff(_env(stamp=baseline), _env(stamp=current))
        assert result.cost_model_status == "unknown"
        assert result.cost_model_changes == []

    def test_known_difference_wins_over_unknown_component(self) -> None:
        result = compute_diff(
            _env(stamp=_stamp(pin=None, method=1)),
            _env(stamp=_stamp(pin="0.2.3", method=2)),
        )
        assert result.cost_model_status == "mismatch"
        assert result.cost_model_changes == ["cost_method"]

    @pytest.mark.parametrize(
        "malformed", ["0.2.3", 7, [], {"cost_method_version": "not-an-int"}],
    )
    def test_malformed_stamp_is_unknown(self, malformed: Any) -> None:
        result = compute_diff(_env(stamp=malformed), _env(stamp=_stamp()))
        assert result.baseline_cost_model is None
        assert result.cost_model_status == "unknown"

    def test_null_stamp_is_unknown(self) -> None:
        baseline = _env() | {"cost_model": None}
        result = compute_diff(baseline, _env(stamp=_stamp()))
        assert result.cost_model_status == "unknown"

    def test_cost_model_never_sets_regression(self) -> None:
        result = compute_diff(
            _env(stamp=_stamp(method=1)), _env(stamp=_stamp(method=2)),
        )
        assert result.regression_detected is False

    def test_json_round_trip_keeps_stamps_and_status(self) -> None:
        result = compute_diff(
            _env(stamp=_stamp(pin="0.1.4")), _env(stamp=_stamp()),
        )
        dumped = result.model_dump(mode="json")
        assert dumped["cost_model_status"] == "mismatch"
        assert dumped["cost_model_changes"] == ["pricing_table"]
        assert dumped["baseline_cost_model"] == _stamp(pin="0.1.4")
        rehydrated = DiffResult.model_validate(dumped)
        assert rehydrated.cost_model_status == "mismatch"
