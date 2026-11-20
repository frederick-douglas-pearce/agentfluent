"""``cost_coverage``: what a run's dollar figures could and could not price (#676, #688).

One run-level block, measured from the run itself (never a static claim):

- ``pricing`` -- tokens whose model had no price at the session's price date
  (``ModelTokenBreakdown.unpriced_tokens``), named by model id. Those tokens are
  in every token count and read ``$0`` in every cost, so the block is what keeps
  an unpriced id from reading as free.
- ``invocations`` -- agent invocations with a linked trace, with trace spend, and
  with a priced trace model (``AgentTypeMetrics`` counts, ``priced <= spend <=
  linked <= total``). Per-agent cost covers ``priced`` of ``total``.
- ``diagnostics`` -- dollar figures in diagnostics: model-switch savings
  (``MODEL_MISMATCH``) and offload candidates; how many cover fewer than all their
  invocations (the per-figure "k of N" notes, #675) and how many have no estimate
  because pricing was missing. ``None`` when diagnostics did not run.

Counts only, no stored percentages, so a consumer can recompute any ratio
exactly. The #648 trace-file cohorts stay in the sibling ``trace_coverage`` key
and are not duplicated here.

**These field names are public JSON API** (D055). Envelope stays "2" (D029): the
change is additive. ``AnalysisResult.cost_coverage`` is ``None`` for an envelope
written before v0.12.0 -- every input here first ships in v0.12.0, so such an
envelope reads as *unknown*, never as full coverage.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from pydantic import BaseModel, Field

from agentfluent.analytics.agent_metrics import AgentTypeMetrics
from agentfluent.analytics.tokens import ModelTokenBreakdown
from agentfluent.config.models import EnvironmentWarning, Severity
from agentfluent.diagnostics.model_routing import SAVINGS_USD_KEY, savings_is_partial
from agentfluent.diagnostics.models import DiagnosticsResult, SignalType

PRICING_GAP_WARNING_CODE = "unpriced_tokens"


class PricingCoverage(BaseModel):
    """Tokens in the run, and those whose model had no price."""

    tokens: int = 0
    """All tokens in the run's ``token_metrics`` (parent and subagent rows)."""

    unpriced_tokens: int = 0
    """Tokens counted but not priced: in ``tokens``, not in any cost."""

    unpriced_models: dict[str, int] = Field(default_factory=dict)
    """Model id -> its unpriced tokens. Only ids with unpriced tokens appear.
    Date-aware pricing (#546) can price an id in one session and not another,
    so an id here may also have priced tokens."""


class InvocationCoverage(BaseModel):
    """Agent invocations, and how many per-agent cost covers."""

    total: int = 0
    """All agent invocations in ``agent_metrics``."""

    linked: int = 0
    """Invocations with a linked subagent trace. The rest have unknown spend."""

    spend: int = 0
    """Linked invocations whose trace counts as spend (has a real model)."""

    priced: int = 0
    """Spend invocations whose trace model had a price -- what per-agent cost covers."""


class DiagnosticsCoverage(BaseModel):
    """Dollar figures in diagnostics and how complete each is."""

    savings_figures: int = 0
    """Dollar figures diagnostics published: model-switch savings with an estimate,
    plus offload candidates whose cost was priced."""

    partial_figures: int = 0
    """Model-switch savings priced over fewer than all the signal's invocations."""

    unpriced_figures: int = 0
    """Figures with no estimate because pricing was missing: overspec model-switch
    signals without savings, and offload candidates priced as unavailable.
    Underspec signals carry no savings by design and are not counted."""


class CostCoverage(BaseModel):
    """What the run's dollar figures cover (#676, #688)."""

    pricing: PricingCoverage = Field(default_factory=PricingCoverage)
    invocations: InvocationCoverage = Field(default_factory=InvocationCoverage)
    diagnostics: DiagnosticsCoverage | None = None


def _pricing_coverage(rows: Iterable[ModelTokenBreakdown]) -> PricingCoverage:
    coverage = PricingCoverage()
    for row in rows:
        coverage.tokens += row.total_tokens
        if row.unpriced_tokens:
            coverage.unpriced_tokens += row.unpriced_tokens
            coverage.unpriced_models[row.model] = (
                coverage.unpriced_models.get(row.model, 0) + row.unpriced_tokens
            )
    coverage.unpriced_models = dict(sorted(coverage.unpriced_models.items()))
    return coverage


def _invocation_coverage(by_type: Mapping[str, AgentTypeMetrics]) -> InvocationCoverage:
    coverage = InvocationCoverage()
    for metrics in by_type.values():
        coverage.total += metrics.invocation_count
        coverage.linked += metrics.linked_invocation_count
        coverage.spend += metrics.spend_invocation_count
        coverage.priced += metrics.priced_invocation_count
    return coverage


def _diagnostics_coverage(diagnostics: DiagnosticsResult) -> DiagnosticsCoverage:
    coverage = DiagnosticsCoverage()
    for signal in diagnostics.signals:
        if signal.signal_type != SignalType.MODEL_MISMATCH:
            continue
        detail = signal.detail
        if detail.get(SAVINGS_USD_KEY) is not None:
            coverage.savings_figures += 1
            if savings_is_partial(detail):
                coverage.partial_figures += 1
        elif detail.get("mismatch_type") == "overspec":
            coverage.unpriced_figures += 1
    for candidate in diagnostics.offload_candidates:
        if candidate.cost_priced is False:
            coverage.unpriced_figures += 1
        elif candidate.cost_priced:
            coverage.savings_figures += 1
    return coverage


def compute_cost_coverage(
    by_model: Iterable[ModelTokenBreakdown],
    by_agent_type: Mapping[str, AgentTypeMetrics],
    diagnostics: DiagnosticsResult | None,
) -> CostCoverage:
    """Measure the run's cost coverage from its own metrics."""
    return CostCoverage(
        pricing=_pricing_coverage(by_model),
        invocations=_invocation_coverage(by_agent_type),
        diagnostics=_diagnostics_coverage(diagnostics) if diagnostics is not None else None,
    )


def pricing_gap_warning(coverage: CostCoverage | None) -> EnvironmentWarning | None:
    """An environment warning when any token went unpriced, so ``warnings[]`` is
    never empty on a run whose totals read low because a model had no price."""
    if coverage is None or not coverage.pricing.unpriced_tokens:
        return None
    ids = ", ".join(coverage.pricing.unpriced_models)
    return EnvironmentWarning(
        code=PRICING_GAP_WARNING_CODE,
        severity=Severity.WARNING,
        message=(
            f"{coverage.pricing.unpriced_tokens:,} of {coverage.pricing.tokens:,} tokens "
            f"have no price ({ids}); dollar figures exclude them. Upgrading "
            "agentfluent usually adds the missing prices."
        ),
    )


__all__ = [
    "PRICING_GAP_WARNING_CODE",
    "CostCoverage",
    "DiagnosticsCoverage",
    "InvocationCoverage",
    "PricingCoverage",
    "compute_cost_coverage",
    "pricing_gap_warning",
]
