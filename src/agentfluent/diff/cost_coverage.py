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


def load_cost_coverage(envelope: dict[str, Any]) -> CostCoverage | None:
    """Read the envelope's ``cost_coverage`` block, or ``None`` when it is absent
    (pre-v0.12.0), null, or malformed.

    Fails closed: every :class:`CostCoverage` field defaults to zero, so a lenient
    validation of ``{}`` (or of a block missing ``pricing`` or ``invocations``)
    would read as full coverage. Both sub-blocks must be objects to count.
    """
    raw = envelope.get("cost_coverage")
    if not isinstance(raw, dict):
        return None
    if not isinstance(raw.get("pricing"), dict) or not isinstance(
        raw.get("invocations"), dict,
    ):
        return None
    try:
        return CostCoverage.model_validate(raw)
    except ValidationError:
        return None


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
