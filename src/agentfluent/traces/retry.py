"""Detect retry sequences within a subagent's tool_calls.

A retry sequence is two or more consecutive tool calls (in
``SubagentTrace.tool_calls`` order) that share a ``tool_name`` and have
similar ``input_summary`` values — i.e., the agent called the same tool
again with the same or nearly-the-same arguments. That pattern is the
signal for the ``RETRY_LOOP`` diagnostic downstream of the parser.

**Similarity is measured against the predecessor**, not against the first
call in the run. Real retry chains drift ("edit file X line 40" ->
"edit file X line 41" -> "edit file X line 42"): comparing each call
to the previous one lets the chain extend under gradual drift, where
comparing to the first would cut it short. Adjacent pairs are what
makes the run a retry sequence; the head and tail can be quite
different as long as every step is a small change.

``SIMILARITY_THRESHOLD = 0.80`` is the direct translation of the AC's
"edit distance < 20%" formulation, applied via
``difflib.SequenceMatcher.ratio()``. An exact ``input_summary`` match is
the common case and short-circuits before the matcher runs.
"""

from __future__ import annotations

from difflib import SequenceMatcher

from agentfluent.traces.models import RetrySequence, SubagentToolCall

SIMILARITY_THRESHOLD = 0.80


def _is_similar_retry(a: SubagentToolCall, b: SubagentToolCall) -> bool:
    if a.tool_name != b.tool_name:
        return False
    if a.input_summary == b.input_summary:
        return True
    return SequenceMatcher(None, a.input_summary, b.input_summary).ratio() >= SIMILARITY_THRESHOLD


def _build_retry_sequence(
    tool_calls: list[SubagentToolCall], start: int, end: int,
) -> RetrySequence:
    """Construct a ``RetrySequence`` from the half-open run ``[start, end)``.

    ``end - start`` is always ``>= 2`` by the caller's emission gate, so
    ``attempts`` always satisfies the model's ``ge=1`` validator. First /
    last error messages come from the first / last calls within the run
    whose ``is_error`` is true; both are ``None`` when no call in the run
    errored (rare but legal).
    """
    indices = list(range(start, end))
    first_error_message: str | None = None
    last_error_message: str | None = None
    for idx in indices:
        if tool_calls[idx].is_error:
            if first_error_message is None:
                first_error_message = tool_calls[idx].result_summary
            last_error_message = tool_calls[idx].result_summary

    return RetrySequence(
        tool_name=tool_calls[start].tool_name,
        attempts=end - start,
        first_error_message=first_error_message,
        last_error_message=last_error_message,
        eventual_success=not tool_calls[end - 1].is_error,
        tool_call_indices=indices,
    )


def detect_retry_sequences(
    tool_calls: list[SubagentToolCall],
) -> list[RetrySequence]:
    """Return the retry sequences found in ``tool_calls``.

    Scans left-to-right; a run ``[i, j)`` qualifies when every adjacent
    pair ``(j-1, j)`` passes ``_is_similar_retry`` and ``j - i >= 2``.
    Interleaved unrelated tool calls break a run. The scan never revisits
    a position, so two separate runs of the same tool across the list
    produce two independent sequences.
    """
    sequences: list[RetrySequence] = []
    i = 0
    n = len(tool_calls)
    while i < n:
        j = i + 1
        while j < n and _is_similar_retry(tool_calls[j - 1], tool_calls[j]):
            j += 1
        if j - i >= 2:
            sequences.append(_build_retry_sequence(tool_calls, i, j))
        i = j
    return sequences
