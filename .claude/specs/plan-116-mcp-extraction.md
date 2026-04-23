# Plan — Story #116: Parse MCP tool names from session + trace data

**Parent epic:** #100 ([epic plan](./plan-100-mcp-assessment.md))
**Depends on:** #117 (merged as PR #156) — `McpServerConfig`, `claude_json_for`

## Context

Extraction story. Discovers observed MCP tool usage from two sources:

1. **Subagent traces** — `SubagentTrace.tool_calls`, which already carry `tool_name` and `is_error` per #101
2. **Parent-session assistant messages** — `tool_use` blocks in `SessionMessage.content` that happen outside any Agent delegation (observed on real data: `mcp__github__*` calls made directly in the main session)

Outputs `dict[str, McpServerUsage]` aggregating calls, tools, and errors per server. Does not audit — that's #118's job.

## Architectural decision — where does parent-session extraction run?

Currently `AgentInvocation` doesn't retain raw messages after `extract_agent_invocations` consumes them. So MCP calls made at the parent session level are invisible downstream.

**Chosen approach**: add a single-pass extractor function that runs during `analyze_session`, alongside the existing `extract_agent_invocations` call. Output lives on `SessionAnalysis` as a new `mcp_tool_calls` field. Threaded through `AnalysisResult` to `run_diagnostics` via a new kwarg on the existing pipeline.

Rationale:
- One pass through the `messages` list (already in memory during `analyze_session`), no extra I/O
- No need to carry raw messages past the analytics layer
- Clean symmetry with `invocations`: both are "per-session extracted evidence"
- Preserves the existing per-session → aggregated flow; `run_diagnostics` takes the flattened list the same way it does for invocations

Alternative considered: extract MCP calls only from subagent traces in #116, defer parent-session to a follow-up. Rejected because real-world data shows substantive parent-session MCP usage (`mcp__github__*` on this contributor's sessions) and the audit story (#118) would ship incomplete.

## Scope

**Owns:**
- `src/agentfluent/diagnostics/mcp_assessment.py` (NEW, ~130 lines) — `parse_mcp_tool_name`, `McpToolCall` dataclass, `McpServerUsage` dataclass, `extract_mcp_calls_from_messages`, `extract_mcp_usage`
- `src/agentfluent/analytics/pipeline.py` — add `mcp_tool_calls` field on `SessionAnalysis`, populate in `analyze_session`
- `tests/unit/test_mcp_assessment.py` (NEW, ~15 tests)
- `tests/fixtures/mcp/` — 2 session fixtures (tool use present; with and without errors)

**Deferred to #118 / #119:**
- Audit rules (unused / missing signal emission)
- Pipeline wiring of audit into `run_diagnostics`
- Any `McpServerConfig` consumption

## Module layout

```
src/agentfluent/
├── analytics/
│   └── pipeline.py          # +mcp_tool_calls on SessionAnalysis + populate in analyze_session
└── diagnostics/
    └── mcp_assessment.py    # NEW — extraction + parsing + aggregation

tests/
├── fixtures/mcp/
│   ├── session_with_mcp_success.jsonl    # mcp__github__create_issue, all success
│   └── session_with_mcp_errors.jsonl     # mcp__nonexistent__* with is_error=True
├── unit/test_mcp_assessment.py           # NEW
└── unit/test_analytics_pipeline.py       # +1 wiring test (may need creating if absent)
```

## Public surface

```python
# diagnostics/mcp_assessment.py

def parse_mcp_tool_name(name: str) -> tuple[str, str] | None: ...

class McpToolCall(BaseModel):
    model_config = ConfigDict(frozen=True)
    server_name: str
    tool_name: str
    is_error: bool

class McpServerUsage(BaseModel):
    model_config = ConfigDict(frozen=True)
    server_name: str
    total_calls: int
    unique_tools: list[str]   # sorted; from a set internally
    error_count: int

def extract_mcp_calls_from_messages(
    messages: list[SessionMessage],
) -> list[McpToolCall]: ...

def extract_mcp_usage(
    invocations: list[AgentInvocation],
    session_mcp_calls: list[McpToolCall] | None = None,
) -> dict[str, McpServerUsage]: ...
```

Both models are Pydantic `BaseModel` with `ConfigDict(frozen=True)` — matching every other cross-boundary model in the codebase (`SubagentToolCall`, `AgentInvocation`, `TokenMetrics`). `McpToolCall` rides on `SessionAnalysis.mcp_tool_calls` (which Pydantic serializes), so Pydantic semantics are required. `McpServerUsage` is internal today but kept symmetric with `McpToolCall` for consistency and to avoid a future churn when #118 might bubble it into a signal detail.

## `parse_mcp_tool_name` — split-based parser

Per architect review on the epic plan, use split-based parsing instead of regex. Use FIRST `__` after the `mcp__` prefix as the delimiter (not last) — the last-delimiter approach breaks on leading-underscore tool names like `mcp__srv___internal_sync` where `rfind` would produce `("srv_", "internal_sync")` instead of the correct `("srv", "_internal_sync")`:

```python
def parse_mcp_tool_name(name: str) -> tuple[str, str] | None:
    """Split an MCP tool name into (server, tool).

    Returns None for non-MCP names or malformed shapes. Uses the last
    `__` occurrence as the server/tool delimiter so server names with
    internal underscores parse correctly (e.g.,
    `mcp__claude_ai_Gmail__authenticate` → `("claude_ai_Gmail",
    "authenticate")`).

    Limitation: a server name containing `__` would be fundamentally
    ambiguous in this format. Documented and not handled.
    """
    if not name.startswith("mcp__"):
        return None
    rest = name[5:]  # strip "mcp__"
    idx = rest.find("__")
    if idx <= 0:
        return None
    server, tool = rest[:idx], rest[idx + 2:]
    if not server or not tool:
        return None
    return server, tool
```

Worked examples — must be covered by tests:
- `mcp__github__create_issue` → `("github", "create_issue")` ✓
- `mcp__claude_ai_Gmail__authenticate` → `("claude_ai_Gmail", "authenticate")` ✓
- `mcp__srv___leading_underscore_tool` → `("srv", "_leading_underscore_tool")` ✓
- `mcp__github__` → `None` (empty tool)
- `mcp__` → `None` (empty server)
- `mcp_github_create` → `None` (missing `__` separator)
- `Bash` → `None` (not MCP)

## `extract_mcp_calls_from_messages`

Walks `list[SessionMessage]`. For each assistant message, iterate `tool_use_blocks` and match each via `parse_mcp_tool_name`. For matches, pair with the corresponding `tool_result` content block in a later user message (match by `tool_use_id`) to determine `is_error`.

Error detection priority:
1. `tool_result.is_error == True` → error
2. `tool_result.is_error` absent AND text matches `ERROR_REGEX` → error (reuse the existing ERROR_REGEX from `diagnostics/signals.py`)
3. Otherwise → not an error

Missing `tool_result` (e.g., interrupted session) → `is_error=False` (no evidence of failure). Log nothing — interrupted sessions are common and don't warrant a warning per call.

```python
def extract_mcp_calls_from_messages(
    messages: list[SessionMessage],
) -> list[McpToolCall]:
    # First pass: index tool_result content blocks by tool_use_id.
    results_by_id: dict[str, tuple[str, bool | None]] = {}
    for msg in messages:
        if msg.type != "user":
            continue
        for block in msg.content_blocks:
            if block.type == "tool_result" and block.tool_use_id:
                results_by_id[block.tool_use_id] = (block.text or "", block.is_error)

    calls: list[McpToolCall] = []
    for msg in messages:
        if msg.type != "assistant":
            continue
        for tu in msg.tool_use_blocks:
            parsed = parse_mcp_tool_name(tu.name)
            if parsed is None:
                continue
            server, tool = parsed
            result_text, explicit_error = results_by_id.get(tu.id, ("", None))
            if explicit_error is True:
                is_error = True
            elif explicit_error is False:
                is_error = False
            else:
                is_error = bool(ERROR_REGEX.search(result_text))
            calls.append(
                McpToolCall(server_name=server, tool_name=tool, is_error=is_error),
            )
    return calls
```

## `extract_mcp_usage` — aggregation

Accumulates across both sources:

```python
def extract_mcp_usage(
    invocations: list[AgentInvocation],
    session_mcp_calls: list[McpToolCall] | None = None,
) -> dict[str, McpServerUsage]:
    """Aggregate observed MCP tool usage per server.

    Pulls from two sources:

    - Subagent traces attached to invocations
      (`inv.trace.tool_calls`). Each call's `tool_name` is parsed; if
      it's an MCP name, counted.
    - Parent-session MCP calls collected by
      `extract_mcp_calls_from_messages`, passed in via
      `session_mcp_calls` (None means "no session-level data").

    Returns an empty dict when no MCP tools appear in either source.
    """
    agg: dict[str, _Accumulator] = defaultdict(_Accumulator)

    for inv in invocations:
        trace = inv.trace
        if trace is None:
            continue
        for call in trace.tool_calls:
            parsed = parse_mcp_tool_name(call.tool_name)
            if parsed is None:
                continue
            server, tool = parsed
            agg[server].add(tool, is_error=call.is_error)

    for call in session_mcp_calls or []:
        agg[call.server_name].add(call.tool_name, is_error=call.is_error)

    return {
        server: a.build(server) for server, a in agg.items()
    }
```

Where `_Accumulator` is a private dataclass tracking `tools: set[str]`, `total_calls: int`, `error_count: int`. Its `.build(server_name)` returns an `McpServerUsage` with `unique_tools` sorted for deterministic output.

## Analytics pipeline wiring

Add field to `SessionAnalysis`:

```python
class SessionAnalysis(BaseModel):
    # ... existing fields ...
    mcp_tool_calls: list[McpToolCall] = Field(default_factory=list)
```

Populate in `analyze_session` (single additional line near the existing `extract_agent_invocations` call):

```python
# analytics/pipeline.py, inside analyze_session
messages = parse_session(path)
# ... existing token/tool metrics extraction ...
invocations = extract_agent_invocations(messages)
# NEW
mcp_tool_calls = extract_mcp_calls_from_messages(messages)
# ... rest of pipeline ...
return SessionAnalysis(
    # ... existing fields ...
    mcp_tool_calls=mcp_tool_calls,
)
```

Note: `McpToolCall` is a frozen dataclass but Pydantic v2 models serialize dataclasses fine. Validate with a round-trip test if in doubt.

## Tests

### `tests/unit/test_mcp_assessment.py` (~15 tests)

- **`TestParseMcpToolName`** (6)
  - simple server + tool
  - server with underscores
  - tool with leading underscore
  - empty server (`mcp____tool` → None)
  - empty tool (`mcp__srv__` → None)
  - non-MCP prefix → None

- **`TestExtractMcpCallsFromMessages`** (6)
  - assistant message with mcp__github__create_issue tool_use paired with success tool_result
  - same pattern but `is_error=True` on tool_result
  - tool_use without paired tool_result → is_error=False (no evidence)
  - tool_use with text matching ERROR_REGEX but no `is_error` field → is_error=True
  - tool_use with explicit `is_error=False` but text containing "error" keyword → is_error=False (explicit field wins; documents ERROR_REGEX fallback's false-positive surface)
  - non-MCP tool_use blocks ignored (mixed with MCP ones)

- **`TestExtractMcpUsage`** (4)
  - trace-only path: invocation with trace containing MCP tools, no session calls
  - session-only path: no invocations, only session_mcp_calls
  - both sources: aggregated, unique_tools is union (sorted)
  - empty inputs → empty dict

### `tests/unit/test_analytics_pipeline.py` (+1 test)

- `analyze_session` on a fixture containing MCP tool calls populates `mcp_tool_calls` on the result

### Fixtures

Minimal JSONL:

- **`session_with_mcp_success.jsonl`** — 1 user prompt + 1 assistant with `mcp__github__create_issue` tool_use + 1 user with successful tool_result
- **`session_with_mcp_errors.jsonl`** — assistant with `mcp__nonexistent__bad_call` tool_use + user with `is_error: true` tool_result

Build via existing `tests/_builders.py` helpers if they cover this shape; otherwise write JSONL inline in test setup.

## Edge cases

- **Mixed MCP and non-MCP tools in same invocation's trace** — only MCP names counted; non-MCP silently skipped (the `parse_mcp_tool_name is None` branch).
- **Trace-path MCP calls vs session-path MCP calls for the same session** — both paths are scanned; duplication shouldn't occur because a trace is scoped to a subagent, and session-level MCP calls happen outside any trace.
- **Interrupted session** — tool_use with no paired tool_result → counted as not-error (no evidence of failure, can't assume).
- **`ERROR_REGEX` match on non-error tool output** — false positive risk. The gate only applies when `is_error` field is absent; if the field is present and False, we trust it. This mirrors the existing metadata-layer ERROR_PATTERN detection.
- **Tool name with ONE underscore** (e.g., `mcp__srv_tool`) — fails `find("__")` (only one `__`), returns None. Expected: this isn't a valid MCP name.

## Verification

```bash
uv run pytest tests/unit/test_mcp_assessment.py -v
uv run pytest tests/unit/test_analytics_pipeline.py -v
uv run pytest -m "not integration" -q           # full suite: 611 → ~626
uv run mypy src/agentfluent/
uv run ruff check src/ tests/
# Dogfood: extract from real session data
uv run python -c "from pathlib import Path; from agentfluent.analytics.pipeline import analyze_session; s = analyze_session(Path('~/.claude/projects/<slug>/<uuid>.jsonl').expanduser()); print({c.server_name for c in s.mcp_tool_calls})"
```

## Definition of done

- `parse_mcp_tool_name` uses `find("__")` split; all 7 worked examples covered by tests
- `McpToolCall` and `McpServerUsage` dataclasses in `diagnostics/mcp_assessment.py`
- `extract_mcp_calls_from_messages(messages)` returns list of `McpToolCall`
- `extract_mcp_usage(invocations, session_mcp_calls=None)` returns aggregated dict
- `SessionAnalysis.mcp_tool_calls` field populated by `analyze_session`
- ~15 new tests; ruff + mypy strict clean
- No regression in 611 existing tests

## Risks

1. **Parent-session MCP calls with no tool_result pair** — rare, but happens in interrupted sessions. Gating on `is_error=False` (no evidence) is the safe default; don't over-count errors.
2. **`ERROR_REGEX` false positives** — used only when `is_error` is absent. Real data should rarely hit this branch; metadata-layer ERROR_PATTERN detection has the same gate and hasn't shown major FP issues.
3. **`McpToolCall` on `SessionAnalysis`** — adds a field consumed only by #118 wiring. If #118 gets delayed/cut, this field becomes vestigial. Acceptable — the field is small and doesn't break anything.
4. **Analytics layer changes touch existing tests** — `SessionAnalysis(...)` constructions in tests may need to accept the new default. Mitigated by making `mcp_tool_calls` default to an empty list.
