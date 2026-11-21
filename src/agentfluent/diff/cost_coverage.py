"""Compare the cost coverage of two envelopes (#713).

Pure functions behind :attr:`agentfluent.diff.models.DiffResult.cost_coverage_status`.
Kept apart from the models module, like :mod:`agentfluent.diff.cost_model`, so the
rules live in one small, directly testable place.

Scope: the cost figures ``diff`` compares -- totals, by-model and per-agent cost.
Diagnostics dollar figures are not diffed, so their coverage is not part of this
status; the same snapshot can therefore read ``full`` here while the ``analyze``
footer reports it ``incomplete`` for a diagnostics gap.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import ValidationError

from agentfluent.analytics.cost_coverage import CostCoverage, has_cost_gap

CostCoverageStatus = Literal["full", "incomplete", "unknown"]


_REQUIRED_PRICING_KEYS = ("tokens", "unpriced_tokens")
_REQUIRED_INVOCATION_KEYS = ("total", "linked", "spend", "priced")


def load_cost_coverage(envelope: dict[str, Any]) -> CostCoverage | None:
    """Read the envelope's ``cost_coverage`` block, or ``None`` when it is absent
    (pre-v0.12.0), null, or malformed.

    Fails closed, because every :class:`CostCoverage` count defaults to zero and a
    zero reads as "no gap". The block counts only when:

    - ``pricing`` carries ``tokens`` and ``unpriced_tokens``, and ``invocations``
      carries ``total``, ``linked``, ``spend`` and ``priced`` -- every count the gap
      check and the caveat clauses read; and
    - those counts are consistent: ``0 <= priced <= spend <= linked <= total`` and
      ``0 <= unpriced_tokens <= tokens``.

    Anything else returns ``None``, which ``diff`` reports as ``unknown``. Values
    pydantic coerces losslessly (``"10"``, ``10.0``) are accepted as read; this
    check is diff's own and does not constrain the ``analyze`` models.
    """
    raw = envelope.get("cost_coverage")
    if not isinstance(raw, dict):
        return None
    pricing = raw.get("pricing")
    invocations = raw.get("invocations")
    if not isinstance(pricing, dict) or not isinstance(invocations, dict):
        return None
    if any(key not in pricing for key in _REQUIRED_PRICING_KEYS) or any(
        key not in invocations for key in _REQUIRED_INVOCATION_KEYS
    ):
        return None
    try:
        coverage = CostCoverage.model_validate(raw)
    except ValidationError:
        return None
    if not _is_consistent(coverage):
        return None
    return coverage


def _is_consistent(coverage: CostCoverage) -> bool:
    inv = coverage.invocations
    pricing = coverage.pricing
    return (
        0 <= inv.priced <= inv.spend <= inv.linked <= inv.total
        and 0 <= pricing.unpriced_tokens <= pricing.tokens
    )


def load_unparseable_trace_files(envelope: dict[str, Any]) -> int | None:
    """Read ``trace_coverage.unparseable.files``, or ``None`` when any level of
    it is missing or not the expected type (never ``0`` by default)."""
    trace_coverage = envelope.get("trace_coverage")
    if not isinstance(trace_coverage, dict):
        return None
    unparseable = trace_coverage.get("unparseable")
    if not isinstance(unparseable, dict):
        return None
    files = unparseable.get("files")
    if isinstance(files, bool) or not isinstance(files, int) or files < 0:
        return None
    return files


def side_coverage_status(
    coverage: CostCoverage | None,
    unparseable_trace_files: int | None,
) -> CostCoverageStatus:
    """Classify one envelope's cost coverage.

    ``unknown`` when the envelope has no readable ``cost_coverage`` block (absent
    before v0.12.0, null, or malformed) or no readable unparseable-trace count:
    either missing input could hide a gap, so it is never read as ``full``.
    ``incomplete`` when :func:`~agentfluent.analytics.cost_coverage.has_cost_gap`
    finds a gap; otherwise ``full``.
    """
    if coverage is None or unparseable_trace_files is None:
        return "unknown"
    if has_cost_gap(coverage, unparseable_trace_files):
        return "incomplete"
    return "full"


def cost_coverage_status(
    baseline: CostCoverageStatus,
    current: CostCoverageStatus,
) -> CostCoverageStatus:
    """Combine the two sides: ``unknown`` if either side is unknown (the weaker
    claim wins), else ``incomplete`` if either side is incomplete, else ``full``."""
    if "unknown" in (baseline, current):
        return "unknown"
    if "incomplete" in (baseline, current):
        return "incomplete"
    return "full"
