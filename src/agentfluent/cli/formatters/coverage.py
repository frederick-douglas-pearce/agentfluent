"""The cost-coverage footer (#676, #688): one text model for every renderer.

``analyze`` (table) and ``report`` (Markdown) both call ``cost_coverage_footer``
on the *serialized* ``cost_coverage`` and ``trace_coverage`` dicts -- in-memory
``model_dump()`` and rehydrated JSON alike -- so the two cannot drift. Each
renderer only chooses styling.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from agentfluent.cli.formatters.helpers import format_tokens, trace_coverage_line

CoverageStatus = Literal["full", "incomplete", "unknown"]

UNKNOWN_HEADLINE = "Cost coverage: unknown (envelope predates v0.12.0)."
INCOMPLETE_HEADLINE = "Cost coverage: INCOMPLETE. Dollar figures leave out the following:"


@dataclass
class CoverageFooter:
    """What the footer says. ``clauses`` is non-empty only when incomplete."""

    status: CoverageStatus
    headline: str
    clauses: list[str] = field(default_factory=list)
    attribution: str | None = None
    """#648's trace-cohort line, rendered quietly whatever the status. The
    folded cohorts it names (nested, session-spawned) are in the totals, so
    they are not a gap; unparseable trace files are, and the incomplete form
    reports them as a clause of their own."""


def _count(block: Mapping[str, Any] | None, key: str) -> int:
    value = (block or {}).get(key, 0)
    return value if isinstance(value, int) else 0


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _percent(part: int, whole: int) -> str:
    """``part`` as a share of ``whole``, one decimal; never rounds a gap to 0% or 100%."""
    if whole <= 0:
        return "0%"
    share = 100 * part / whole
    if part == whole:
        return "100%"
    if part == 0:
        return "0%"
    return f"{min(max(share, 0.1), 99.9):.1f}%"


def _pricing_clause(pricing: Mapping[str, Any]) -> str | None:
    unpriced = _count(pricing, "unpriced_tokens")
    if not unpriced:
        return None
    models = pricing.get("unpriced_models") or {}
    named = ", ".join(
        f"{model_id} ({format_tokens(tokens)})"
        for model_id, tokens in models.items()
        if isinstance(tokens, int)
    )
    tokens = _count(pricing, "tokens")
    # The priced share, the same metric the full headline shows "(100%)" of.
    return (
        f"Pricing: {_percent(tokens - unpriced, tokens)} of tokens priced; "
        f"{format_tokens(unpriced)} of {format_tokens(tokens)} tokens have no price "
        f"and count as $0. Unpriced tokens by model: {named}."
    )


def _invocation_clause(invocations: Mapping[str, Any]) -> str | None:
    """A gap is an invocation whose spend is unknown (no linked trace) or whose
    spend went unpriced. A linked trace with no model response spent nothing, so
    it is not a gap; it is still named when a gap is reported."""
    total = _count(invocations, "total")
    linked = _count(invocations, "linked")
    spend = _count(invocations, "spend")
    priced = _count(invocations, "priced")
    if linked >= total and priced >= spend:
        return None
    reasons = []
    if total > linked:
        reasons.append(f"{total - linked:,} with no linked trace (spend unknown)")
    if spend > priced:
        reasons.append(f"{spend - priced:,} on an unpriced model")
    if linked > spend:
        reasons.append(f"{linked - spend:,} whose trace has no model response (no spend)")
    return (
        f"Agent cost: per-agent cost covers {priced:,} of {total:,} agent invocations; "
        + "; ".join(reasons) + "."
    )


def _invocation_summary(invocations: Mapping[str, Any]) -> str:
    """The full-coverage wording for agent invocations."""
    total = _count(invocations, "total")
    spend = _count(invocations, "spend")
    if not total:
        return "no agent invocations"
    if spend >= total:
        return f"{total:,} of {total:,} agent invocations linked and trace-priced"
    return (
        f"{total:,} of {total:,} agent invocations linked, {spend:,} trace-priced "
        f"({total - spend:,} with no model response, so no spend)"
    )


def _diagnostics_clause(diagnostics: Mapping[str, Any] | None) -> str | None:
    partial = _count(diagnostics, "partial_figures")
    unpriced = _count(diagnostics, "unpriced_figures")
    parts = []
    if partial:
        parts.append(
            f"{_plural(partial, 'savings figure')} cover only some of their invocations "
            '(see the "k of N" note on each)'
        )
    if unpriced:
        parts.append(
            f"{_plural(unpriced, 'estimate')} unavailable (a model involved has no "
            "price, or the invocations behind the figure have no trace spend)"
        )
    if not parts:
        return None
    return "Diagnostics: " + "; ".join(parts) + "."


def _unparseable_clause(trace_coverage: Mapping[str, Any] | None) -> str | None:
    files = _count((trace_coverage or {}).get("unparseable"), "files")
    if not files:
        return None
    return (
        f"Subagent traces: {_plural(files, 'trace file')} could not be parsed; "
        "their spend is unknown and not in any total."
    )


def cost_coverage_footer(
    cost_coverage: Mapping[str, Any] | None,
    trace_coverage: Mapping[str, Any] | None,
) -> CoverageFooter:
    """Build the footer from the serialized coverage dicts.

    A missing or null ``cost_coverage`` is an envelope from before v0.12.0 and
    reads *unknown*, never *full*.
    """
    attribution = trace_coverage_line(trace_coverage)
    if not cost_coverage:
        return CoverageFooter("unknown", UNKNOWN_HEADLINE, attribution=attribution)

    pricing = cost_coverage.get("pricing") or {}
    invocations = cost_coverage.get("invocations") or {}
    clauses = [
        clause
        for clause in (
            _pricing_clause(pricing),
            _invocation_clause(invocations),
            _diagnostics_clause(cost_coverage.get("diagnostics")),
            _unparseable_clause(trace_coverage),
        )
        if clause is not None
    ]
    if clauses:
        return CoverageFooter("incomplete", INCOMPLETE_HEADLINE, clauses, attribution)

    headline = (
        f"Cost coverage: full. All {format_tokens(_count(pricing, 'tokens'))} tokens "
        f"(100%) priced; {_invocation_summary(invocations)}."
    )
    return CoverageFooter("full", headline, attribution=attribution)
