"""Parse subagent trace JSONL files into ``SubagentTrace`` instances.

Consumes one file at ``<project>/<session-uuid>/subagents/agent-<agentId>.jsonl``
by delegating to ``core.parser.parse_session`` for line reading, SKIP_TYPES
filtering, per-message parsing, and streaming-snapshot deduplication. This
module's job is the subagent-specific shape on top of that: pairing
``tool_use``/``tool_result`` blocks, truncating summaries, detecting errors,
aggregating ``Usage``, and deriving the trace's scalar fields.

Unlike the sibling ``parse_session`` (which warn-logs and returns an empty
list for missing paths), ``parse_subagent_trace`` raises ``FileNotFoundError``
on a missing path — the trace-discovery step guarantees path existence at
call time, so a missing file is a programmer error rather than a user
condition.

The ``agent_type`` field is intentionally left at ``UNKNOWN_AGENT_TYPE`` here
and filled in by the trace-to-parent linker from the parent
``AgentInvocation`` value. Retry-sequence detection runs during parse and
populates ``retry_sequences`` / ``total_retries`` before returning.
"""

from __future__ import annotations

import json
import statistics
from datetime import datetime
from pathlib import Path
from typing import Any

from agentfluent.core.parser import parse_session
from agentfluent.core.session import ContentBlock, SessionMessage, Usage
from agentfluent.diagnostics.signals import detect_is_error_from_text
from agentfluent.traces.discovery import AGENT_FILENAME_PATTERN
from agentfluent.traces.models import (
    INPUT_SUMMARY_MAX_CHARS,
    RESULT_SUMMARY_MAX_CHARS,
    UNKNOWN_AGENT_TYPE,
    SubagentToolCall,
    SubagentTrace,
)
from agentfluent.traces.retry import detect_retry_sequences

# Idle-gap heuristic: a per-call (tool_use → tool_result) gap counts
# as idle when gap_ms > max(IDLE_GAP_K * median_gap_in_trace, IDLE_GAP_FLOOR_MS).
# Calibrated in scripts/calibration/threshold_validation.ipynb §11
# (100% recall on 12 stuck traces in v0.4.0 dogfood data). Floor
# anchors on the 5-min prompt-cache TTL boundary; k forward-protects
# against future workloads with higher baseline tool latency.
IDLE_GAP_K = 10
IDLE_GAP_FLOOR_MS = 300_000


def _truncate_input(input_dict: dict[str, Any] | None) -> str:
    """Serialize a tool_use input dict and truncate to the model's max.

    ``default=str`` handles non-serializable values (datetimes, Paths);
    ``ensure_ascii=False`` preserves unicode for readability. Python str
    slicing is codepoint-aware, so the truncation never produces invalid
    unicode (though it can split extended grapheme clusters — acceptable
    for summary display).
    """
    if input_dict is None:
        return ""
    serialized = json.dumps(input_dict, default=str, ensure_ascii=False)
    return serialized[:INPUT_SUMMARY_MAX_CHARS]


def _truncate_result(text: str | None) -> str:
    if text is None:
        return ""
    return text[:RESULT_SUMMARY_MAX_CHARS]


def _detect_is_error(block: ContentBlock) -> bool:
    """Detect whether a tool_result block represents an error.

    Explicit ``is_error`` field is authoritative when present (True or
    False). When missing, fall back to ``detect_is_error_from_text``
    (shared helper bounded by ``ERROR_DETECTION_WINDOW_CHARS``; see
    #238 / #241 for the FP-defense rationale).
    """
    if block.is_error is not None:
        return block.is_error
    return detect_is_error_from_text(block.text)


def _sum_usage(messages: list[SessionMessage]) -> Usage:
    """Aggregate ``Usage`` across assistant messages. User messages have
    no ``usage`` and are skipped."""
    return sum((m.usage for m in messages if m.usage is not None), Usage())


def _compute_duration_ms(messages: list[SessionMessage]) -> int | None:
    """Timestamp span from first to last message, in milliseconds.

    Returns ``None`` when fewer than two timestamped messages exist.
    """
    timestamps = [msg.timestamp for msg in messages if msg.timestamp is not None]
    if len(timestamps) < 2:
        return None
    first = timestamps[0]
    last = timestamps[-1]
    return int(round((last - first).total_seconds() * 1000))


def _extract_delegation_prompt(messages: list[SessionMessage]) -> str:
    """First user message's text content; empty string if no user messages."""
    for msg in messages:
        if msg.type == "user":
            return msg.text
    return ""


def _pair_tool_calls(messages: list[SessionMessage]) -> list[SubagentToolCall]:
    """Pair ``tool_use`` blocks (in assistant messages) with ``tool_result``
    blocks (in user messages) by ``tool_use_id`` and build
    ``SubagentToolCall`` entries.

    Per-call ``Usage`` is left at default zero: the JSONL shape provides
    one ``usage`` per assistant message but a single message can carry
    multiple ``tool_use`` blocks, so faithful per-call token attribution
    is not possible. Trace-level ``usage`` is the source of truth.
    """
    results: dict[str, tuple[ContentBlock, datetime | None]] = {}
    for msg in messages:
        if msg.type != "user":
            continue
        for block in msg.content_blocks:
            if block.type == "tool_result" and block.tool_use_id:
                results[block.tool_use_id] = (block, msg.timestamp)

    tool_calls: list[SubagentToolCall] = []
    for msg in messages:
        if msg.type != "assistant":
            continue
        for block in msg.content_blocks:
            if block.type != "tool_use" or block.id is None:
                continue
            entry = results.get(block.id)
            if entry is not None:
                result_block, result_ts = entry
                is_error = _detect_is_error(result_block)
                result_text = result_block.text
            else:
                is_error = False
                result_text = None
                result_ts = None
            tool_calls.append(
                SubagentToolCall(
                    tool_name=block.name or "",
                    input_summary=_truncate_input(block.input),
                    result_summary=_truncate_result(result_text),
                    is_error=is_error,
                    timestamp=msg.timestamp,
                    result_timestamp=result_ts,
                ),
            )
    return tool_calls


def _compute_idle_gap_ms(tool_calls: list[SubagentToolCall]) -> int | None:
    """Sum per-call gaps (``tool_use`` to ``tool_result``) flagged as idle.

    Returns ``None`` when fewer than two paired calls have both
    timestamps — too little data to compute a meaningful per-trace
    median, and a single isolated long gap can't be distinguished from
    "this is just how slow this tool runs" without context.

    The Claude Code JSONL has no structural marker for approval-pending
    state; this heuristic is a workaround. See anthropics/claude-code#55240
    for the upstream proposal that would replace it with structural detection.
    """
    gaps_ms: list[float] = []
    for tc in tool_calls:
        if tc.timestamp is None or tc.result_timestamp is None:
            continue
        delta = (tc.result_timestamp - tc.timestamp).total_seconds() * 1000
        if delta < 0:
            # Out-of-order timestamps (clock skew, parsing artifact);
            # don't synthesize work that didn't happen.
            continue
        gaps_ms.append(delta)

    if len(gaps_ms) < 2:
        return None

    median_gap = statistics.median(gaps_ms)
    threshold = max(IDLE_GAP_K * median_gap, float(IDLE_GAP_FLOOR_MS))
    idle_total = sum(g for g in gaps_ms if g > threshold)
    return int(round(idle_total))


def parse_subagent_trace(path: Path) -> SubagentTrace:
    """Parse one subagent JSONL file into a ``SubagentTrace``.

    ``path`` must match the ``agent-<agentId>.jsonl`` pattern; the filename
    is the authoritative source of ``agent_id``. Delegates to
    ``core.parser.parse_session`` for all line-level concerns, then builds
    subagent-specific fields on top.

    Raises:
        FileNotFoundError: ``path`` does not exist. The discovery step
            guarantees existence at call time; this guard catches direct-
            caller programmer errors rather than a user-facing case.
        ValueError: ``path.name`` does not match ``agent-<agentId>.jsonl``.
    """
    if not path.exists():
        msg = f"Subagent trace file not found: {path}"
        raise FileNotFoundError(msg)

    filename_match = AGENT_FILENAME_PATTERN.match(path.name)
    if filename_match is None:
        msg = f"Malformed subagent filename (expected agent-<agentId>.jsonl): {path.name}"
        raise ValueError(msg)
    agent_id = filename_match.group(1)

    messages = parse_session(path)
    tool_calls = _pair_tool_calls(messages)
    retry_sequences = detect_retry_sequences(tool_calls)

    return SubagentTrace(
        agent_id=agent_id,
        agent_type=UNKNOWN_AGENT_TYPE,
        delegation_prompt=_extract_delegation_prompt(messages),
        tool_calls=tool_calls,
        retry_sequences=retry_sequences,
        total_errors=sum(1 for tc in tool_calls if tc.is_error),
        total_retries=sum(seq.attempts - 1 for seq in retry_sequences),
        usage=_sum_usage(messages),
        duration_ms=_compute_duration_ms(messages),
        idle_gap_ms=_compute_idle_gap_ms(tool_calls),
        source_file=path.resolve(),
        model=_first_assistant_model(messages),
    )


def _first_assistant_model(messages: list[SessionMessage]) -> str | None:
    """Return the model string from the first assistant message, or None.

    Subagents are spawned with a model and don't switch mid-run; the
    first assistant message's model is authoritative for the trace.
    Later drift (different models across assistant messages) would be a
    data anomaly — not worth special-casing here; ``diagnostics.model_routing``
    consumes this single value.
    """
    for msg in messages:
        if msg.type == "assistant" and msg.model:
            return msg.model
    return None
