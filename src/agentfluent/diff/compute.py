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

from agentfluent.analytics.tokens import Origin
from agentfluent.config.models import SEVERITY_RANK, Severity
from agentfluent.diagnostics.models import Axis, SignalType
from agentfluent.diff.models import (
    AgentTypeDelta,
    DeltaStatus,
    DiffResult,
    ModelTokenDelta,
    RecommendationDelta,
    TokenMetricsDelta,
)

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

    new_count = resolved_count = persisting_count = 0
    threshold = SEVERITY_RANK[fail_on] if fail_on is not None else None
    regression = False
    for r in rec_deltas:
        if r.status == "new":
            new_count += 1
            if threshold is not None and SEVERITY_RANK[r.severity] >= threshold:
                regression = True
        elif r.status == "resolved":
            resolved_count += 1
        else:
            persisting_count += 1

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
    status: DeltaStatus,
    *,
    baseline: dict[str, Any] | None,
    current: dict[str, Any] | None,
) -> RecommendationDelta:
    agent_type, target, signal_set = key
    sample = current if current is not None else baseline
    if sample is None:
        msg = "_make_delta requires at least one of baseline or current"
        raise ValueError(msg)

    baseline_count = int((baseline or {}).get("count", 0) or 0)
    current_count = int((current or {}).get("count", 0) or 0)
    baseline_priority = float((baseline or {}).get("priority_score", 0.0) or 0.0)
    current_priority = float((current or {}).get("priority_score", 0.0) or 0.0)

    severity = _coerce_severity(sample.get("severity"))
    representative = str(sample.get("representative_message", ""))

    # Pre-v0.6 envelopes lack ``primary_axis``; ``None`` propagation keeps
    # ``axis_shifted`` quiet when one side has no attribution at all.
    baseline_axis = _coerce_axis((baseline or {}).get("primary_axis"))
    current_axis = _coerce_axis((current or {}).get("primary_axis"))

    return RecommendationDelta(
        status=status,
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
        baseline_primary_axis=baseline_axis,
        current_primary_axis=current_axis,
    )


def _coerce_axis(value: Any) -> Axis | None:
    """Normalize a JSON ``primary_axis`` value to an :class:`Axis` member.

    Returns ``None`` for missing, blank, or unrecognized values so legacy
    envelopes and forward-compat axes don't crash the diff.
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        return Axis(value)
    except ValueError:
        return None


def _coerce_severity(value: Any) -> Severity:
    if isinstance(value, Severity):
        return value
    if value is None:
        return Severity.INFO
    return Severity(value)


_STATUS_ORDER = {"new": 0, "persisting": 1, "resolved": 2}


def _delta_sort_key(delta: RecommendationDelta) -> tuple[int, float, str, str]:
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

    baseline_total_tokens = _sum_token_components(baseline_tm)
    current_total_tokens = _sum_token_components(current_tm)

    baseline_cost = float(baseline_tm.get("total_cost", 0.0) or 0.0)
    current_cost = float(current_tm.get("total_cost", 0.0) or 0.0)

    baseline_cache = float(baseline_tm.get("cache_efficiency", 0.0) or 0.0)
    current_cache = float(current_tm.get("cache_efficiency", 0.0) or 0.0)

    by_model = _diff_by_model(
        baseline_tm.get("by_model") or [],
        current_tm.get("by_model") or [],
    )

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


def _sum_token_components(d: dict[str, Any]) -> int:
    # ``TokenMetrics.total_tokens`` and ``ModelTokenBreakdown.total_tokens``
    # are ``@property`` accessors; Pydantic's ``model_dump`` skips them.
    # Reconstruct from the four serialized fields shared by both shapes.
    return (
        int(d.get("input_tokens", 0) or 0)
        + int(d.get("output_tokens", 0) or 0)
        + int(d.get("cache_creation_input_tokens", 0) or 0)
        + int(d.get("cache_read_input_tokens", 0) or 0)
    )


def _normalize_by_model(
    raw: dict[str, Any] | list[Any],
) -> dict[tuple[str, Origin], dict[str, Any]]:
    """Coerce a ``by_model`` payload to ``{(model, origin): row}``.

    Schema v2 (#227) emits a list of rows with an ``origin`` field. The
    pre-#227 schema (v1) emitted a dict keyed by model name with no
    ``origin`` field; for cross-version diffs we treat those rows as
    parent-origin so legacy saved envelopes remain comparable. Rows
    without a ``model`` field, or with an unknown ``origin`` value, are
    treated defensively (skip / default to parent) — a user-edited JSON
    file should not crash the diff.
    """
    if isinstance(raw, dict):
        return {
            (model, "parent"): (row if isinstance(row, dict) else {})
            for model, row in raw.items()
        }
    out: dict[tuple[str, Origin], dict[str, Any]] = {}
    for row in raw:
        if not isinstance(row, dict):
            continue
        model = row.get("model")
        if not isinstance(model, str):
            continue
        raw_origin = row.get("origin", "parent")
        origin: Origin = raw_origin if raw_origin in ("parent", "subagent") else "parent"
        out[(model, origin)] = row
    return out


def _diff_by_model(
    baseline: dict[str, Any] | list[Any],
    current: dict[str, Any] | list[Any],
) -> list[ModelTokenDelta]:
    baseline_map = _normalize_by_model(baseline)
    current_map = _normalize_by_model(current)
    keys = sorted(set(baseline_map.keys()) | set(current_map.keys()))
    deltas: list[ModelTokenDelta] = []
    for model, origin in keys:
        b = baseline_map.get((model, origin)) or {}
        c = current_map.get((model, origin)) or {}
        b_tokens = _sum_token_components(b)
        c_tokens = _sum_token_components(c)
        b_cost = float(b.get("cost", 0.0) or 0.0)
        c_cost = float(c.get("cost", 0.0) or 0.0)
        deltas.append(
            ModelTokenDelta(
                model=model,
                origin=origin,
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
