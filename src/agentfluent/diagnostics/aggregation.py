"""Aggregate per-invocation recommendations into distinct findings.

``correlate`` emits one recommendation per matched signal, producing
N near-identical rows when N invocations trigger the same rule. The
default Recommendations table is more actionable when those rows
collapse to a single row with an occurrence count and (for signal
types with scalar metrics) a min–max range. Aggregation runs after
``correlate`` so every output format benefits; per-invocation
recommendations are preserved on ``DiagnosticsResult`` for
``--verbose`` and JSON drill-down.

**Priority scoring (#172, #272).** Each aggregated row carries a
``priority_score`` for sort-by-impact:

    score = severity_rank * W_SEVERITY
          + log1p(count) * W_COUNT
          + summed_savings_usd * W_COST
          + has_trace_evidence * W_TRACE
          + quality_evidence_factor * W_QUALITY

Severity dominates (``W_SEVERITY = 100``): one CRITICAL outranks any
volume of WARNINGs. ``log1p(count)`` damps repeats. Trace evidence
(``STUCK_PATTERN``, ``RETRY_LOOP``, ``PERMISSION_FAILURE``,
``TOOL_ERROR_SEQUENCE``) gets a modest boost over metadata-only
signals.

The ``quality_evidence_factor`` (D021) is ``1.0`` when any
contributing signal maps to ``Axis.QUALITY``, else ``0.0``. The
annotations approach preserves backward compatibility: recommendations
without quality signals score identically to v0.5, so post-upgrade
``diff`` shows zero ``priority_score_delta`` for persisting
non-quality recommendations.

**Axis attribution (#272, D021/D022/D027).** ``axis_scores`` sums
each signal's severity-rank contribution into the axis bucket assigned
by ``SIGNAL_AXIS_MAP``. ``primary_axis`` is the largest bucket; ties
resolve via ``_AXIS_TIEBREAKER`` (quality > speed > cost) per D027.
"""

from __future__ import annotations

import math
from collections import defaultdict

from agentfluent.config.models import SEVERITY_RANK as _SEVERITY_RANK
from agentfluent.config.models import Severity
from agentfluent.diagnostics.model_routing import SAVINGS_USD_KEY
from agentfluent.diagnostics.models import (
    TRACE_SIGNAL_TYPES,
    AggregatedRecommendation,
    Axis,
    DiagnosticRecommendation,
    DiagnosticSignal,
    SignalType,
    zero_axis_scores,
)

# Single-axis classification per D022. Every ``SignalType`` maps to
# exactly one ``Axis``; cross-cutting reduced-weight contributions were
# rejected for Tier 1. ``ERROR_PATTERN``, ``PERMISSION_FAILURE``, and
# ``MCP_MISSING_SERVER`` land on ``SPEED`` as the closest existing axis
# for operational-health signals. Defined here rather than in
# ``models.py`` because aggregation is the natural consumer; the
# drift-prevention test in ``tests/unit/test_recommendation_aggregation``
# ensures any new ``SignalType`` lacking an entry fails CI.
SIGNAL_AXIS_MAP: dict[SignalType, Axis] = {
    SignalType.TOKEN_OUTLIER: Axis.COST,
    SignalType.MODEL_MISMATCH: Axis.COST,
    SignalType.MCP_UNUSED_SERVER: Axis.COST,
    SignalType.DURATION_OUTLIER: Axis.SPEED,
    SignalType.RETRY_LOOP: Axis.SPEED,
    SignalType.STUCK_PATTERN: Axis.SPEED,
    SignalType.TOOL_ERROR_SEQUENCE: Axis.SPEED,
    SignalType.ERROR_PATTERN: Axis.SPEED,
    SignalType.PERMISSION_FAILURE: Axis.SPEED,
    SignalType.MCP_MISSING_SERVER: Axis.SPEED,
    SignalType.USER_CORRECTION: Axis.QUALITY,
    SignalType.FILE_REWORK: Axis.QUALITY,
    SignalType.REVIEWER_CAUGHT: Axis.QUALITY,
}

# Signal types that carry comparable scalar metrics in ``detail``. Only
# these produce a ``metric_range`` on the aggregated row.
_SCALAR_METRIC_SIGNALS: frozenset[SignalType] = frozenset(
    {SignalType.TOKEN_OUTLIER, SignalType.DURATION_OUTLIER},
)

# Priority-score weights (#172, #272). Tuned so severity dominates: a
# single CRITICAL (rank 3) outranks any volume of WARNING (rank 2).
# Calibration pass against multi-contributor data is a v0.6 follow-up
# if dogfood shows the ranking is off (#274).
_PRIORITY_WEIGHT_SEVERITY = 100.0
_PRIORITY_WEIGHT_COUNT = 10.0
_PRIORITY_WEIGHT_COST = 1.0
_PRIORITY_WEIGHT_TRACE = 5.0
_PRIORITY_WEIGHT_QUALITY = 5.0

# D027: deterministic tiebreaker for ``primary_axis`` when two or more
# axes carry equal ``axis_scores``. Quality wins ties so the v0.6
# headline axis stays visible by default; see decisions.md D027 for
# the product rationale and tradeoffs.
_AXIS_TIEBREAKER: tuple[Axis, ...] = (Axis.QUALITY, Axis.SPEED, Axis.COST)

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
        savings = sig.detail.get(SAVINGS_USD_KEY)
        if isinstance(savings, (int, float)):
            total += float(savings)
    return total


def _compute_priority_score(
    severity: Severity,
    count: int,
    summed_savings: float,
    has_trace_evidence: bool,
    quality_evidence_factor: float = 0.0,
) -> float:
    """Composite score per the formula in this module's docstring."""
    return (
        _SEVERITY_RANK[severity] * _PRIORITY_WEIGHT_SEVERITY
        + math.log1p(count) * _PRIORITY_WEIGHT_COUNT
        + summed_savings * _PRIORITY_WEIGHT_COST
        + (1.0 if has_trace_evidence else 0.0) * _PRIORITY_WEIGHT_TRACE
        + quality_evidence_factor * _PRIORITY_WEIGHT_QUALITY
    )


def _signal_axis_contribution(signal: DiagnosticSignal) -> float:
    """Per-signal contribution to its axis bucket — Tier 1 = severity rank.

    Calibration of the per-signal factor is deferred to #274.
    """
    return float(_SEVERITY_RANK[signal.severity])


def _compute_axis_attribution(
    signals: list[DiagnosticSignal],
) -> tuple[dict[str, float], str]:
    """Sum per-signal axis contributions and resolve ``primary_axis``.

    Returns ``(axis_scores, primary_axis)``. ``axis_scores`` keys are
    bare axis strings for stable JSON serialization. ``primary_axis``
    breaks ties via D027 (quality > speed > cost).
    """
    axis_scores = zero_axis_scores()
    for sig in signals:
        axis = SIGNAL_AXIS_MAP[sig.signal_type]
        axis_scores[axis.value] += _signal_axis_contribution(sig)
    primary_axis = max(
        _AXIS_TIEBREAKER, key=lambda a: axis_scores[a.value],
    ).value
    return axis_scores, primary_axis


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
        has_trace_evidence = any(
            sig.signal_type in TRACE_SIGNAL_TYPES for sig in signals
        )
        summed_savings = _summed_savings_usd(signals)
        axis_scores, primary_axis = _compute_axis_attribution(signals)
        quality_evidence_factor = (
            1.0 if axis_scores[Axis.QUALITY.value] > 0.0 else 0.0
        )
        priority_score = _compute_priority_score(
            severity,
            count,
            summed_savings,
            has_trace_evidence,
            quality_evidence_factor=quality_evidence_factor,
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
                axis_scores=axis_scores,
                primary_axis=primary_axis,
            ),
        )

    aggregated.sort(
        key=lambda a: (-a.priority_score, -_SEVERITY_RANK[a.severity], -a.count),
    )
    return aggregated
