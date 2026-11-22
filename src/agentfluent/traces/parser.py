"""Parse subagent trace JSONL files into ``SubagentTrace`` instances.

Consumes one file at ``<project>/<session-uuid>/subagents/agent-<agentId>.jsonl``
by delegating to ``core.parser.parse_session`` for line reading, SKIP_TYPES
filtering, per-message parsing, and assistant-fragment merging. This
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

from agentfluent.analytics.pricing import SYNTHETIC_MODELS
from agentfluent.core.parser import parse_session
from agentfluent.core.session import ContentBlock, SessionMessage, Usage
from agentfluent.diagnostics.signals import detect_is_error_for_tool
from agentfluent.traces.discovery import AGENT_FILENAME_PATTERN
from agentfluent.traces.models import (
    INPUT_DATA_MAX_CHARS,
    INPUT_SUMMARY_MAX_CHARS,
    RESULT_SUMMARY_MAX_CHARS,
    UNKNOWN_AGENT_TYPE,
    SubagentToolCall,
    SubagentTrace,
    TraceTurnUsage,
)
from agentfluent.traces.retry import detect_retry_sequences

# Idle-gap heuristic: a per-turn wait (earliest tool_use → latest
# tool_result among the turn's parallel calls) counts as idle when
# wait_ms > max(IDLE_GAP_K * median_turn_wait_in_trace, IDLE_GAP_FLOOR_MS);
# idle waits are unioned so overlapping waits count once (#697).
# Re-calibrated for #454 against the v0.7+ corpus: the original
# (k=10, floor=300_000) caught dramatic gaps but missed 1-4 min
# user-coupled waits, contributing to the inflated 33-min pm average
# in the v0.7 dogfood. The §11 12-trace "obviously-stuck" set still
# retains 100% subtraction recall at the lowered values — it's a
# regression guard for subtraction quality, NOT a recall metric for
# STUCK_PATTERN (which uses an unrelated retry-attempt mechanism in
# diagnostics/trace_signals.py and never reads these constants; see
# D038-A in .claude/specs/decisions.md).
#
# Floor anchors on a human-scale "stepped away" threshold (60 s);
# k forward-protects against future workloads with chunkier tool
# latency (e.g., long ML inference) by demanding the gap also exceed
# 5× this trace's median. See scripts/calibration/threshold_validation.ipynb
# §11 for the sweep results and rationale.
IDLE_GAP_K = 5
IDLE_GAP_FLOOR_MS = 60_000


def _summarize_input(
    input_dict: dict[str, Any] | None,
) -> tuple[str, dict[str, Any] | None]:
    """Serialize a tool_use input ONCE and derive both views the trace needs.

    Returns ``(input_summary, input_data)``:

    - ``input_summary`` — the serialized form truncated to
      ``INPUT_SUMMARY_MAX_CHARS`` for display.
    - ``input_data`` — a JSON-normalized copy (round-tripped through
      ``json.loads`` so it holds only plain JSON types and is guaranteed
      re-serializable for paste-ready ``input_examples`` output), or
      ``None`` when the input is absent/empty or its serialized form
      exceeds ``INPUT_DATA_MAX_CHARS`` (oversized inputs omit the
      paste-ready example rather than store a truncated, invalid dict).

    ``default=str`` handles non-serializable values (datetimes, Paths);
    ``ensure_ascii=False`` preserves unicode. Python str slicing is
    codepoint-aware, so truncation never produces invalid unicode (it can
    split a grapheme cluster — acceptable for summary display). Serializing
    once here avoids paying ``json.dumps`` twice per tool call at parse time.
    """
    if input_dict is None:
        return "", None
    serialized = json.dumps(input_dict, default=str, ensure_ascii=False)
    summary = serialized[:INPUT_SUMMARY_MAX_CHARS]
    if not input_dict or len(serialized) > INPUT_DATA_MAX_CHARS:
        return summary, None
    data: dict[str, Any] = json.loads(serialized)
    return summary, data


def _truncate_result(text: str | None) -> str:
    if text is None:
        return ""
    return text[:RESULT_SUMMARY_MAX_CHARS]


def _detect_is_error(block: ContentBlock, tool_name: str) -> bool:
    """Detect whether a tool_result block represents an error.

    Explicit ``is_error`` field is authoritative when present (True or
    False). When missing, fall back to ``detect_is_error_for_tool``, which
    is tool-aware: file-reading tools (Read/Grep/Glob) require the result to
    *begin* with a structured error signature, so a successfully-read file
    whose head is error vocabulary is not misread as a failure (#580).
    Other tools keep the windowed behavior (see #238 / #241).
    """
    if block.is_error is not None:
        return block.is_error
    return detect_is_error_for_tool(block.text, tool_name)


def _turn_usages(messages: list[SessionMessage]) -> list[TraceTurnUsage]:
    """Per-turn ``(message_id, usage)`` for every message carrying usage (#701).

    ``messages`` is already fragment-merged by ``parse_session`` (#696), so each
    entry is one API turn. User messages have no ``usage`` and are skipped. The
    trace's ``usage`` is the sum of exactly these entries (see ``_sum_usage``),
    so the per-file total and the per-turn list cannot drift.
    """
    return [
        TraceTurnUsage(message_id=m.message_id, usage=m.usage)
        for m in messages
        if m.usage is not None
    ]


def _sum_usage(turns: list[TraceTurnUsage]) -> Usage:
    """Aggregate ``Usage`` across a trace's turns."""
    return sum((turn.usage for turn in turns), Usage())


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

    ``turn_index`` is the 0-based ordinal of the assistant message among
    all assistant messages in file order, so sibling calls from one message
    share it (#695). It is not an index into ``turn_usages``, which skips
    messages without ``usage``.
    """
    results: dict[str, tuple[ContentBlock, datetime | None]] = {}
    for msg in messages:
        if msg.type != "user":
            continue
        for block in msg.content_blocks:
            if block.type == "tool_result" and block.tool_use_id:
                results[block.tool_use_id] = (block, msg.timestamp)

    tool_calls: list[SubagentToolCall] = []
    assistant_messages = (msg for msg in messages if msg.type == "assistant")
    for turn_index, msg in enumerate(assistant_messages):
        for block in msg.content_blocks:
            if block.type != "tool_use" or block.id is None:
                continue
            entry = results.get(block.id)
            if entry is not None:
                result_block, result_ts = entry
                is_error = _detect_is_error(result_block, block.name or "")
                result_text = result_block.text
            else:
                is_error = False
                result_text = None
                result_ts = None
            input_summary, input_data = _summarize_input(block.input)
            tool_calls.append(
                SubagentToolCall(
                    tool_name=block.name or "",
                    input_summary=input_summary,
                    input_keys=list(block.input.keys()) if block.input else [],
                    input_data=input_data,
                    result_summary=_truncate_result(result_text),
                    is_error=is_error,
                    timestamp=msg.timestamp,
                    result_timestamp=result_ts,
                    turn_index=turn_index,
                ),
            )
    return tool_calls


def _turn_wait_intervals(
    tool_calls: list[SubagentToolCall],
) -> list[tuple[datetime, datetime]]:
    """One ``(start, end)`` wait interval per model turn, in first-seen order.

    Calls sharing a ``turn_index`` were issued together (#695) and wait in
    parallel, so a turn's wait runs from its earliest ``tool_use`` to its
    latest ``tool_result``. A call with ``turn_index=None`` is its own
    interval. Calls missing either timestamp, or whose result precedes its
    use (clock skew, parsing artifact), are skipped so no work is synthesized.
    """
    by_turn: dict[tuple[str, int], tuple[datetime, datetime]] = {}
    for position, tc in enumerate(tool_calls):
        if tc.timestamp is None or tc.result_timestamp is None:
            continue
        if tc.result_timestamp < tc.timestamp:
            continue
        key = ("call", position) if tc.turn_index is None else ("turn", tc.turn_index)
        current = by_turn.get(key)
        if current is None:
            by_turn[key] = (tc.timestamp, tc.result_timestamp)
        else:
            by_turn[key] = (
                min(current[0], tc.timestamp),
                max(current[1], tc.result_timestamp),
            )
    return list(by_turn.values())


def _union_ms(intervals: list[tuple[datetime, datetime]]) -> float:
    """Total length (ms) covered by ``intervals``, counting overlaps once."""
    total = 0.0
    current_start: datetime | None = None
    current_end: datetime | None = None
    for start, end in sorted(intervals):
        if current_start is None or current_end is None or start > current_end:
            if current_start is not None and current_end is not None:
                total += (current_end - current_start).total_seconds() * 1000
            current_start, current_end = start, end
        elif end > current_end:
            current_end = end
    if current_start is not None and current_end is not None:
        total += (current_end - current_start).total_seconds() * 1000
    return total


def _compute_idle_gap_ms(tool_calls: list[SubagentToolCall]) -> int | None:
    """Idle time: the union of per-turn waits flagged as idle (#697).

    Each model turn contributes one wait (``_turn_wait_intervals``), so N
    parallel siblings waiting through one approval window count as one wait,
    not N. The median is taken over those per-turn waits, a turn is idle when
    its wait exceeds ``max(IDLE_GAP_K * median, IDLE_GAP_FLOOR_MS)``, and the
    idle turns' intervals are unioned so any cross-turn overlap (e.g. a
    background call) is also counted once.

    Returns ``None`` when fewer than two timed turns exist -- too little data
    to compute a meaningful per-trace median, and a single isolated long wait
    can't be distinguished from "this is just how slow this tool runs"
    without context.

    The Claude Code JSONL has no structural marker for approval-pending
    state; this heuristic is a workaround. See anthropics/claude-code#55240
    for the upstream proposal that would replace it with structural detection.
    """
    intervals = _turn_wait_intervals(tool_calls)
    if len(intervals) < 2:
        return None

    waits_ms = [(end - start).total_seconds() * 1000 for start, end in intervals]
    median_wait = statistics.median(waits_ms)
    threshold = max(IDLE_GAP_K * median_wait, float(IDLE_GAP_FLOOR_MS))
    idle_intervals = [
        interval
        for interval, wait in zip(intervals, waits_ms, strict=True)
        if wait > threshold
    ]
    return int(round(_union_ms(idle_intervals)))


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
    turn_usages = _turn_usages(messages)
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
        model_turns=sum(
            1
            for m in messages
            if m.type == "assistant" and m.model not in SYNTHETIC_MODELS
        ),
        usage=_sum_usage(turn_usages),
        turn_usages=turn_usages,
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
