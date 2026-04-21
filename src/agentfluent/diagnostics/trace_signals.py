"""Trace-level behavior signal extraction from a parsed subagent trace.

The first consumer of the `SubagentTrace` evidence layer produced by E2
(#101–#106). Where `signals.py` mines metadata fields on
`AgentInvocation`, this module mines the full internal tool-call
timeline carried on `AgentInvocation.trace` and emits four
trace-specific signal types:

- `PERMISSION_FAILURE` — a tool result contains a permission-denied
  keyword. Specific remediation: grant the tool in the agent's config.
- `STUCK_PATTERN` — 4+ consecutive identical calls to the same tool.
  Indicates a missing exit condition in the prompt.
- `RETRY_LOOP` — a `RetrySequence` with `attempts >= 3` that is not
  `STUCK_PATTERN`. Indicates missing error-recovery guidance.
- `TOOL_ERROR_SEQUENCE` — 2+ consecutive `is_error=True` results that
  are not covered by a STUCK or RETRY sequence. Indicates missing
  fallback instructions.

STUCK and RETRY are mutually exclusive: a single `RetrySequence` emits
exactly one of them, never both, and TOOL_ERROR_SEQUENCE never emits
for indices already covered by STUCK or RETRY. This invariant keeps the
principle "one observed pattern → one signal" — a future dev must not
"fix" this to independent emission without updating the downstream
correlator contract.

PERMISSION_FAILURE is intentionally NOT mutually exclusive with
TOOL_ERROR_SEQUENCE: a permission-denied run produces both a
specialized recommendation (grant the tool) and the general fallback
signal. Different remediation axes.
"""

from __future__ import annotations

import re

from agentfluent.config.models import Severity
from agentfluent.diagnostics.models import DiagnosticSignal, SignalType
from agentfluent.traces.models import (
    RetrySequence,
    SubagentToolCall,
    SubagentTrace,
)

# Keywords that signal an authorization/access failure in a tool_result.
# Substring match is intentional — "not allowed" inside a longer message
# still indicates a denial. Keep local to this module; do NOT extend
# `signals.ERROR_PATTERNS`, which governs the metadata-level
# ERROR_PATTERN signal and has a different remediation contract.
_PERMISSION_KEYWORDS = (
    "permission denied",
    "access denied",
    "not allowed",
    "blocked",
)
PERMISSION_REGEX = re.compile(
    "|".join(re.escape(k) for k in _PERMISSION_KEYWORDS),
    re.IGNORECASE,
)

# Cap on the `detail.tool_calls` evidence list. The true count lives in
# the sibling `error_count` / `retry_count` / `stuck_count` detail key.
_EVIDENCE_CAP = 5

# Minimum consecutive errors to emit TOOL_ERROR_SEQUENCE.
_ERROR_SEQUENCE_MIN = 2
# Above this length, escalate TOOL_ERROR_SEQUENCE to critical.
_ERROR_SEQUENCE_CRITICAL_MIN = 4
# Minimum attempts to emit RETRY_LOOP (below this, the retry detector's
# output is below the diagnostic signal threshold).
_RETRY_LOOP_MIN_ATTEMPTS = 3
# Minimum identical attempts to emit STUCK_PATTERN instead of RETRY_LOOP.
_STUCK_MIN_ATTEMPTS = 4


def _evidence_from_call(call: SubagentToolCall, index: int) -> dict[str, object]:
    """Build one evidence entry for `DiagnosticSignal.detail.tool_calls`.

    `index` is the position in the owning `SubagentTrace.tool_calls`
    list so the CLI can cross-reference with `RetrySequence.tool_call_indices`.
    """
    return {
        "index": index,
        "tool_name": call.tool_name,
        "input_summary": call.input_summary,
        "result_summary": call.result_summary,
        "is_error": call.is_error,
    }


def _cap_evidence(
    calls: list[SubagentToolCall], indices: list[int],
) -> list[dict[str, object]]:
    """Build an evidence list from parallel call/index lists, capped at _EVIDENCE_CAP."""
    pairs = zip(calls[:_EVIDENCE_CAP], indices[:_EVIDENCE_CAP], strict=False)
    return [_evidence_from_call(c, idx) for c, idx in pairs]


def _extract_permission_failures(
    trace: SubagentTrace, agent_type: str,
) -> list[DiagnosticSignal]:
    """Emit one PERMISSION_FAILURE signal per unique tool with a denied result."""
    signals: list[DiagnosticSignal] = []
    matches: list[tuple[int, SubagentToolCall, str]] = []

    for i, call in enumerate(trace.tool_calls):
        match = PERMISSION_REGEX.search(call.result_summary)
        if match is None:
            continue
        matches.append((i, call, match.group(0).lower()))

    if not matches:
        return signals

    # One signal per unique tool. Scoping to the tool (not the specific
    # call) keeps recommendations actionable: "this tool was denied
    # everywhere" maps to a single config change.
    by_tool: dict[str, list[tuple[int, SubagentToolCall, str]]] = {}
    for entry in matches:
        by_tool.setdefault(entry[1].tool_name, []).append(entry)

    for tool_name, entries in by_tool.items():
        keyword = entries[0][2]
        capped = entries[:_EVIDENCE_CAP]
        evidence = _cap_evidence(
            [c for _, c, _ in capped], [i for i, _, _ in capped],
        )
        signals.append(
            DiagnosticSignal(
                signal_type=SignalType.PERMISSION_FAILURE,
                severity=Severity.CRITICAL,
                agent_type=agent_type,
                message=(
                    f"Subagent '{agent_type}' was denied access to "
                    f"tool '{tool_name}' ({keyword!r})."
                ),
                detail={
                    "tool_calls": evidence,
                    "tool_name": tool_name,
                    "matched_keyword": keyword,
                },
            ),
        )

    return signals


def _extract_retry_and_stuck(
    trace: SubagentTrace, agent_type: str,
) -> tuple[list[DiagnosticSignal], set[int]]:
    """Emit STUCK_PATTERN or RETRY_LOOP — never both — per `RetrySequence`.

    STUCK and RETRY are mutually exclusive by design. A `RetrySequence`
    whose `attempts >= 4` AND whose member calls all share the exact
    same `input_summary` is STUCK; any other sequence with `attempts >= 3`
    is RETRY. Sequences with `attempts == 2` are below the diagnostic
    threshold and do not emit here.

    The returned `covered` set lists all `tool_call_indices` consumed by
    emitted STUCK or RETRY signals so `_extract_error_sequences` can
    avoid double-emitting TOOL_ERROR_SEQUENCE on the same bytes.
    """
    signals: list[DiagnosticSignal] = []
    covered: set[int] = set()

    for seq in trace.retry_sequences:
        if seq.attempts < _RETRY_LOOP_MIN_ATTEMPTS:
            continue

        calls = [trace.tool_calls[i] for i in seq.tool_call_indices]
        if not calls:
            continue

        # STUCK requires attempts >= 4 AND exact input identity; only
        # compute identity when the attempt threshold qualifies.
        is_stuck = seq.attempts >= _STUCK_MIN_ATTEMPTS and all(
            c.input_summary == calls[0].input_summary for c in calls[1:]
        )
        if is_stuck:
            signals.append(_build_stuck_signal(agent_type, seq, calls))
        else:
            signals.append(_build_retry_signal(agent_type, seq, calls))
        covered.update(seq.tool_call_indices)

    return signals, covered


def _build_stuck_signal(
    agent_type: str,
    seq: RetrySequence,
    calls: list[SubagentToolCall],
) -> DiagnosticSignal:
    evidence = _cap_evidence(calls, seq.tool_call_indices)
    return DiagnosticSignal(
        signal_type=SignalType.STUCK_PATTERN,
        severity=Severity.CRITICAL,
        agent_type=agent_type,
        message=(
            f"Subagent '{agent_type}' repeated tool "
            f"'{seq.tool_name}' with identical input {seq.attempts} "
            "times without progress."
        ),
        detail={
            "tool_calls": evidence,
            "stuck_count": seq.attempts,
            "tool_name": seq.tool_name,
            "input_summary": calls[0].input_summary,
        },
    )


def _build_retry_signal(
    agent_type: str,
    seq: RetrySequence,
    calls: list[SubagentToolCall],
) -> DiagnosticSignal:
    evidence = _cap_evidence(calls, seq.tool_call_indices)
    return DiagnosticSignal(
        signal_type=SignalType.RETRY_LOOP,
        severity=Severity.WARNING,
        agent_type=agent_type,
        message=(
            f"Subagent '{agent_type}' retried tool "
            f"'{seq.tool_name}' {seq.attempts} times."
        ),
        detail={
            "tool_calls": evidence,
            "retry_count": seq.attempts,
            "tool_name": seq.tool_name,
            "first_error_message": seq.first_error_message,
            "eventual_success": seq.eventual_success,
        },
    )


def _extract_error_sequences(
    trace: SubagentTrace, covered: set[int], agent_type: str,
) -> list[DiagnosticSignal]:
    """Emit TOOL_ERROR_SEQUENCE for runs of consecutive `is_error=True`
    tool calls that are not already covered by STUCK/RETRY signals.

    A covered index is dropped from the run before measuring its length,
    which preserves the `>= 2` threshold — a run straddling a covered
    region is only emitted if the uncovered portion is itself long
    enough.
    """
    signals: list[DiagnosticSignal] = []
    calls = trace.tool_calls
    n = len(calls)

    i = 0
    while i < n:
        if not calls[i].is_error:
            i += 1
            continue
        # Consume consecutive errors starting at i.
        j = i
        while j < n and calls[j].is_error:
            j += 1
        # Filter out covered indices; if uncovered remainder is long
        # enough, emit.
        run_indices = [k for k in range(i, j) if k not in covered]
        if len(run_indices) >= _ERROR_SEQUENCE_MIN:
            run_calls = [calls[k] for k in run_indices]
            severity = (
                Severity.CRITICAL
                if len(run_indices) >= _ERROR_SEQUENCE_CRITICAL_MIN
                else Severity.WARNING
            )
            evidence = _cap_evidence(run_calls, run_indices)
            signals.append(
                DiagnosticSignal(
                    signal_type=SignalType.TOOL_ERROR_SEQUENCE,
                    severity=severity,
                    agent_type=agent_type,
                    message=(
                        f"Subagent '{agent_type}' had "
                        f"{len(run_indices)} consecutive tool errors."
                    ),
                    detail={
                        "tool_calls": evidence,
                        "error_count": len(run_indices),
                        "start_index": run_indices[0],
                        "end_index": run_indices[-1],
                    },
                ),
            )
        i = j

    return signals


def extract_trace_signals(
    trace: SubagentTrace | None,
    *,
    agent_type: str | None = None,
) -> list[DiagnosticSignal]:
    """Extract all trace-level behavior signals from a parsed subagent trace.

    Handles `None` (unlinked invocation) and empty traces by returning
    an empty list. Order of signals: PERMISSION_FAILURE first
    (remediation-specific), then STUCK/RETRY (indexed), then
    TOOL_ERROR_SEQUENCE (filtered by STUCK/RETRY coverage).

    `agent_type` overrides `trace.agent_type` on emitted signals when
    provided. The pipeline passes the parent `AgentInvocation.agent_type`
    to avoid depending on the linker having populated the trace
    (programmatically-constructed or unlinked traces may still hold
    `UNKNOWN_AGENT_TYPE`).
    """
    if trace is None or not trace.tool_calls:
        return []

    effective_agent_type = agent_type if agent_type else trace.agent_type

    signals: list[DiagnosticSignal] = []
    signals.extend(_extract_permission_failures(trace, effective_agent_type))
    retry_stuck, covered = _extract_retry_and_stuck(trace, effective_agent_type)
    signals.extend(retry_stuck)
    signals.extend(_extract_error_sequences(trace, covered, effective_agent_type))
    return signals
