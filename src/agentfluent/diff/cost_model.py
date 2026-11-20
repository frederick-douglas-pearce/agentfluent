"""Compare the cost-model stamps of two envelopes (#543).

Pure functions behind :attr:`agentfluent.diff.models.DiffResult.cost_model_status` and
:attr:`~agentfluent.diff.models.DiffResult.cost_model_changes`. Kept apart from the models
module so the comparison rules live in one small, directly testable place.
"""

from __future__ import annotations

from typing import Literal

from agentfluent.analytics.cost_model import CostModelStamp

CostModelStatus = Literal["match", "mismatch", "unknown"]
CostModelChange = Literal["pricing_table", "cost_method"]


def _components(stamp: CostModelStamp) -> dict[CostModelChange, object]:
    return {
        "pricing_table": stamp.genai_prices_version,
        "cost_method": stamp.cost_method_version,
    }


def cost_model_status(
    baseline: CostModelStamp | None,
    current: CostModelStamp | None,
) -> CostModelStatus:
    """Classify whether two envelopes were priced by the same cost model.

    - Both stamped: ``mismatch`` if any component differs; ``unknown`` if no component differs
      but one is ``None`` on either side (a missing value is never equal to anything);
      otherwise ``match``.
    - Either side without a readable stamp (absent, null or malformed): ``unknown``. This
      holds even when both sides are unstamped and share a ``diagnostics_version``: the
      package version does not identify the cost model, because unreleased builds keep the
      last release's version string while their pin and cost method move.
    """
    if baseline is None or current is None:
        return "unknown"
    if cost_model_changes(baseline, current):
        return "mismatch"
    base_parts = _components(baseline)
    curr_parts = _components(current)
    if any(base_parts[k] is None or curr_parts[k] is None for k in base_parts):
        return "unknown"
    return "match"


def cost_model_changes(
    baseline: CostModelStamp | None,
    current: CostModelStamp | None,
) -> list[CostModelChange]:
    """Name the stamp components that are known on both sides and differ.

    Empty when either stamp is missing: nothing is known to have changed, which
    :func:`cost_model_status` reports as ``unknown`` rather than as a difference.
    """
    if baseline is None or current is None:
        return []
    base_parts = _components(baseline)
    curr_parts = _components(current)
    return [
        key
        for key in base_parts
        if base_parts[key] is not None
        and curr_parts[key] is not None
        and base_parts[key] != curr_parts[key]
    ]
