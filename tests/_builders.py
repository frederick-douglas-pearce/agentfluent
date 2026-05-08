"""Shared JSONL builders for tests.

Underscore-prefixed so pytest doesn't auto-collect it. Test files import
these helpers instead of redeclaring their own factories for the same
JSONL shapes (user messages, assistant messages, content blocks,
project layouts with subagent traces).

Design: low-level block and message factories compose into higher-level
shortcuts (``assistant_with_tool_use``, ``user_with_tool_result``,
``write_project_layout``). Keep each helper independently testable and
small — prefer composition over parameter bloat.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Low-level content-block factories.
# ---------------------------------------------------------------------------


def tool_use_block(
    tool_use_id: str,
    name: str = "Bash",
    inp: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a ``tool_use`` content-block dict."""
    return {"type": "tool_use", "id": tool_use_id, "name": name, "input": inp or {}}


def tool_result_block(
    tool_use_id: str,
    content: str = "ok",
    *,
    is_error: bool | None = None,
) -> dict[str, Any]:
    """Build a ``tool_result`` content-block dict."""
    block: dict[str, Any] = {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": content,
    }
    if is_error is not None:
        block["is_error"] = is_error
    return block


# ---------------------------------------------------------------------------
# Message-level factories.
# ---------------------------------------------------------------------------


def user_message(
    content: Any,  # noqa: ANN401 — JSONL content is legitimately Any
    timestamp: str | None = "2026-04-21T10:00:00.000Z",
    *,
    tool_use_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a complete ``user`` message dict.

    ``tool_use_result`` is the camelCase ``toolUseResult`` sibling used
    when the user message is carrying an Agent-tool result — the linker
    reads metadata (agentId, agentType, totalTokens, ...) from this
    field. Omit for plain user text messages.
    """
    msg: dict[str, Any] = {
        "type": "user",
        "message": {"role": "user", "content": content},
    }
    if timestamp is not None:
        msg["timestamp"] = timestamp
    if tool_use_result is not None:
        msg["toolUseResult"] = tool_use_result
    return msg


def assistant_message(
    content: list[dict[str, Any]],
    *,
    message_id: str = "msg_01",
    timestamp: str | None = "2026-04-21T10:00:01.000Z",
    usage: dict[str, int] | None = None,
    model: str = "claude-opus-4-6",
) -> dict[str, Any]:
    """Build a complete ``assistant`` message dict."""
    msg: dict[str, Any] = {
        "type": "assistant",
        "message": {
            "id": message_id,
            "role": "assistant",
            "model": model,
            "content": content,
        },
    }
    if timestamp is not None:
        msg["timestamp"] = timestamp
    if usage is not None:
        msg["message"]["usage"] = usage
    return msg


# ---------------------------------------------------------------------------
# Sequence shortcuts — assistant emits tool_use, user returns tool_result.
# ---------------------------------------------------------------------------


def assistant_with_tool_use(
    tool_use_id: str,
    name: str = "Bash",
    inp: dict[str, Any] | None = None,
    *,
    message_id: str = "msg_01",
    timestamp: str | None = "2026-04-21T10:00:01.000Z",
    usage: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Assistant message wrapping a single ``tool_use`` content block."""
    return assistant_message(
        [tool_use_block(tool_use_id, name, inp)],
        message_id=message_id, timestamp=timestamp, usage=usage,
    )


def user_with_tool_result(
    tool_use_id: str,
    content: str = "ok",
    *,
    is_error: bool | None = None,
    timestamp: str | None = "2026-04-21T10:00:01.500Z",
    tool_use_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """User message wrapping a single ``tool_result`` content block."""
    return user_message(
        [tool_result_block(tool_use_id, content, is_error=is_error)],
        timestamp=timestamp,
        tool_use_result=tool_use_result,
    )


# ---------------------------------------------------------------------------
# Project-layout helper for linker tests.
# ---------------------------------------------------------------------------


def write_project_layout(
    tmp_path: Path,
    session_uuid: str,
    session_messages: list[dict[str, Any]],
    *,
    subagent_traces: dict[str, list[dict[str, Any]]] | None = None,
) -> Path:
    """Write a session JSONL plus optional subagent trace files under ``tmp_path``.

    ``subagent_traces`` maps ``agent_id`` → list of message dicts; each
    entry becomes ``<tmp_path>/<session_uuid>/subagents/agent-<agent_id>.jsonl``.
    Returns ``tmp_path`` itself so the caller can root ``discover_*`` calls
    at the same directory.
    """
    session_path = tmp_path / f"{session_uuid}.jsonl"
    session_path.write_text(
        "\n".join(json.dumps(m) for m in session_messages) + "\n",
    )

    if subagent_traces:
        subagents_dir = tmp_path / session_uuid / "subagents"
        subagents_dir.mkdir(parents=True, exist_ok=True)
        for agent_id, messages in subagent_traces.items():
            (subagents_dir / f"agent-{agent_id}.jsonl").write_text(
                "\n".join(json.dumps(m) for m in messages) + "\n",
            )
    return tmp_path


# ---------------------------------------------------------------------------
# Model factories.
# ---------------------------------------------------------------------------

from typing import Literal  # noqa: E402

from agentfluent.diagnostics.models import DelegationSuggestion  # noqa: E402


def delegation_suggestion(
    name: str = "test-runner",
    description: str = "Handles delegations related to: pytest, tests, run.",
    tools: list[str] | None = None,
    tools_note: str = "",
    confidence: Literal["high", "medium", "low"] = "high",
    dedup_note: str = "",
    top_terms: list[str] | None = None,
    cohesion_score: float = 0.85,
    tools_observed: list[str] | None = None,
) -> DelegationSuggestion:
    """Build a ``DelegationSuggestion`` with project-consistent defaults.

    ``tools_observed`` defaults to ``None`` and is omitted from the model
    constructor when unset — preserves the Pydantic ``default_factory=list``
    behavior. Tests that exercise the unfiltered observed-tools surface
    pass it explicitly.
    """
    kwargs: dict[str, object] = {
        "name": name,
        "description": description,
        "model": "claude-sonnet-4-6",
        "tools": tools if tools is not None else ["Read", "Grep"],
        "tools_note": tools_note,
        "prompt_template": "You run pytest tests and report results.",
        "confidence": confidence,
        "cluster_size": 10,
        "cohesion_score": cohesion_score,
        "top_terms": (
            top_terms if top_terms is not None else ["pytest", "tests", "run"]
        ),
        "dedup_note": dedup_note,
    }
    if tools_observed is not None:
        kwargs["tools_observed"] = tools_observed
    return DelegationSuggestion(**kwargs)
