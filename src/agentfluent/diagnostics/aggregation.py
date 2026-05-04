"""Aggregate per-invocation recommendations into distinct findings.

``correlate`` emits one recommendation per matched signal, which produces
N near-identical rows when N invocations of the same agent trigger the
same rule (e.g., four Explore invocations each firing TOKEN_OUTLIER).
The default Recommendations table is far more actionable when those N
rows collapse to a single aggregated row with an occurrence count and
(for signal types that carry scalar metrics) a min–max range.

Aggregation happens in the pipeline, after ``correlate``, so every
output format benefits — not just the table formatter. The raw
per-invocation list is preserved alongside the aggregated list on
``DiagnosticsResult`` so ``--verbose`` and JSON consumers can drill in.

**Priority scoring (#172).** Each aggregated row carries a
``priority_score: float`` so the default table can sort by impact.
The score is a weighted composite:

    score = severity_rank * W_SEVERITY
          + log1p(count) * W_COUNT
          + summed_savings_usd * W_COST
          + has_trace_evidence * W_TRACE

Severity dominates: ``W_SEVERITY = 100`` is large enough that a
single CRITICAL outranks any volume of WARNINGs. ``log1p(count)``
gives diminishing returns so 100 occurrences score ~5x a single
one, not 100x. ``summed_savings_usd`` is summed across contributing
``MODEL_MISMATCH`` signals; each signal's ``estimated_savings_usd``
is **already an agent-type aggregate** (model_routing emits one
signal per agent_type), so summing across contributors only
double-counts when multiple agent_types share the same
``(target, signal_types)`` shape — vanishing-rare. Trace-signal
evidence (``STUCK_PATTERN``, ``RETRY_LOOP``, ``PERMISSION_FAILURE``,
``TOOL_ERROR_SEQUENCE``) adds a modest boost so deep findings
outrank metadata-only ones at the same severity + count.
"""

from __future__ import annotations

import math
from collections import defaultdict

from agentfluent.config.models import Severity
from agentfluent.diagnostics.models import (
    TRACE_SIGNAL_TYPES,
    AggregatedRecommendation,
    DiagnosticRecommendation,
    DiagnosticSignal,
    SignalType,
)

_SEVERITY_RANK: dict[Severity, int] = {
    Severity.CRITICAL: 3,
    Severity.WARNING: 2,
    Severity.INFO: 1,
}

# Signal types that carry comparable scalar metrics in ``detail``. Only
# these produce a ``metric_range`` on the aggregated row.
_SCALAR_METRIC_SIGNALS: frozenset[SignalType] = frozenset(
    {SignalType.TOKEN_OUTLIER, SignalType.DURATION_OUTLIER},
)

# Priority-score weights (#172). Tuned so severity dominates: a single
# CRITICAL (rank 3) outranks any volume of WARNING (rank 2). Calibration
# pass against multi-contributor data is a v0.6 follow-up if dogfood
# shows the ranking is off.
_PRIORITY_WEIGHT_SEVERITY = 100.0
_PRIORITY_WEIGHT_COUNT = 10.0
_PRIORITY_WEIGHT_COST = 1.0
_PRIORITY_WEIGHT_TRACE = 5.0

# Shape key for grouping per-invocation recommendations. ``agent_type``
# is ``None`` for cross-cutting recommendations (MCP audit) which form
# their own group; ``target`` distinguishes rows when one rule forks
# (e.g. TokenOutlierRule emits to either ``tools`` or ``prompt``).
type AggregationKey = tuple[str | None, str, frozenset[SignalType]]


def _aggregation_key(rec: DiagnosticRecommendation) -> AggregationKey:
    return (rec.agent_type, rec.target, frozenset(rec.signal_types))


def _compute_metric_range(signals: list[DiagnosticSignal]) -> str | None:
    """Build ``"3.2×–6.8×IQR above Q3"`` when signals carry IQR-based data.

    Returns ``None`` for signal types without comparable scalars (retry
    counts, permission failures) so the aggregated row falls back to a
    count-only message.
    """
    excesses: list[float] = []
    for sig in signals:
        if sig.signal_type not in _SCALAR_METRIC_SIGNALS:
            continue
        excess = sig.detail.get("excess_iqrs")
        if isinstance(excess, (int, float)):
            excesses.append(float(excess))

    if not excesses:
        return None

    lo, hi = min(excesses), max(excesses)
    if lo == hi:
        range_text = f"{lo:.1f}×IQR"
    else:
        range_text = f"{lo:.1f}×–{hi:.1f}×IQR"

    return f"{range_text} above Q3"


def _max_severity(recs: list[DiagnosticRecommendation]) -> Severity:
    return max(recs, key=lambda r: _SEVERITY_RANK[r.severity]).severity


def _representative_message(
    recs: list[DiagnosticRecommendation],
    count: int,
    metric_range: str | None,
) -> str:
    """Build the aggregated row's message.

    For ``count == 1`` the original message is returned verbatim so
    single-invocation findings read identically to the raw table. For
    ``count > 1`` the message is rebuilt as
    ``"<signal_type>[ (range)]: <action>"``. The Count column already
    surfaces the occurrence count, so repeating it in the prefix would
    duplicate state the table is already showing; the signal type is
    what actually differentiates same-(agent, target) aggregated rows.
    """
    rep = recs[0]
    if count == 1:
        return rep.message

    prefix = rep.signal_types[0].value if rep.signal_types else ""
    if metric_range:
        prefix = f"{prefix} ({metric_range})" if prefix else f"({metric_range})"

    if not prefix:
        return rep.action or rep.message
    if rep.action:
        return f"{prefix}: {rep.action}"
    return f"{prefix}."


def _summed_savings_usd(signals: list[DiagnosticSignal]) -> float:
    """Total ``estimated_savings_usd`` carried on contributing MODEL_MISMATCH signals.

    Each MODEL_MISMATCH signal already aggregates savings across an
    agent_type's invocations (model_routing emits one per agent_type),
    so summing here only adds across same-shape rows when multiple
    agent_types share the aggregation key — rare in practice.
    """
    total = 0.0
    for sig in signals:
        if sig.signal_type != SignalType.MODEL_MISMATCH:
            continue
        savings = sig.detail.get("estimated_savings_usd")
        if isinstance(savings, (int, float)):
            total += float(savings)
    return total


def _compute_priority_score(
    severity: Severity,
    count: int,
    summed_savings: float,
    has_trace_evidence: bool,
) -> float:
    """Composite score per the formula in this module's docstring."""
    return (
        _SEVERITY_RANK[severity] * _PRIORITY_WEIGHT_SEVERITY
        + math.log1p(count) * _PRIORITY_WEIGHT_COUNT
        + summed_savings * _PRIORITY_WEIGHT_COST
        + (1.0 if has_trace_evidence else 0.0) * _PRIORITY_WEIGHT_TRACE
    )


def aggregate_recommendations(
    pairs: list[tuple[DiagnosticSignal, DiagnosticRecommendation]],
) -> list[AggregatedRecommendation]:
    """Group paired ``(signal, recommendation)`` tuples by their shape key.

    Sorted by ``priority_score`` descending so the highest-impact findings
    surface first; severity then count are stable tiebreakers when scores
    collide (e.g., two findings with identical signal-type families).
    """
    groups: dict[
        AggregationKey,
        list[tuple[DiagnosticSignal, DiagnosticRecommendation]],
    ] = defaultdict(list)
    for signal, rec in pairs:
        groups[_aggregation_key(rec)].append((signal, rec))

    aggregated: list[AggregatedRecommendation] = []
    for (agent_type, target, signal_types_set), members in groups.items():
        signals = [s for s, _ in members]
        recs = [r for _, r in members]
        count = len(members)
        severity = _max_severity(recs)
        metric_range = _compute_metric_range(signals)
        # Architect-noted: signals are in scope here, no external lookup.
        has_trace_evidence = any(
            sig.signal_type in TRACE_SIGNAL_TYPES for sig in signals
        )
        summed_savings = _summed_savings_usd(signals)
        priority_score = _compute_priority_score(
            severity, count, summed_savings, has_trace_evidence,
        )

        aggregated.append(
            AggregatedRecommendation(
                agent_type=agent_type,
                target=target,
                severity=severity,
                signal_types=sorted(signal_types_set, key=lambda s: s.value),
                count=count,
                metric_range=metric_range,
                representative_message=_representative_message(
                    recs, count, metric_range,
                ),
                is_builtin=recs[0].is_builtin,
                contributing_recommendations=recs,
                priority_score=priority_score,
            ),
        )

    aggregated.sort(
        key=lambda a: (-a.priority_score, -_SEVERITY_RANK[a.severity], -a.count),
    )
    return aggregated
