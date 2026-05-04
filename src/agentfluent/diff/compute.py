"""Pure compute layer for ``agentfluent diff``.

No I/O, no formatting — takes two envelope dicts (as produced by
:func:`agentfluent.diff.loader.load_envelope`) and returns a
:class:`DiffResult`. Reusable by the CLI today and the v0.6 markdown
report (#198) tomorrow.

Recommendation grouping uses ``frozenset(signal_types)`` to match
``diagnostics.aggregation._aggregation_key`` exactly — JSON
deserialization gives ordered lists, so both sides are normalized to
the same canonical form here.
"""

from __future__ import annotations

from typing import Any

from agentfluent.config.models import Severity
from agentfluent.diagnostics.models import SignalType
from agentfluent.diff.models import (
    AgentTypeDelta,
    DiffResult,
    ModelTokenDelta,
    RecommendationDelta,
    TokenMetricsDelta,
)

# Severity ranking for `--fail-on` threshold checks. Mirrors the
# ordering implied by config.models.Severity but pinned numerically here
# so the comparison is explicit.
_SEVERITY_RANK: dict[Severity, int] = {
    Severity.INFO: 1,
    Severity.WARNING: 2,
    Severity.CRITICAL: 3,
}


type RecKey = tuple[str | None, str, frozenset[SignalType]]
"""Mirrors ``diagnostics.aggregation.AggregationKey``. Re-declared
locally so this module doesn't import from aggregation (which pulls in
the whole diagnostics graph). The contract is enforced by tests."""


def compute_diff(
    baseline: dict[str, Any],
    current: dict[str, Any],
    *,
    fail_on: Severity | None = None,
) -> DiffResult:
    """Compare two envelope ``data`` dicts and return a :class:`DiffResult`.

    Args:
        baseline: ``data`` payload from the older analyze run.
        current: ``data`` payload from the newer analyze run.
        fail_on: If set, mark ``regression_detected=True`` when any new
            recommendation has severity at or above this threshold.
    """
    rec_deltas = _diff_recommendations(baseline, current)
    token_delta = _diff_token_metrics(baseline, current)
    agent_deltas = _diff_agent_metrics(baseline, current)

    new_count = sum(1 for r in rec_deltas if r.status == "new")
    resolved_count = sum(1 for r in rec_deltas if r.status == "resolved")
    persisting_count = sum(1 for r in rec_deltas if r.status == "persisting")

    regression = False
    if fail_on is not None:
        threshold = _SEVERITY_RANK[fail_on]
        regression = any(
            r.status == "new" and _SEVERITY_RANK[r.severity] >= threshold
            for r in rec_deltas
        )

    return DiffResult(
        new_count=new_count,
        resolved_count=resolved_count,
        persisting_count=persisting_count,
        recommendations=rec_deltas,
        token_metrics=token_delta,
        by_agent_type=agent_deltas,
        baseline_session_count=int(baseline.get("session_count", 0) or 0),
        current_session_count=int(current.get("session_count", 0) or 0),
        fail_on=fail_on,
        regression_detected=regression,
    )


def has_regression(result: DiffResult) -> bool:
    """Convenience accessor for the CLI's exit-code branch."""
    return result.regression_detected


# ---------------------------------------------------------------------------
# Recommendations
# ---------------------------------------------------------------------------


def _diff_recommendations(
    baseline: dict[str, Any],
    current: dict[str, Any],
) -> list[RecommendationDelta]:
    baseline_recs = _index_recommendations(baseline)
    current_recs = _index_recommendations(current)

    deltas: list[RecommendationDelta] = []

    for key, current_rec in current_recs.items():
        baseline_rec = baseline_recs.get(key)
        if baseline_rec is None:
            deltas.append(_make_delta(key, "new", baseline=None, current=current_rec))
        else:
            deltas.append(
                _make_delta(key, "persisting", baseline=baseline_rec, current=current_rec),
            )

    for key, baseline_rec in baseline_recs.items():
        if key not in current_recs:
            deltas.append(_make_delta(key, "resolved", baseline=baseline_rec, current=None))

    deltas.sort(key=_delta_sort_key)
    return deltas


def _index_recommendations(envelope: dict[str, Any]) -> dict[RecKey, dict[str, Any]]:
    diagnostics = envelope.get("diagnostics") or {}
    aggregated = diagnostics.get("aggregated_recommendations") or []
    return {_rec_key(rec): rec for rec in aggregated if isinstance(rec, dict)}


def _rec_key(rec: dict[str, Any]) -> RecKey:
    agent_type = rec.get("agent_type")
    target = str(rec.get("target", ""))
    raw_signals = rec.get("signal_types") or []
    signals = frozenset(_coerce_signal_type(s) for s in raw_signals)
    return (agent_type, target, signals)


def _coerce_signal_type(value: Any) -> SignalType:
    """Recommendations come from JSON, so signal types arrive as strings.

    Unknown values pass through as a synthetic enum member would be ugly;
    instead we coerce by string equality and rely on the source contract
    that aggregated recs only carry valid SignalType values.
    """
    if isinstance(value, SignalType):
        return value
    return SignalType(value)


def _make_delta(
    key: RecKey,
    status: str,
    *,
    baseline: dict[str, Any] | None,
    current: dict[str, Any] | None,
) -> RecommendationDelta:
    agent_type, target, signal_set = key
    sample = current if current is not None else baseline
    assert sample is not None  # one side is always present  # noqa: S101

    baseline_count = int((baseline or {}).get("count", 0) or 0)
    current_count = int((current or {}).get("count", 0) or 0)
    baseline_priority = float((baseline or {}).get("priority_score", 0.0) or 0.0)
    current_priority = float((current or {}).get("priority_score", 0.0) or 0.0)

    severity = _coerce_severity(sample.get("severity"))
    representative = str(sample.get("representative_message", ""))

    return RecommendationDelta(
        status=status,  # type: ignore[arg-type]
        agent_type=agent_type,
        target=target,
        signal_types=sorted(signal_set, key=lambda s: s.value),
        severity=severity,
        representative_message=representative,
        baseline_count=baseline_count,
        current_count=current_count,
        count_delta=current_count - baseline_count,
        baseline_priority_score=baseline_priority,
        current_priority_score=current_priority,
        priority_score_delta=current_priority - baseline_priority,
        is_builtin=bool(sample.get("is_builtin", False)),
    )


def _coerce_severity(value: Any) -> Severity:
    if isinstance(value, Severity):
        return value
    if value is None:
        return Severity.INFO
    return Severity(value)


_STATUS_ORDER = {"new": 0, "persisting": 1, "resolved": 2}


def _delta_sort_key(delta: RecommendationDelta) -> tuple[int, float, str, str]:
    # Within each status group, highest current priority first; ties broken
    # by agent_type then target for stable, human-friendly ordering.
    return (
        _STATUS_ORDER[delta.status],
        -delta.current_priority_score,
        delta.agent_type or "",
        delta.target,
    )


# ---------------------------------------------------------------------------
# Token metrics
# ---------------------------------------------------------------------------


def _diff_token_metrics(
    baseline: dict[str, Any],
    current: dict[str, Any],
) -> TokenMetricsDelta:
    baseline_tm = baseline.get("token_metrics") or {}
    current_tm = current.get("token_metrics") or {}

    baseline_total_tokens = _total_tokens(baseline_tm)
    current_total_tokens = _total_tokens(current_tm)

    baseline_cost = float(baseline_tm.get("total_cost", 0.0) or 0.0)
    current_cost = float(current_tm.get("total_cost", 0.0) or 0.0)

    baseline_cache = float(baseline_tm.get("cache_efficiency", 0.0) or 0.0)
    current_cache = float(current_tm.get("cache_efficiency", 0.0) or 0.0)

    by_model = _diff_by_model(baseline_tm.get("by_model") or {}, current_tm.get("by_model") or {})

    return TokenMetricsDelta(
        baseline_total_tokens=baseline_total_tokens,
        current_total_tokens=current_total_tokens,
        total_tokens_delta=current_total_tokens - baseline_total_tokens,
        baseline_total_cost=baseline_cost,
        current_total_cost=current_cost,
        total_cost_delta=current_cost - baseline_cost,
        baseline_cache_efficiency=baseline_cache,
        current_cache_efficiency=current_cache,
        cache_efficiency_delta=current_cache - baseline_cache,
        by_model=by_model,
    )


def _total_tokens(tm: dict[str, Any]) -> int:
    # ``TokenMetrics.total_tokens`` is a ``@property``, not a serialized
    # field — Pydantic's ``model_dump`` skips it. Reconstruct from the
    # four token components which DO serialize.
    return (
        int(tm.get("input_tokens", 0) or 0)
        + int(tm.get("output_tokens", 0) or 0)
        + int(tm.get("cache_creation_input_tokens", 0) or 0)
        + int(tm.get("cache_read_input_tokens", 0) or 0)
    )


def _model_total_tokens(breakdown: dict[str, Any]) -> int:
    return (
        int(breakdown.get("input_tokens", 0) or 0)
        + int(breakdown.get("output_tokens", 0) or 0)
        + int(breakdown.get("cache_creation_input_tokens", 0) or 0)
        + int(breakdown.get("cache_read_input_tokens", 0) or 0)
    )


def _diff_by_model(
    baseline: dict[str, Any],
    current: dict[str, Any],
) -> list[ModelTokenDelta]:
    models = sorted(set(baseline.keys()) | set(current.keys()))
    deltas: list[ModelTokenDelta] = []
    for model in models:
        b = baseline.get(model) or {}
        c = current.get(model) or {}
        b_tokens = _model_total_tokens(b)
        c_tokens = _model_total_tokens(c)
        b_cost = float(b.get("cost", 0.0) or 0.0)
        c_cost = float(c.get("cost", 0.0) or 0.0)
        deltas.append(
            ModelTokenDelta(
                model=model,
                baseline_total_tokens=b_tokens,
                current_total_tokens=c_tokens,
                total_tokens_delta=c_tokens - b_tokens,
                baseline_cost=b_cost,
                current_cost=c_cost,
                cost_delta=c_cost - b_cost,
            ),
        )
    return deltas


# ---------------------------------------------------------------------------
# Agent metrics
# ---------------------------------------------------------------------------


def _diff_agent_metrics(
    baseline: dict[str, Any],
    current: dict[str, Any],
) -> list[AgentTypeDelta]:
    baseline_by = (baseline.get("agent_metrics") or {}).get("by_agent_type") or {}
    current_by = (current.get("agent_metrics") or {}).get("by_agent_type") or {}

    types = sorted(set(baseline_by.keys()) | set(current_by.keys()))
    deltas: list[AgentTypeDelta] = []
    for agent_type in types:
        b = baseline_by.get(agent_type) or {}
        c = current_by.get(agent_type) or {}

        b_inv = int(b.get("invocation_count", 0) or 0)
        c_inv = int(c.get("invocation_count", 0) or 0)
        b_tok = int(b.get("total_tokens", 0) or 0)
        c_tok = int(c.get("total_tokens", 0) or 0)
        b_cost = float(b.get("estimated_total_cost_usd", 0.0) or 0.0)
        c_cost = float(c.get("estimated_total_cost_usd", 0.0) or 0.0)

        # is_builtin is identity-bound to the agent name; if either side
        # has it set we can trust it.
        is_builtin = bool(c.get("is_builtin", False) or b.get("is_builtin", False))

        deltas.append(
            AgentTypeDelta(
                agent_type=agent_type,
                is_builtin=is_builtin,
                baseline_invocation_count=b_inv,
                current_invocation_count=c_inv,
                invocation_count_delta=c_inv - b_inv,
                baseline_total_tokens=b_tok,
                current_total_tokens=c_tok,
                total_tokens_delta=c_tok - b_tok,
                baseline_estimated_cost_usd=b_cost,
                current_estimated_cost_usd=c_cost,
                estimated_cost_delta_usd=c_cost - b_cost,
            ),
        )
    return deltas
