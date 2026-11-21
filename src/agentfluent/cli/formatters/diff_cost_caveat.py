"""The cost-caveat slot for ``agentfluent diff`` (#543, #713).

One header and one reason per line that qualify every cost delta ``diff`` shows.
Reasons come from two sources: the cost model that priced each side (#543) and
each side's cost coverage (#713). Plain text only -- the caller escapes and
styles it, so a future Markdown diff renderer can reuse the same wording.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from agentfluent.cli.formatters.coverage import cost_gap_clauses

if TYPE_CHECKING:
    from agentfluent.diff.models import DiffResult

COST_CAVEAT_HEADER = "Cost deltas below may not reflect agent behavior:"


def cost_caveat_reasons(result: DiffResult) -> list[str]:
    """Reasons that qualify the cost deltas, or ``[]`` when none apply.

    This is the single slot for cost caveats: printed once, above the total,
    by-model and per-agent cost tables. Order: cost model, then baseline
    coverage, then current coverage.
    """
    reasons: list[str] = []
    cost_model_reason = _cost_model_reason(result)
    if cost_model_reason is not None:
        reasons.append(cost_model_reason)
    for side in ("baseline", "current"):
        coverage_reason = _cost_coverage_reason(result, side)
        if coverage_reason is not None:
            reasons.append(coverage_reason)
    return reasons


def _cost_model_reason(result: DiffResult) -> str | None:
    """Explain a cost-model mismatch or an unknown cost model (#543)."""
    status = result.cost_model_status
    if status == "match":
        return None
    baseline = result.baseline_cost_model
    current = result.current_cost_model
    if status == "mismatch" and baseline is not None and current is not None:
        parts: list[str] = []
        if "pricing_table" in result.cost_model_changes:
            parts.append(
                f"genai-prices {baseline.genai_prices_version} → "
                f"{current.genai_prices_version}",
            )
        if "cost_method" in result.cost_model_changes:
            parts.append(
                f"cost method {baseline.cost_method_version} → "
                f"{current.cost_method_version}",
            )
        return (
            "baseline and current were priced by different cost models ("
            + "; ".join(parts)
            + "), so cost deltas may reflect the pricing change, not agent "
            "behavior."
        )
    if baseline is not None and current is not None:
        subject = "a snapshot's cost-model stamp is incomplete"
    elif baseline is None and current is None:
        subject = (
            "neither snapshot has a readable cost-model stamp (missing or malformed)"
        )
    elif baseline is None:
        subject = (
            "the baseline snapshot has no readable cost-model stamp (missing or malformed)"
        )
    else:
        subject = (
            "the current snapshot has no readable cost-model stamp (missing or malformed)"
        )
    return (
        f"cost model unknown: {subject}, so cost deltas may include pricing "
        "or cost-method changes, not only agent behavior."
    )


def _cost_coverage_reason(result: DiffResult, side: str) -> str | None:
    """Explain one side's unknown or incomplete cost coverage (#713)."""
    if side == "baseline":
        status = result.baseline_cost_coverage_status
        coverage = result.baseline_cost_coverage
        unparseable = result.baseline_unparseable_trace_files
    else:
        status = result.current_cost_coverage_status
        coverage = result.current_cost_coverage
        unparseable = result.current_unparseable_trace_files
    if status == "full":
        return None
    if status == "unknown" or coverage is None or unparseable is None:
        return (
            f"cost coverage unknown: the {side} snapshot has no readable cost-coverage "
            "record (missing or malformed; envelopes from before v0.12.0 lack it), "
            "so its dollar figures may leave out unpriced tokens or agent invocations."
        )
    clauses = cost_gap_clauses(coverage.model_dump(), unparseable)
    return (
        f"{side} cost coverage is incomplete, so cost deltas may reflect what "
        "could not be priced, not agent behavior. " + " ".join(clauses)
    )
