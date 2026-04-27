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
"""

from __future__ import annotations

from collections import defaultdict

from agentfluent.config.models import Severity
from agentfluent.diagnostics.models import (
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

# Shape key for grouping per-invocation recommendations. ``agent_type``
# is ``None`` for cross-cutting recommendations (MCP audit) which form
# their own group; ``target`` distinguishes rows when one rule forks
# (e.g. TokenOutlierRule emits to either ``tools`` or ``prompt``).
type AggregationKey = tuple[str | None, str, frozenset[SignalType]]


def _aggregation_key(rec: DiagnosticRecommendation) -> AggregationKey:
    return (rec.agent_type, rec.target, frozenset(rec.signal_types))


def _compute_metric_range(signals: list[DiagnosticSignal]) -> str | None:
    """Build ``"4.9x–8.0x above 5,064 mean"`` when signals carry ratio data.

    Returns ``None`` for signal types without comparable scalars (retry
    counts, permission failures) so the aggregated row falls back to a
    count-only message.
    """
    ratios: list[float] = []
    means: list[float] = []
    for sig in signals:
        if sig.signal_type not in _SCALAR_METRIC_SIGNALS:
            continue
        ratio = sig.detail.get("ratio")
        mean = sig.detail.get("mean_value")
        if isinstance(ratio, (int, float)):
            ratios.append(float(ratio))
        if isinstance(mean, (int, float)):
            means.append(float(mean))

    if not ratios:
        return None

    lo, hi = min(ratios), max(ratios)
    if lo == hi:
        range_text = f"{lo:.1f}x"
    else:
        range_text = f"{lo:.1f}x–{hi:.1f}x"

    if means:
        mean_ref = sum(means) / len(means)
        return f"{range_text} above {mean_ref:,.0f} mean"
    return f"{range_text} above mean"


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


def aggregate_recommendations(
    pairs: list[tuple[DiagnosticSignal, DiagnosticRecommendation]],
) -> list[AggregatedRecommendation]:
    """Group paired ``(signal, recommendation)`` tuples by their shape key.

    Sorted by (severity descending, count descending) so the highest-
    impact findings surface first in the default table.
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
            ),
        )

    aggregated.sort(key=lambda a: (-_SEVERITY_RANK[a.severity], -a.count))
    return aggregated
