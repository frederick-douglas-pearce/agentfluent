"""Reader for the ``agent-<agentId>.meta.json`` sidecar beside a trace file.

Claude Code writes a small JSON sidecar next to each subagent trace:

```
<session>/subagents/agent-<agentId>.jsonl        # the trace
<session>/subagents/agent-<agentId>.meta.json    # this sidecar
```

Shape: ``{"agentType", "description", "toolUseId"}``, plus -- on newer
Claude Code -- optional ``name``, ``spawnDepth``, ``parentAgentId``, ``isFork``,
``requestShape`` and others (ignored). ``toolUseId`` itself is absent on agents
spawned without an ``Agent`` tool_use, e.g. skill-run background agents (#701).

**Why this matters (#595).** ``toolUseId`` names the ``Agent`` ``tool_use``
block that spawned this agent, which makes the sidecar the *only structured*
child-to-parent edge at depth >= 2. At depth 1 the same edge is recoverable
from the parent session's ``toolUseResult.agentId``, but a depth->=2
``tool_result`` carries **no** ``toolUseResult`` at all -- only an inline prose
trailer (``agentId: <id> <usage>subagent_tokens: ...</usage>``), which this
package deliberately does not parse. Verified against live SDK bytes and
encoded in ``tests/fixtures/nested_session/``.

Note the edge is a *label*, not a resolved parent: ``tool_use_id`` identifies a
``tool_use`` **block**, not the agent that emitted it. Resolving label to
emitting agent requires an index over the main session plus sibling traces --
that resolver is the linker's job (#595 PR B), not this module's.

This layer is intentionally read-only and total: a missing, unreadable or
non-object sidecar yields ``None`` rather than raising, because sidecars are a
Claude Code format evolution and older sessions simply do not have them. Within
an object, wrongly-typed fields fall back to their defaults rather than
discarding the sidecar (#701).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

logger = logging.getLogger(__name__)

SIDECAR_SUFFIX = ".meta.json"


class SubagentSidecar(BaseModel):
    """Parsed ``agent-<agentId>.meta.json`` contents.

    ``extra="ignore"`` so added upstream keys do not break parsing, matching
    ``core.session.ToolResultMetadata``'s forward-compatibility posture.
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    tool_use_id: str | None = Field(None, alias="toolUseId")
    """The ``Agent`` ``tool_use`` block id that spawned this agent -- the edge
    label joining this trace to its invoking call. See the module docstring on
    why this is load-bearing at depth >= 2.

    ``None`` when the sidecar carries no ``toolUseId``. That is a real, common
    shape, not a malformed one (#701): agents a session spawns **without** an
    ``Agent`` tool_use -- skill- and workflow-run background agents such as the
    built-in ``/code-review`` -- get a sidecar with ``name`` / ``spawnDepth`` /
    ``requestShape`` and no ``toolUseId``. Rejecting the whole sidecar for the
    missing edge used to discard their ``agentType`` and ``name`` too. Such a
    sidecar simply contributes no edge to lineage resolution."""

    name: str | None = None
    """Runtime-assigned name, e.g. ``"code-review"`` for the built-in review
    skill's background agent. Absent on ``Agent``-tool spawns."""

    spawn_depth: int | None = Field(None, alias="spawnDepth")
    """Runtime-recorded delegation depth (1 = spawned by the main session).
    Present on newer sidecars only. **A label, never a fold input** -- which
    traces count toward a session total is decided by ownership, not by this
    field (#648 architect ruling)."""

    parent_agent_id: str | None = Field(None, alias="parentAgentId")
    """Runtime-recorded spawning agent's id, on some depth->=2 sidecars. Same
    label-only status as ``spawn_depth``."""

    is_fork: bool = Field(False, alias="isFork")
    """``True`` for fork agents, which replay the spawning conversation's
    context -- the reason trace turns can repeat across files (#701)."""

    request_shape: str | None = Field(None, alias="requestShape")
    """``"background"`` / ``"foreground"`` as recorded by the runtime."""

    agent_type: str = Field("", alias="agentType")
    """Agent type as recorded by the runtime. Parent-authoritative: unlike a
    trace's own ``agent_type`` (which the parser defaults to ``unknown``),
    this is what the spawning side named. Empty when absent or malformed --
    callers should fall back to the trace's own value."""

    description: str = ""
    """Short human-readable task description supplied at delegation time."""

    @field_validator("agent_type", "description", mode="before")
    @classmethod
    def _null_becomes_empty(cls, value: object) -> object:
        """Coerce a null or non-string descriptive field to ``""`` instead of
        failing.

        A cosmetic field's shape must not cost us the ``tool_use_id`` edge.
        """
        return value if isinstance(value, str) else ""

    @field_validator(
        "tool_use_id", "name", "parent_agent_id", "request_shape", mode="before",
    )
    @classmethod
    def _non_string_becomes_none(cls, value: object) -> object:
        """Read a non-string optional text field as absent rather than failing.

        Same posture as ``_null_becomes_empty``: one odd field must not discard
        the rest of the sidecar.
        """
        return value if isinstance(value, str) else None

    @field_validator("spawn_depth", mode="before")
    @classmethod
    def _non_int_depth_becomes_none(cls, value: object) -> object:
        """Only a JSON integer is a depth; anything else (bool included) is absent."""
        if isinstance(value, bool) or not isinstance(value, int):
            return None
        return value

    @field_validator("is_fork", mode="before")
    @classmethod
    def _malformed_flag_becomes_false(cls, value: object) -> object:
        """Anything but a JSON boolean reads as not-a-fork."""
        return value if isinstance(value, bool) else False


def sidecar_path_for(trace_path: Path) -> Path:
    """Return the sidecar path for a trace path (existence not checked).

    Built from ``stem`` rather than chained ``with_suffix`` calls: an agentId
    containing a dot (``agent-a.b.jsonl``) would make ``with_suffix("")`` strip
    ``.b`` and yield ``agent-a.meta.json``.
    """
    return trace_path.parent / (trace_path.stem + SIDECAR_SUFFIX)


def read_subagent_sidecar(trace_path: Path) -> SubagentSidecar | None:
    """Read the sidecar beside ``trace_path``.

    Returns ``None`` -- never raises -- when the sidecar is absent, unreadable,
    or not a JSON object, or -- defensively -- fails model validation. Every
    field coerces a missing or wrongly-typed value to its default, so one odd
    field never discards the rest; the validation branch is a backstop, not an
    expected path. (No key is required since #701 -- a sidecar without
    ``toolUseId`` still carries the agent's type and name.) Absence is an
    expected, ordinary case: sessions predating the sidecar format have trace
    files with no sidecar, and callers must degrade rather than fail.
    """
    path = sidecar_path_for(trace_path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        logger.debug("No subagent sidecar for trace: %s", trace_path)
        return None
    except Exception as exc:
        # Deliberately broad. Totality is this function's contract, and
        # enumerating exception types has already failed twice here:
        # UnicodeDecodeError subclasses ValueError (not OSError), and
        # RecursionError -- raised by json.loads on deeply nested input --
        # subclasses RuntimeError, so neither matched a typed tuple. The
        # operation is bounded (read one small file, parse it) and every
        # failure has the same handling, so bound the operation, not the
        # type list.
        logger.debug("Unreadable subagent sidecar %s: %s", path, exc)
        return None

    if not isinstance(raw, dict):
        logger.debug("Subagent sidecar is not a JSON object: %s", path)
        return None

    try:
        return SubagentSidecar.model_validate(raw)
    except ValidationError as exc:
        logger.debug("Malformed subagent sidecar %s: %s", path, exc)
        return None
