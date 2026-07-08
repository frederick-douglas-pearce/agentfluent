"""Trace-level behavior signal extraction from a parsed subagent trace.

The first consumer of the `SubagentTrace` evidence layer produced by E2
(#101–#106). Where `signals.py` mines metadata fields on
`AgentInvocation`, this module mines the full internal tool-call
timeline carried on `AgentInvocation.trace` and emits four
trace-specific signal types:

- `PERMISSION_FAILURE` — a tool result contains a permission-denied
  keyword. Specific remediation: grant the tool in the agent's config.
- `PARAMETER_RETRY` — 2+ consecutive calls to the same tool where the
  first errored and the `input` shape changed between attempts.
  Indicates the agent is guessing at the parameter format; remediation
  is `input_examples` on the tool definition.
- `STUCK_PATTERN` — 4+ consecutive identical calls to the same tool.
  Indicates a missing exit condition in the prompt.
- `RETRY_LOOP` — a `RetrySequence` with `attempts >= 3` that is not
  `STUCK_PATTERN`. Indicates missing error-recovery guidance.
- `TOOL_ERROR_SEQUENCE` — 2+ consecutive `is_error=True` results that
  are not covered by a STUCK or RETRY sequence. Indicates missing
  fallback instructions.

PARAMETER_RETRY, STUCK, RETRY, and TOOL_ERROR_SEQUENCE share one
`covered` index set, enforcing "one observed pattern → one signal" with
the precedence **PARAMETER_RETRY > STUCK_PATTERN > RETRY_LOOP >
TOOL_ERROR_SEQUENCE**. PARAMETER_RETRY runs first and is the most
specific/actionable (it yields a concrete `input_examples` fix), so its
indices suppress any overlapping RETRY_LOOP/STUCK_PATTERN/error
sequence. STUCK and RETRY remain mutually exclusive per `RetrySequence`.
A future dev must not "fix" this to independent emission without
updating the downstream correlator contract.

PARAMETER_RETRY scans `tool_calls` directly rather than reusing
`trace.retry_sequences`: a parameter-shape retry changes the `input`
enough that the similarity-based retry detector (`traces.retry`, 0.80
threshold) may not group the calls into a `RetrySequence` at all. The
two detectors answer different questions — "same args, no progress"
(retry) vs "different arg shape after an error" (parameter).

PERMISSION_FAILURE is intentionally NOT mutually exclusive with the
above: a permission-denied run produces both a specialized
recommendation (grant the tool) and the general fallback signal.
Different remediation axes.
"""

from __future__ import annotations

import re

from agentfluent.config.models import Severity
from agentfluent.diagnostics.models import DiagnosticSignal, SignalType
from agentfluent.traces.models import (
    RESULT_SUMMARY_MAX_CHARS,
    RetrySequence,
    SubagentToolCall,
    SubagentTrace,
)

# Producer/consumer contract: ``_extract_parameter_retries`` stores the
# extracted paste-ready input dict under this key in the signal's
# ``detail`` (only when a successful call's ``input_data`` is available);
# ``correlator.ParameterRetryRule`` reads it to format the suggested
# ``input_examples`` block. Absent when no successful call followed the
# retries or its input exceeded the capture cap.
PARAMETER_RETRY_EXAMPLE_KEY = "input_example"

# Minimum consecutive same-tool calls to consider a parameter-retry run.
_PARAMETER_RETRY_MIN_ATTEMPTS = 2
# First-error summary length cap in the signal message.
_PARAM_ERROR_SUMMARY_MAX_CHARS = 120

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

# Match a leading line-number prefix from successful Read (``<n>\t``) or
# Grep (``<n>:``) output. Permission-keyword hits inside such results
# are file content, not error messages.
_LINE_NUMBERED_RESULT = re.compile(r"^\s*\d+[\t:]")


def _is_false_positive_denial(result_summary: str) -> bool:
    """True when a permission keyword in ``result_summary`` is structurally
    file content (line-numbered Read/Grep output, or truncated to the cap)."""
    if _LINE_NUMBERED_RESULT.match(result_summary):
        return True
    return len(result_summary) >= RESULT_SUMMARY_MAX_CHARS

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
    trace: SubagentTrace,
    agent_type: str,
    *,
    invocation_id: str | None = None,
) -> list[DiagnosticSignal]:
    """Emit one PERMISSION_FAILURE signal per unique tool with a denied result."""
    signals: list[DiagnosticSignal] = []
    matches: list[tuple[int, SubagentToolCall, str]] = []

    for i, call in enumerate(trace.tool_calls):
        match = PERMISSION_REGEX.search(call.result_summary)
        if match is None:
            continue
        if _is_false_positive_denial(call.result_summary):
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
                invocation_id=invocation_id,
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


def _value_shape(value: object) -> object:
    """Recursively reduce a JSON value to its structural shape.

    Dicts recurse (sorted keys mapped to nested shapes) so nested-object
    differences and added/removed nested keys are detected; every other
    value collapses to its type name, so a scalar type change
    (``"str"`` -> ``"int"``) registers while same-typed value changes
    (two different file paths) do not. Lists collapse to ``"list"`` —
    element-level drift is out of scope for shape comparison.
    """
    if isinstance(value, dict):
        return {k: _value_shape(v) for k, v in sorted(value.items())}
    return type(value).__name__


def _input_shape_changed(run_calls: list[SubagentToolCall]) -> bool:
    """Whether the ``input`` shape changed across a same-tool run.

    Primary signal: the set of top-level keys differs between calls
    (a key added, removed, or renamed). Secondary signal (only when every
    call captured ``input_data``): the nested value structure differs —
    a nested key change or a scalar type change. Returns ``False`` when
    no shape evidence exists (all inputs empty / uncaptured), so a run
    with no observable parameter variation never fires PARAMETER_RETRY.
    """
    key_shapes = {tuple(sorted(c.input_keys)) for c in run_calls}
    if len(key_shapes) > 1:
        return True
    if all(c.input_data is not None for c in run_calls):
        # `_value_shape` returns nested dicts of type-name strings, which
        # compare structurally with `==` — no need to serialize to compare.
        first_shape = _value_shape(run_calls[0].input_data)
        return any(
            _value_shape(c.input_data) != first_shape for c in run_calls[1:]
        )
    return False


def _extract_successful_example(
    run_calls: list[SubagentToolCall],
) -> dict[str, object] | None:
    """The most-evolved successful ``input_data`` in the run, if any.

    Scans from the end so the last (most-corrected) successful shape is
    preferred. Returns ``None`` when no non-error call carries captured
    ``input_data`` — the paste-ready section is then omitted while the
    signal still fires.
    """
    for call in reversed(run_calls):
        if not call.is_error and call.input_data is not None:
            return call.input_data
    return None


def _build_parameter_retry_signal(
    calls: list[SubagentToolCall],
    run_indices: list[int],
    agent_type: str,
    invocation_id: str | None,
) -> DiagnosticSignal | None:
    """Emit PARAMETER_RETRY for a same-tool run, or ``None`` if it doesn't
    qualify (first call not an error, or no input-shape change).

    The first attempt must carry ``is_error=True`` — the parser-supplied flag,
    which the parser also synthesizes from result text via
    ``signals.detect_is_error_for_tool``. That synthesis covers generic error
    vocabulary (``failed``/``error``/``unable to``/…) for most tools, but on
    file-reading tools (Read/Grep/Glob) it requires the result to *begin* with a
    structured error signature — so a successful read whose head is error
    vocabulary is not misread as a first-attempt failure (#580). A first call
    that sets neither ``is_error`` nor a qualifying error signature is treated as
    paging/refinement, not a parameter retry. This is a
    deliberate precision trade-off (#510): it drops the prior keyword-regex
    fallback (``invalid``/``validation``/``schema``/…) that fired on
    *successful* results whose text merely looked validation-flavored — the
    false positives, including describing a successful first result as "failed
    with", that motivated this gate. The message is keyed on ``run_calls[0]``,
    so gating on the FIRST attempt (not any attempt) is what keeps that message
    truthful."""
    run_calls = [calls[i] for i in run_indices]
    if not run_calls[0].is_error:
        return None
    if not _input_shape_changed(run_calls):
        return None

    tool_name = run_calls[0].tool_name
    attempts = len(run_calls)
    eventual_success = not run_calls[-1].is_error
    error_summary = " ".join(run_calls[0].result_summary.split())[
        :_PARAM_ERROR_SUMMARY_MAX_CHARS
    ]
    suffix = " before succeeding" if eventual_success else ""

    detail: dict[str, object] = {
        "tool_calls": _cap_evidence(run_calls, run_indices),
        "tool_name": tool_name,
        "retry_count": attempts,
        "first_error_message": run_calls[0].result_summary,
        "eventual_success": eventual_success,
    }
    example = _extract_successful_example(run_calls)
    if example is not None:
        detail[PARAMETER_RETRY_EXAMPLE_KEY] = example

    return DiagnosticSignal(
        signal_type=SignalType.PARAMETER_RETRY,
        severity=Severity.WARNING,
        agent_type=agent_type,
        invocation_id=invocation_id,
        message=(
            f"Subagent '{agent_type}' retried tool '{tool_name}' "
            f"{attempts} times with different parameter shapes{suffix}. "
            f"First attempt failed with: '{error_summary}'."
        ),
        detail=detail,
    )


def _extract_parameter_retries(
    trace: SubagentTrace,
    agent_type: str,
    *,
    invocation_id: str | None = None,
) -> tuple[list[DiagnosticSignal], set[int]]:
    """Emit PARAMETER_RETRY for consecutive same-tool runs that show an
    initial error followed by an input-shape change.

    Scans ``tool_calls`` directly (independent of ``retry_sequences``) for
    maximal runs of 2+ calls sharing a ``tool_name``. The returned
    ``covered`` set lists every index claimed by an emitted signal so the
    retry/stuck and error-sequence extractors skip the same evidence —
    PARAMETER_RETRY wins the precedence.
    """
    signals: list[DiagnosticSignal] = []
    covered: set[int] = set()
    calls = trace.tool_calls
    n = len(calls)

    i = 0
    while i < n:
        j = i + 1
        while j < n and calls[j].tool_name == calls[i].tool_name:
            j += 1
        if j - i >= _PARAMETER_RETRY_MIN_ATTEMPTS:
            run_indices = list(range(i, j))
            signal = _build_parameter_retry_signal(
                calls, run_indices, agent_type, invocation_id,
            )
            if signal is not None:
                signals.append(signal)
                covered.update(run_indices)
        i = j

    return signals, covered


def _extract_retry_and_stuck(
    trace: SubagentTrace,
    agent_type: str,
    *,
    invocation_id: str | None = None,
    exclude: set[int] | None = None,
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

    `exclude` carries the indices already claimed by PARAMETER_RETRY
    (higher precedence); any `RetrySequence` overlapping it is skipped so
    a parameter-shape retry doesn't also surface as a RETRY_LOOP.
    """
    exclude = exclude or set()
    signals: list[DiagnosticSignal] = []
    covered: set[int] = set()

    for seq in trace.retry_sequences:
        if seq.attempts < _RETRY_LOOP_MIN_ATTEMPTS:
            continue
        if exclude.intersection(seq.tool_call_indices):
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
            signals.append(
                _build_stuck_signal(
                    agent_type, seq, calls, invocation_id=invocation_id,
                ),
            )
        else:
            signals.append(
                _build_retry_signal(
                    agent_type, seq, calls, invocation_id=invocation_id,
                ),
            )
        covered.update(seq.tool_call_indices)

    return signals, covered


def _build_stuck_signal(
    agent_type: str,
    seq: RetrySequence,
    calls: list[SubagentToolCall],
    *,
    invocation_id: str | None = None,
) -> DiagnosticSignal:
    evidence = _cap_evidence(calls, seq.tool_call_indices)
    return DiagnosticSignal(
        signal_type=SignalType.STUCK_PATTERN,
        severity=Severity.CRITICAL,
        agent_type=agent_type,
        invocation_id=invocation_id,
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
    *,
    invocation_id: str | None = None,
) -> DiagnosticSignal:
    evidence = _cap_evidence(calls, seq.tool_call_indices)
    return DiagnosticSignal(
        signal_type=SignalType.RETRY_LOOP,
        severity=Severity.WARNING,
        agent_type=agent_type,
        invocation_id=invocation_id,
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
    trace: SubagentTrace,
    covered: set[int],
    agent_type: str,
    *,
    invocation_id: str | None = None,
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
                    invocation_id=invocation_id,
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
    invocation_id: str | None = None,
) -> list[DiagnosticSignal]:
    """Extract all trace-level behavior signals from a parsed subagent trace.

    Handles `None` (unlinked invocation) and empty traces by returning
    an empty list. Order of signals: PERMISSION_FAILURE first
    (remediation-specific, non-exclusive), then PARAMETER_RETRY (claims
    its indices), then STUCK/RETRY (skipping PARAMETER_RETRY-covered
    sequences), then TOOL_ERROR_SEQUENCE (filtered by all prior
    coverage).

    `agent_type` overrides `trace.agent_type` on emitted signals when
    provided. The pipeline passes the parent `AgentInvocation.agent_type`
    to avoid depending on the linker having populated the trace
    (programmatically-constructed or unlinked traces may still hold
    `UNKNOWN_AGENT_TYPE`).

    `invocation_id` is stamped on every emitted signal so consumers can
    drill from a trace finding back to its parent ``AgentInvocation``
    (#197). The pipeline passes ``inv.agent_id or inv.tool_use_id``.
    """
    if trace is None or not trace.tool_calls:
        return []

    effective_agent_type = agent_type if agent_type else trace.agent_type

    signals: list[DiagnosticSignal] = []
    signals.extend(
        _extract_permission_failures(
            trace, effective_agent_type, invocation_id=invocation_id,
        ),
    )
    param_signals, param_covered = _extract_parameter_retries(
        trace, effective_agent_type, invocation_id=invocation_id,
    )
    signals.extend(param_signals)
    retry_stuck, retry_covered = _extract_retry_and_stuck(
        trace,
        effective_agent_type,
        invocation_id=invocation_id,
        exclude=param_covered,
    )
    signals.extend(retry_stuck)
    signals.extend(
        _extract_error_sequences(
            trace,
            param_covered | retry_covered,
            effective_agent_type,
            invocation_id=invocation_id,
        ),
    )
    return signals
