"""Typed output of :func:`agentfluent.diff.compute.compute_diff`.

Pydantic models so the CLI's JSON renderer, the v0.6 markdown report
(#198), and a future webapp can all consume the same shape. Empty lists
default for forward-compat — adding offload/delegation diff sections in
v0.6 is additive.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from agentfluent.config.models import Severity
from agentfluent.diagnostics.models import SignalType

DeltaStatus = Literal["new", "resolved", "persisting"]


class RecommendationDelta(BaseModel):
    """One row in the recommendations diff.

    Keyed by ``(agent_type, target, frozenset(signal_types))`` — the same
    grouping ``diagnostics.aggregation`` uses for ``AggregatedRecommendation``.
    For ``status='persisting'``, ``count_delta`` and ``priority_score_delta``
    capture how the same finding shifted between runs.
    """

    status: DeltaStatus
    agent_type: str | None
    target: str
    signal_types: list[SignalType] = Field(default_factory=list)
    """Sorted alphabetically for stable JSON output (the underlying join
    uses a frozenset, but lists serialize cleanly)."""

    severity: Severity
    """For ``new``/``persisting`` rows this is the current severity; for
    ``resolved`` rows it's the baseline severity (the side that has the
    rec)."""

    representative_message: str = ""

    baseline_count: int = 0
    current_count: int = 0
    count_delta: int = 0
    """``current_count - baseline_count``. Negative for ``resolved`` rows;
    positive for ``new``; can be either sign for ``persisting``."""

    baseline_priority_score: float = 0.0
    current_priority_score: float = 0.0
    priority_score_delta: float = 0.0
    """``current - baseline``. Surfaced in v0.5 output (architect review,
    #199) so the v0.6 ``--fail-on priority-regression`` mode doesn't
    require a schema change."""

    is_builtin: bool = False


class ModelTokenDelta(BaseModel):
    """Per-(model, origin) token / cost delta inside :class:`TokenMetricsDelta`.

    ``origin`` distinguishes parent vs subagent rows (#227). Defaults
    to ``"parent"`` so legacy v1 envelopes (which had no origin field)
    diff cleanly under the compatibility shim in
    :func:`agentfluent.diff.compute._diff_by_model`.
    """

    model: str
    origin: str = "parent"
    baseline_total_tokens: int = 0
    current_total_tokens: int = 0
    total_tokens_delta: int = 0

    baseline_cost: float = 0.0
    current_cost: float = 0.0
    cost_delta: float = 0.0


class TokenMetricsDelta(BaseModel):
    """Session-level token / cost / cache deltas."""

    baseline_total_tokens: int = 0
    current_total_tokens: int = 0
    total_tokens_delta: int = 0

    baseline_total_cost: float = 0.0
    current_total_cost: float = 0.0
    total_cost_delta: float = 0.0

    baseline_cache_efficiency: float = 0.0
    current_cache_efficiency: float = 0.0
    cache_efficiency_delta: float = 0.0

    by_model: list[ModelTokenDelta] = Field(default_factory=list)
    """One entry per model that appears in either baseline or current. A
    model present on only one side has zero on the missing side."""


class AgentTypeDelta(BaseModel):
    """Per-agent-type invocation / token / cost delta."""

    agent_type: str
    is_builtin: bool = False

    baseline_invocation_count: int = 0
    current_invocation_count: int = 0
    invocation_count_delta: int = 0

    baseline_total_tokens: int = 0
    current_total_tokens: int = 0
    total_tokens_delta: int = 0

    baseline_estimated_cost_usd: float = 0.0
    current_estimated_cost_usd: float = 0.0
    estimated_cost_delta_usd: float = 0.0


class DiffResult(BaseModel):
    """Complete diff output. JSON envelope wraps ``model_dump(mode='json')``.

    Counts on the top level summarize the recommendations section so a CI
    consumer can branch on totals without walking the per-row list.
    """

    new_count: int = 0
    resolved_count: int = 0
    persisting_count: int = 0

    recommendations: list[RecommendationDelta] = Field(default_factory=list)
    """All deltas in a stable order: new (priority desc), resolved
    (priority desc), persisting (priority desc). Frontends can re-sort."""

    token_metrics: TokenMetricsDelta = Field(default_factory=TokenMetricsDelta)
    by_agent_type: list[AgentTypeDelta] = Field(default_factory=list)

    baseline_session_count: int = 0
    current_session_count: int = 0

    fail_on: Severity | None = None
    """The severity threshold the diff was evaluated against (``None``
    means regression check disabled)."""

    regression_detected: bool = False
    """``True`` iff at least one ``new`` recommendation has severity
    >= ``fail_on``. Persisting-rec ``priority_score`` increases do NOT
    count in v0.5 (deferred to v0.6 per PRD)."""
