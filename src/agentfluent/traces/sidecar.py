"""Reader for the ``agent-<agentId>.meta.json`` sidecar beside a trace file.

Claude Code writes a small JSON sidecar next to each subagent trace:

```
<session>/subagents/agent-<agentId>.jsonl        # the trace
<session>/subagents/agent-<agentId>.meta.json    # this sidecar
```

Shape: ``{"agentType", "description", "toolUseId"}``.

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

This layer is intentionally read-only and total: a missing or malformed
sidecar yields ``None`` rather than raising, because sidecars are a Claude Code
format evolution and older sessions simply do not have them.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

logger = logging.getLogger(__name__)

SIDECAR_SUFFIX = ".meta.json"


class SubagentSidecar(BaseModel):
    """Parsed ``agent-<agentId>.meta.json`` contents.

    ``extra="ignore"`` so added upstream keys do not break parsing, matching
    ``core.session.ToolResultMetadata``'s forward-compatibility posture.
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    agent_type: str = Field(alias="agentType")
    """Agent type as recorded by the runtime. Parent-authoritative: unlike a
    trace's own ``agent_type`` (which the parser defaults to ``unknown``),
    this is what the spawning side named."""

    description: str = ""
    """Short human-readable task description supplied at delegation time."""

    tool_use_id: str = Field(alias="toolUseId")
    """The ``Agent`` ``tool_use`` block id that spawned this agent -- the edge
    label joining this trace to its invoking call. See the module docstring on
    why this is load-bearing at depth >= 2."""


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
    not valid JSON, or missing required keys. Absence is an expected, ordinary
    case: sessions predating the sidecar format have trace files with no
    sidecar, and callers must degrade rather than fail.
    """
    path = sidecar_path_for(trace_path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        logger.debug("No subagent sidecar for trace: %s", trace_path)
        return None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        # UnicodeDecodeError subclasses ValueError, NOT OSError, so a non-UTF-8
        # sidecar would otherwise escape and break this function's totality.
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
