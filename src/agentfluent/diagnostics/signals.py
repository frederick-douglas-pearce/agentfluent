"""Behavior signal extraction from agent invocations.

Detects error patterns in output text, token consumption outliers,
and duration outliers.
"""

from __future__ import annotations

import re
import statistics
from collections import defaultdict
from collections.abc import Callable, Iterator

from agentfluent.agents.models import AgentInvocation
from agentfluent.config.models import Severity
from agentfluent.diagnostics.models import DiagnosticSignal, SignalType

# Error keywords and their severity. "blocked"/"permission denied" are critical;
# others are warnings.
ERROR_PATTERNS: list[tuple[str, Severity]] = [
    ("blocked", Severity.CRITICAL),
    ("permission denied", Severity.CRITICAL),
    ("don't have access", Severity.CRITICAL),
    ("unable to", Severity.WARNING),
    ("failed", Severity.WARNING),
    ("error", Severity.WARNING),
    ("retry", Severity.WARNING),
    ("not found", Severity.WARNING),
    ("timed out", Severity.WARNING),
]

# Compiled pattern for efficient matching. Public so other modules
# (e.g., traces.parser) reuse the same regex instead of recompiling from
# ERROR_PATTERNS.
ERROR_REGEX = re.compile(
    "|".join(re.escape(kw) for kw, _ in ERROR_PATTERNS),
    re.IGNORECASE,
)

# Real error messages lead with the indicator ("Error: ...", "Permission
# denied", "Failed to ..."). Bounding the regex prevents successful
# results that mention error keywords mid-text — GitHub issue bodies,
# Playwright snapshots, file Reads — from synthesizing is_error=True.
# Shared between traces.parser._detect_is_error and
# mcp_assessment.extract_mcp_calls_from_messages (#241).
ERROR_DETECTION_WINDOW_CHARS = 200


def detect_is_error_from_text(text: str | None) -> bool:
    """Synthesize ``is_error`` from result text by matching the leading window.

    Used as a fallback when the upstream tool result has no explicit
    ``is_error`` boolean. The leading-window bound is the FP defense:
    it keeps long, successful results (issue bodies, web fetches, file
    contents) from being flagged just because an error keyword appears
    deep in the body. ``None`` and empty strings always return False.
    """
    if not text:
        return False
    return bool(ERROR_REGEX.search(text[:ERROR_DETECTION_WINDOW_CHARS]))


# File-reading tools whose *successful* output is file/source content — so a
# leading-window keyword match is a false positive (#580): the head of a Read is
# the top of the file, and a Grep hit line is `path:line:content`. On a
# self-referential corpus (agents reading AgentFluent's own error-handling
# source) that head IS error vocabulary, which the generic windowed
# ``detect_is_error_from_text`` misreads as a real error.
FILE_READING_TOOLS = frozenset({"Read", "Grep", "Glob"})

# Structured error signatures a genuine Read/Grep/Glob failure *leads* with.
# Case-sensitive on purpose: these are literal Claude Code / Node system strings
# (lowercase ``<tool_use_error>`` wrapper, uppercase errno codes) — matching them
# case-insensitively would re-admit the very source-content FPs this guards. The
# v0.10 dogfood confirmed all 15 genuine fires begin with one of these while all
# 10 FPs carry the keyword mid-line (#580; analysis.md). ``ENOENT``/``EACCES``
# are included defensively alongside the observed ``EISDIR`` at zero FP cost.
LEADING_ERROR_REGEX = re.compile(
    r"<tool_use_error>"
    r"|EISDIR|ENOENT|EACCES"
    r"|File does not exist"
    r"|File content .*? exceeds maximum",
)


def detect_is_error_for_tool(text: str | None, tool_name: str) -> bool:
    """Synthesize ``is_error`` with tool-aware anchoring (#580).

    For file-reading tools (``FILE_READING_TOOLS``) a successful result is
    file/source content, so only a result that *begins* with a structured
    error signature (after leading whitespace) counts as an error — merely
    *containing* error vocabulary in the leading window does not. All other
    tools keep the unchanged windowed behavior of
    ``detect_is_error_from_text`` (preserving the #241 mcp_assessment path).
    ``None``/empty always return False.
    """
    if not text:
        return False
    if tool_name in FILE_READING_TOOLS:
        return bool(LEADING_ERROR_REGEX.match(text.lstrip()))
    return detect_is_error_from_text(text)


def iter_error_matches(
    text: str | None,
    *,
    window: int = ERROR_DETECTION_WINDOW_CHARS,
) -> Iterator[re.Match[str]]:
    """Yield ``ERROR_REGEX`` matches from the bounded leading window of ``text``.

    Shared shape consumed by ``compute_error_rate`` (count) and
    ``_extract_error_signals`` (iterate for snippet extraction). The
    bound is the FP defense — agent ``output_text`` runs thousands of
    chars and frequently discusses error-handling code as a topic,
    inflating an unbounded ``findall`` count. #281 dogfood research:
    98% of full-text matches across 447 invocations were mid-text
    code identifiers (``tool_error_sequence``, ``RetrySequence``) and
    schema field mentions (``is_error?``), not error events.

    Match positions are within the sliced prefix and therefore valid
    indices into the original text — callers extracting snippets via
    ``match.start()`` / ``match.end()`` get correct offsets without
    further translation.
    """
    if not text:
        return
    yield from ERROR_REGEX.finditer(text[:window])


# Map matched text back to severity
_KEYWORD_SEVERITY: dict[str, Severity] = {kw.lower(): sev for kw, sev in ERROR_PATTERNS}

OUTLIER_IQR_MULTIPLIER = 1.5
"""Tukey-style IQR multiplier: ``threshold = Q3 + k * IQR``. Calibrated
in scripts/calibration/threshold_validation.ipynb §10."""

OUTLIER_MIN_SAMPLE = 4
"""Minimum invocations per agent type before IQR detection runs.
``statistics.quantiles(n=4)`` requires ≥ 4 data points; below that,
Q3/IQR aren't computable. Larger samples produce more stable estimates
but the absolute floor is set here."""


def _extract_error_signals(invocations: list[AgentInvocation]) -> list[DiagnosticSignal]:
    """Detect error patterns in agent output text.

    Traced invocations are skipped: trace-level signals
    (``TOOL_ERROR_SEQUENCE``, ``RETRY_LOOP``, ``PERMISSION_FAILURE``)
    carry per-tool evidence the keyword scan can't reproduce, so the
    metadata fallback is redundant there and its FP rate is high
    (#333 calibration on dogfood corpus: 0% → 100% visible precision
    once traced invocations were excluded — code-review prose
    discussing identifiers like ``_exit_with_error`` and
    ``conversations_with_errors`` no longer surfaces).

    Untraced invocations (Agent SDK calls without linked subagent
    files) still flow through; the 200-char window is their only
    precision backstop. If a future corpus surfaces an untraced FP
    class, next-layer defenses (anchored patterns like ``^Error:``,
    or a confidence field on the emitted signal) are deferred per
    #281's architect-review options.
    """
    signals: list[DiagnosticSignal] = []

    for inv in invocations:
        if not inv.output_text:
            continue
        if inv.trace is not None:
            continue

        for match in iter_error_matches(inv.output_text):
            keyword = match.group(0).lower()
            severity = _KEYWORD_SEVERITY.get(keyword, Severity.WARNING)

            # Extract context around the match. ``iter_error_matches``
            # bounds the search to the leading window, but the snippet
            # context is allowed to extend past the window into the full
            # text so the user sees natural surrounding prose.
            start = max(0, match.start() - 50)
            end = min(len(inv.output_text), match.end() + 50)
            snippet = inv.output_text[start:end].strip()

            signals.append(DiagnosticSignal(
                signal_type=SignalType.ERROR_PATTERN,
                severity=severity,
                agent_type=inv.agent_type,
                invocation_id=inv.invocation_id,
                message=f"Agent '{inv.agent_type}' output contains '{keyword}'.",
                detail={
                    "keyword": keyword,
                    "snippet": snippet,
                    "tool_use_id": inv.tool_use_id,
                },
            ))

    return signals


def _detect_outliers(
    invocations: list[AgentInvocation],
    *,
    accessor: Callable[[AgentInvocation], float | None],
    signal_type: SignalType,
    format_message: Callable[[AgentInvocation, float, float, float], str],
    extra_detail: Callable[[AgentInvocation], dict[str, object]] | None = None,
) -> list[DiagnosticSignal]:
    """Generic per-agent-type outlier detector (Tukey IQR rule).

    Groups invocations by agent type, computes Q1/Q3/IQR of ``accessor``
    values per group, and emits a ``DiagnosticSignal`` for each value
    exceeding ``Q3 + OUTLIER_IQR_MULTIPLIER * IQR``. Skips groups where
    ``len(group) < OUTLIER_MIN_SAMPLE`` or where ``IQR <= 0`` (degenerate
    distribution — can't establish "outlier" sensibly).

    Callers supply ``format_message(inv, value, q3, iqr)`` and may supply
    ``extra_detail(inv)`` to merge per-invocation keys into the signal's
    ``detail`` (e.g. the duration path attaches the current model + token
    count the correlator needs to name + price a concrete model switch).
    """
    signals: list[DiagnosticSignal] = []

    by_type: dict[str, list[AgentInvocation]] = defaultdict(list)
    for inv in invocations:
        if accessor(inv) is not None:
            by_type[inv.agent_type.lower()].append(inv)

    for group in by_type.values():
        if len(group) < OUTLIER_MIN_SAMPLE:
            continue

        values = [v for v in (accessor(inv) for inv in group) if v is not None]
        q1, median_val, q3 = statistics.quantiles(values, n=4)
        iqr = q3 - q1
        if iqr <= 0:
            continue
        threshold = q3 + OUTLIER_IQR_MULTIPLIER * iqr
        # P95 as auxiliary distribution context. Tautological at very
        # small n (becomes the max), but useful at n >= ~10.
        sorted_vals = sorted(values)
        p95_idx = max(0, min(len(sorted_vals) - 1, int(round(0.95 * (len(sorted_vals) - 1)))))
        p95 = sorted_vals[p95_idx]

        for inv in group:
            val = accessor(inv)
            if val is None or val <= threshold:
                continue
            excess_iqrs = (val - q3) / iqr
            detail: dict[str, object] = {
                "actual_value": val,
                "median_value": median_val,
                "q3_value": q3,
                "iqr_value": iqr,
                "p95_value": p95,
                "threshold_value": threshold,
                "excess_iqrs": round(excess_iqrs, 2),
                "tool_use_id": inv.tool_use_id,
            }
            if extra_detail is not None:
                detail.update(extra_detail(inv))
            signals.append(DiagnosticSignal(
                signal_type=signal_type,
                severity=Severity.WARNING,
                agent_type=inv.agent_type,
                invocation_id=inv.invocation_id,
                message=format_message(inv, val, q3, iqr),
                detail=detail,
            ))

    return signals


def _extract_token_outliers(invocations: list[AgentInvocation]) -> list[DiagnosticSignal]:
    """Detect invocations with unusually high token consumption."""
    return _detect_outliers(
        invocations,
        accessor=lambda i: i.tokens_per_tool_use,
        signal_type=SignalType.TOKEN_OUTLIER,
        format_message=lambda inv, val, q3, iqr: (
            f"Agent '{inv.agent_type}' has {val:,.0f} tokens/tool_use, "
            f"{(val - q3) / iqr:.1f}×IQR above Q3 of {q3:,.0f}."
        ),
    )


def _extract_duration_outliers(invocations: list[AgentInvocation]) -> list[DiagnosticSignal]:
    """Detect invocations with unusually high active duration per tool call.

    Uses ``active_duration_per_tool_use`` so user-approval wait time
    isn't attributed to the agent. Filters to ``duration_reliable``
    invocations only: no-trace invocations fall back to wall-clock,
    which silently includes user-wait time and would produce false
    "this agent is slow" outliers (see #453).
    """
    reliable = [inv for inv in invocations if inv.duration_reliable]
    return _detect_outliers(
        reliable,
        accessor=lambda i: i.active_duration_per_tool_use,
        signal_type=SignalType.DURATION_OUTLIER,
        format_message=lambda inv, val, q3, iqr: (
            f"Agent '{inv.agent_type}' has {val / 1000:.1f}s/tool_use, "
            f"{(val - q3) / iqr:.1f}×IQR above Q3 of {q3 / 1000:.1f}s."
        ),
        # The correlator's DurationOutlierRule can't reach the invocation
        # (its interface is (signal, config)), so carry the model it ran
        # on and this invocation's token count — enough to name and price
        # a concrete faster-model switch (#170).
        extra_detail=lambda inv: {
            "current_model": inv.trace.model if inv.trace else None,
            "total_tokens": inv.total_tokens,
        },
    )


def extract_signals(invocations: list[AgentInvocation]) -> list[DiagnosticSignal]:
    """Extract all behavior signals from agent invocations.

    Runs error pattern detection, token outlier detection, and
    duration outlier detection.
    """
    signals: list[DiagnosticSignal] = []
    signals.extend(_extract_error_signals(invocations))
    signals.extend(_extract_token_outliers(invocations))
    signals.extend(_extract_duration_outliers(invocations))
    return signals
