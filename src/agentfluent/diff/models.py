"""Typed output of :func:`agentfluent.diff.compute.compute_diff`.

Pydantic models so the CLI's JSON renderer, the v0.6 markdown report
(#198), and a future webapp can all consume the same shape. Empty lists
default for forward-compat — adding offload/delegation diff sections in
v0.6 is additive.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, computed_field

from agentfluent.analytics.cost_coverage import CostCoverage
from agentfluent.analytics.cost_model import CostModelStamp
from agentfluent.analytics.tokens import Origin
from agentfluent.config.models import Severity
from agentfluent.core.filtering import WindowMetadata
from agentfluent.diagnostics.models import Axis, SignalType
from agentfluent.diff.cost_coverage import (
    CostCoverageStatus,
    cost_coverage_status,
    side_coverage_status,
)
from agentfluent.diff.cost_model import (
    CostModelChange,
    CostModelStatus,
    cost_model_changes,
    cost_model_status,
)

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

    baseline_primary_axis: Axis | None = None
    """``None`` for ``status='new'`` rows and for legacy pre-v0.6
    envelopes that lack the field. Defaulting to ``None`` rather than
    ``Axis.COST`` keeps ``axis_shifted`` quiet when one side has no
    attribution at all."""

    current_primary_axis: Axis | None = None
    """``None`` for ``status='resolved'`` rows."""

    # mypy + pydantic ``@computed_field`` interaction (pydantic/pydantic#6709).
    @computed_field  # type: ignore[prop-decorator]
    @property
    def axis_shifted(self) -> bool:
        """``True`` iff baseline and current both have a non-``None``
        ``primary_axis`` and the values differ."""
        if self.baseline_primary_axis is None or self.current_primary_axis is None:
            return False
        return self.baseline_primary_axis != self.current_primary_axis


class ModelTokenDelta(BaseModel):
    """Per-(model, origin) token / cost delta inside :class:`TokenMetricsDelta`.

    ``origin`` distinguishes parent vs subagent rows (#227). Defaults
    to ``"parent"`` so legacy v1 envelopes (which had no origin field)
    diff cleanly under the compatibility shim in
    :func:`agentfluent.diff.compute._diff_by_model`.
    """

    model: str
    origin: Origin = "parent"
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

    baseline_model_turns: int = 0
    current_model_turns: int = 0
    model_turns_delta: int = 0
    """Parent-session model-turn delta (#470). Sourced from the envelope's
    top-level ``total_model_turns`` (#465), not from the nested
    ``token_metrics`` block — it lives here because ``TokenMetricsDelta``
    already holds session-shape metrics. Pre-#465 envelopes lack the field
    and fall back to ``0`` on both sides (delta ``0``)."""

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
    """Baseline ``toolUseResult.totalTokens`` rollup -- a context-size proxy,
    not spend (D057(1)), absent on most current invocations (D062(4)). Kept
    with its original meaning because it is public JSON (D055); not rendered
    in the Per-Agent Deltas table, whose Tokens Δ shows spend (#693)."""
    current_total_tokens: int = 0
    """Current-side rollup; see ``baseline_total_tokens``."""
    total_tokens_delta: int = 0
    """``current_total_tokens - baseline_total_tokens`` (rollup, JSON only)."""

    baseline_spend_tokens: int | None = None
    """Baseline trace-sourced spend (``AgentTypeMetrics.spend_tokens``,
    #646). ``None`` means **unknown, never 0**: the side's envelope predates
    #646 (no ``total_agent_spend_tokens``), or the agent type's entry on that
    side has no trace-covered invocation (``spend_invocation_count == 0``, no
    ``spend_tokens`` key, or a null entry). An agent type with no entry on a
    spend-carrying side is a measured ``0`` there."""
    current_spend_tokens: int | None = None
    """Current-side spend; same ``None`` contract as ``baseline_spend_tokens``."""

    spend_tokens_delta: int | None = None
    """``current_spend_tokens - baseline_spend_tokens``; ``None`` iff either
    side is ``None`` (#693)."""

    baseline_estimated_cost_usd: float = 0.0
    current_estimated_cost_usd: float = 0.0
    estimated_cost_delta_usd: float = 0.0

    baseline_total_model_turns: int = 0
    current_total_model_turns: int = 0
    total_model_turns_delta: int = 0
    """Per-agent-type model-turn delta (#470), summed across this type's
    invocations (#467). Pre-#467 envelopes lack the field and fall back to
    ``0`` on both sides."""

    baseline_invocations_with_turns: int = 0
    current_invocations_with_turns: int = 0
    """Denominator for avg-turns-per-invocation, stored (not averaged) so
    the diff renderer derives the avg the same way ``analyze`` does (#467),
    keeping the two outputs from diverging. No precomputed avg-delta field
    exists by design."""

    @property
    def has_change(self) -> bool:
        """``True`` iff the invocation, spend, cost or total-model-turns delta
        is nonzero.

        Centralizes the "is this row worth showing" policy for the diff
        renderer's zero-row filter. The aim is for the predicate to track the
        table's *rendered* columns: the rollup ``total_tokens_delta`` is
        deliberately excluded because Tokens Δ renders ``spend_tokens_delta``
        (#693) -- a rollup-only change would otherwise show a row whose
        visible cells all read zero. One known exception remains:
        ``total_model_turns_delta`` still counts although the table renders
        an avg-turns Δ, so a row can pass with every visible cell at zero
        (#711). A plain ``@property`` (not a ``computed_field``) so it stays
        out of the JSON envelope."""
        return bool(
            self.invocation_count_delta
            or self.spend_tokens_delta
            or self.estimated_cost_delta_usd
            or self.total_model_turns_delta,
        )


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

    baseline_window: WindowMetadata | None = None
    current_window: WindowMetadata | None = None
    """Time-filter window metadata copied from each input envelope (#342).
    ``None`` for unfiltered runs and for legacy envelopes that predate
    ``analyze --since/--until`` (#316). Renderers print
    ``(window not recorded)`` in the latter case."""

    baseline_diagnostics_version: str | None = None
    current_diagnostics_version: str | None = None
    """Package version that produced each input envelope (#347). ``None``
    for envelopes that predate v0.7. ``diff`` warns when both are present
    and differ — signal counts may not be directly comparable across
    rule-set / calibration changes."""

    @computed_field  # type: ignore[prop-decorator]
    @property
    def diagnostics_version_drift(self) -> bool:
        """``True`` iff both versions are present and differ.

        ``None`` on either side does not count as drift — the warning copy
        differentiates "unknown" from "mismatched" so a v0.6 baseline
        being re-diffed against a v0.7 current doesn't fire a false alarm
        about regressions in detector sensitivity it can't actually
        attest to."""
        baseline = self.baseline_diagnostics_version
        current = self.current_diagnostics_version
        if baseline is None or current is None:
            return False
        return baseline != current

    baseline_cost_model: CostModelStamp | None = None
    current_cost_model: CostModelStamp | None = None
    """Cost-model stamp copied from each input envelope (#543): the genai-prices
    version and cost-method version that priced its dollar figures. ``None`` when
    the envelope has no readable stamp: absent (envelopes from before v0.12.0),
    null, or malformed."""

    @computed_field  # type: ignore[prop-decorator]
    @property
    def cost_model_status(self) -> CostModelStatus:
        """``match`` / ``mismatch`` / ``unknown`` -- whether both sides' dollar
        figures come from the same cost model (rules in
        :func:`agentfluent.diff.cost_model.cost_model_status`). Unlike
        ``diagnostics_version_drift``, a side with no readable stamp is ``unknown``
        and raises the cost caveat, even when both sides lack one. Never affects
        ``regression_detected``."""
        return cost_model_status(
            self.baseline_cost_model,
            self.current_cost_model,
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def cost_model_changes(self) -> list[CostModelChange]:
        """Which stamp components differ between the two sides
        (``pricing_table`` and/or ``cost_method``)."""
        return cost_model_changes(self.baseline_cost_model, self.current_cost_model)

    baseline_cost_coverage: CostCoverage | None = None
    current_cost_coverage: CostCoverage | None = None
    """``cost_coverage`` block copied from each input envelope (#713): what its
    dollar figures could and could not price. ``None`` when the envelope has no
    readable block: absent (envelopes from before v0.12.0), null, or malformed
    (including one missing its ``pricing`` or ``invocations`` object)."""

    baseline_unparseable_trace_files: int | None = None
    current_unparseable_trace_files: int | None = None
    """Subagent trace files each envelope could not parse
    (``trace_coverage.unparseable.files``, #648); their spend is in no total.
    ``None`` when the envelope has no readable ``trace_coverage`` block."""

    @computed_field  # type: ignore[prop-decorator]
    @property
    def baseline_cost_coverage_status(self) -> CostCoverageStatus:
        """``full`` / ``incomplete`` / ``unknown`` for the baseline side (rules in
        :func:`agentfluent.diff.cost_coverage.side_coverage_status`)."""
        return side_coverage_status(
            self.baseline_cost_coverage, self.baseline_unparseable_trace_files,
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def current_cost_coverage_status(self) -> CostCoverageStatus:
        """``full`` / ``incomplete`` / ``unknown`` for the current side."""
        return side_coverage_status(
            self.current_cost_coverage, self.current_unparseable_trace_files,
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def cost_coverage_status(self) -> CostCoverageStatus:
        """Whether both sides' cost figures cover everything they count:
        ``unknown`` if either side is unknown, else ``incomplete`` if either side
        is, else ``full``. Scope is the figures ``diff`` compares -- totals,
        by-model and per-agent cost; diagnostics coverage is excluded, so a
        snapshot can read ``full`` here and ``incomplete`` in the ``analyze``
        footer. Never affects ``regression_detected``."""
        return cost_coverage_status(
            self.baseline_cost_coverage_status, self.current_cost_coverage_status,
        )

    fail_on: Severity | None = None
    """The severity threshold the diff was evaluated against (``None``
    means regression check disabled)."""

    regression_detected: bool = False
    """``True`` iff at least one ``new`` recommendation has severity
    >= ``fail_on``. Persisting-rec ``priority_score`` increases do NOT
    count in v0.5 (deferred to v0.6 per PRD)."""
