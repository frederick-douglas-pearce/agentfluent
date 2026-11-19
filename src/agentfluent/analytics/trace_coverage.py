"""``trace_coverage``: how much of a session's subagent spend came from where (#648).

Built from ``traces.coverage.SessionTraces`` after the session dedup has run.
Every number is per cohort, so attributed (``linked``) and unattributed
(``nested``, ``session_spawned``) spend are never blended into one figure.

``tokens`` and ``cost`` read ``trace_spend_usage`` -- the deduplicated usage the
session fold sums -- so for the folded cohorts they are exactly what each
cohort added to ``token_metrics``. That gives the reconciliation #648 AC3
tests: Σ per-agent cost + ``nested.cost`` + ``session_spawned.cost`` equals
the subagent-origin ``by_model`` cost.

**These field names are public JSON API** (D055): ``SessionAnalysis`` and
``AnalysisResult`` serialize them under ``trace_coverage``. Envelope stays "2"
(D029) -- the change is additive.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from agentfluent.analytics.tokens import (
    TraceDedupStats,
    price_trace,
    trace_counts_as_spend,
    trace_spend_usage,
)
from agentfluent.traces.coverage import SessionTraces
from agentfluent.traces.models import SubagentTrace

UNKNOWN_SPAWNER = "unknown"


class CohortCoverage(BaseModel):
    """Files, tokens and cost for one cohort of trace files."""

    files: int = 0
    """Trace files in this cohort."""

    tokens: int = 0
    """Deduplicated tokens (input + output + cache) of the cohort's traces that
    count as spend. ``0`` for ``unparseable``."""

    cost: float = 0.0
    """USD cost of ``tokens``, summed over the traces whose model is priced."""

    unpriced_files: int = 0
    """Files that count as spend but whose model has no price, so their tokens
    are in ``tokens`` and not in ``cost``. Kept apart so "unpriced" never reads
    as $0."""

    folded: bool = False
    """Whether this cohort is in the session's ``token_metrics`` (and so in
    ``total_cost``). ``linked``, ``nested`` and ``session_spawned`` are folded;
    ``filtered`` and ``unparseable`` are not."""

    def add(self, other: CohortCoverage) -> None:
        self.files += other.files
        self.tokens += other.tokens
        self.cost += other.cost
        self.unpriced_files += other.unpriced_files


def _folded() -> CohortCoverage:
    return CohortCoverage(folded=True)


class TraceCoverage(BaseModel):
    """Where a session's subagent trace spend came from, by cohort (#648)."""

    linked: CohortCoverage = Field(default_factory=_folded)
    """Traces owning an invocation row: the spend behind per-agent rows."""

    nested: CohortCoverage = Field(default_factory=_folded)
    """Depth >= 2 traces. Folded into session totals, not into ``by_agent_type``."""

    session_spawned: CohortCoverage = Field(default_factory=_folded)
    """Agents the session spawned without an ``Agent`` call (skill- and
    workflow-run background agents such as ``/code-review``). Folded into
    session totals, not into ``by_agent_type``."""

    filtered: CohortCoverage = Field(default_factory=CohortCoverage)
    """Traces excluded by ``--agent``. Not a loss: the run asked for it."""

    unparseable: CohortCoverage = Field(default_factory=CohortCoverage)
    """Trace files that could not be parsed. Their spend is unknown."""

    session_spawned_by_type: dict[str, CohortCoverage] = Field(default_factory=dict)
    """``session_spawned`` split by what spawned it: the sidecar ``name`` when
    present (``"code-review"``), else its ``agentType``, else ``"unknown"``."""

    deduped_turns: int = 0
    """API turns dropped because the main session or an earlier trace already
    counted them (fork agents replay context, #701). Session-wide and counted
    before ``--agent`` is applied, so it includes drops among ``filtered``
    traces, and a second parse of one file when two invocations share an
    ``agent_id`` (#705)."""

    deduped_tokens: int = 0
    """Tokens in ``deduped_turns``."""

    def add(self, other: TraceCoverage) -> None:
        self.linked.add(other.linked)
        self.nested.add(other.nested)
        self.session_spawned.add(other.session_spawned)
        self.filtered.add(other.filtered)
        self.unparseable.add(other.unparseable)
        for spawner, cohort in other.session_spawned_by_type.items():
            self.session_spawned_by_type.setdefault(spawner, _folded()).add(cohort)
        self.deduped_turns += other.deduped_turns
        self.deduped_tokens += other.deduped_tokens


def _trace_cohort(trace: SubagentTrace | None, *, timestamp: datetime | None) -> CohortCoverage:
    """One file's contribution, priced like the session fold prices it."""
    cohort = CohortCoverage(files=1)
    if trace is None or not trace_counts_as_spend(trace):
        return cohort
    cohort.tokens = trace_spend_usage(trace).total_tokens
    cost = price_trace(trace, timestamp=timestamp)
    if cost is None:
        cohort.unpriced_files = 1
    else:
        cohort.cost = cost
    return cohort


def compute_trace_coverage(
    traces: SessionTraces,
    dedup: TraceDedupStats,
    *,
    timestamp: datetime | None = None,
) -> TraceCoverage:
    """Build the coverage object for one session.

    Call after ``dedupe_trace_turns`` has run on ``traces``. ``timestamp`` is the
    session price date (#546), the same one the fold uses.
    """
    coverage = TraceCoverage(
        deduped_turns=dedup.dropped_turns, deduped_tokens=dedup.dropped_tokens,
    )
    for owned in traces.owned:
        contribution = _trace_cohort(owned.trace, timestamp=timestamp)
        getattr(coverage, owned.cohort).add(contribution)
        if owned.cohort == "session_spawned":
            spawner = owned.spawned_as or UNKNOWN_SPAWNER
            coverage.session_spawned_by_type.setdefault(spawner, _folded()).add(contribution)
    return coverage


def merge_trace_coverage(coverages: list[TraceCoverage]) -> TraceCoverage:
    """Sum per-session coverage into one run-level object."""
    total = TraceCoverage()
    for coverage in coverages:
        total.add(coverage)
    return total
