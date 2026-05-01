"""Behavior signal extraction from agent invocations.

Detects error patterns in output text, token consumption outliers,
and duration outliers.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Callable

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

# Map matched text back to severity
_KEYWORD_SEVERITY: dict[str, Severity] = {kw.lower(): sev for kw, sev in ERROR_PATTERNS}

OUTLIER_THRESHOLD = 2.0


def _extract_error_signals(invocations: list[AgentInvocation]) -> list[DiagnosticSignal]:
    """Detect error patterns in agent output text."""
    signals: list[DiagnosticSignal] = []

    for inv in invocations:
        if not inv.output_text:
            continue

        for match in ERROR_REGEX.finditer(inv.output_text):
            keyword = match.group(0).lower()
            severity = _KEYWORD_SEVERITY.get(keyword, Severity.WARNING)

            # Extract context around the match
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
    format_message: Callable[[AgentInvocation, float, float], str],
) -> list[DiagnosticSignal]:
    """Generic per-agent-type outlier detector.

    Groups invocations by agent type, computes the mean of ``accessor``
    values per group, and emits a ``DiagnosticSignal`` for each value
    exceeding ``mean * OUTLIER_THRESHOLD``. Callers supply a
    ``format_message`` that receives ``(invocation, value, mean)`` and
    returns the human-readable signal message.
    """
    signals: list[DiagnosticSignal] = []

    by_type: dict[str, list[AgentInvocation]] = defaultdict(list)
    for inv in invocations:
        if accessor(inv) is not None:
            by_type[inv.agent_type.lower()].append(inv)

    for group in by_type.values():
        if len(group) < 2:
            continue

        values = [v for v in (accessor(inv) for inv in group) if v is not None]
        mean = sum(values) / len(values)

        for inv in group:
            val = accessor(inv)
            if val is not None and val > mean * OUTLIER_THRESHOLD:
                signals.append(DiagnosticSignal(
                    signal_type=signal_type,
                    severity=Severity.WARNING,
                    agent_type=inv.agent_type,
                    invocation_id=inv.invocation_id,
                    message=format_message(inv, val, mean),
                    detail={
                        "actual_value": val,
                        "mean_value": mean,
                        "ratio": round(val / mean, 1),
                        "tool_use_id": inv.tool_use_id,
                    },
                ))

    return signals


def _extract_token_outliers(invocations: list[AgentInvocation]) -> list[DiagnosticSignal]:
    """Detect invocations with unusually high token consumption."""
    return _detect_outliers(
        invocations,
        accessor=lambda i: i.tokens_per_tool_use,
        signal_type=SignalType.TOKEN_OUTLIER,
        format_message=lambda inv, val, mean: (
            f"Agent '{inv.agent_type}' has {val:,.0f} tokens/tool_use, "
            f"{val / mean:.1f}x above the {mean:,.0f} mean."
        ),
    )


def _extract_duration_outliers(invocations: list[AgentInvocation]) -> list[DiagnosticSignal]:
    """Detect invocations with unusually high active duration per tool call.

    Uses ``active_duration_per_tool_use`` so user-approval wait time
    isn't attributed to the agent.
    """
    return _detect_outliers(
        invocations,
        accessor=lambda i: i.active_duration_per_tool_use,
        signal_type=SignalType.DURATION_OUTLIER,
        format_message=lambda inv, val, mean: (
            f"Agent '{inv.agent_type}' has {val / 1000:.1f}s/tool_use, "
            f"{val / mean:.1f}x above the {mean / 1000:.1f}s mean."
        ),
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
