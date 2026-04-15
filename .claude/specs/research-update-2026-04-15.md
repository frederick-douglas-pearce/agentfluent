# Research Update: Subagent JSONL Trace Discovery

**Date:** 2026-04-15
**Status:** Supersedes the "Subagent Session Data Analysis" section of `docs/AGENT_ANALYTICS_RESEARCH.md`
**Context:** The AgentFluent research doc at `docs/AGENT_ANALYTICS_RESEARCH.md` contains outdated information about subagent data availability. This update documents the corrected state. The `docs/` version should be updated with this content when a developer next touches the research doc.

---

## What Changed

The AgentFluent research doc (copied from CodeFluent pre-discovery) states:

> "Subagent activity is **inlined into the parent session file** -- no separate JSONL files are created."

This is incorrect. As of April 15, 2026, the CodeFluent project discovered that full subagent session traces exist at:

```
~/.claude/projects/<project>/<session-uuid>/subagents/agent-<agentId>.jsonl
```

**350 subagent files** were found across projects. The original analysis only examined top-level JSONL files and missed the `<session-uuid>/subagents/` subdirectory structure.

---

## Corrected Data Availability

Subagent data exists in **two locations**:

### 1. Parent Session (summary only)

When Claude spawns a subagent, the parent session contains:
1. Assistant message with `tool_use` block where `name: "Agent"` and `input` contains `subagent_type`, `description`, and `prompt`
2. A `tool_result` block with the subagent's final summary + metadata (`total_tokens`, `tool_uses`, `duration_ms`, `agentId`)

### 2. Separate Subagent JSONL Files (full internal traces)

Each subagent session is written to `~/.claude/projects/<project>/<session-uuid>/subagents/agent-<agentId>.jsonl`. These files contain **complete session data**:
- User prompts (delegation prompt as first message)
- Assistant responses with per-step token usage
- tool_use/tool_result pairs for every internal tool call
- `is_error` flags on tool_result blocks
- Internal reasoning steps (full assistant response content)
- All messages have `isSidechain: true`

### Updated Data Availability Table

| Data Point | Parent JSONL (summary) | Subagent JSONL (full trace) |
|---|---|---|
| Which agent was invoked | Yes (`subagent_type` field) | Yes (`agentId` on all messages) |
| Delegation prompt | Yes (`prompt` in Agent tool input) | Yes (first user message) |
| Agent description | Yes (`description` field) | -- |
| Final result/summary | Yes (`tool_result` content) | Yes (last assistant message) |
| Total tokens per invocation | Yes (metadata `total_tokens`) | Yes (sum of assistant usage) |
| Tool use count per invocation | Yes (metadata `tool_uses`) | Yes (individual tool_use blocks) |
| Duration per invocation | Yes (metadata `duration_ms`) | Yes (timestamp span) |
| Agent session ID | Yes (metadata `agentId`) | Yes (`agentId` field) |
| **Internal tool calls** | **No** | **Yes** -- Read, Bash, Grep, Edit, etc. with full input/output |
| **Internal tool results** | **No** | **Yes** -- including `is_error`, exit codes, error content |
| **Internal reasoning steps** | **No** | **Yes** -- full assistant response content |
| **Retries and errors** | **No** | **Yes** -- tool_result with `is_error: true` |
| **Per-step token usage** | **No** | **Yes** -- usage on each assistant message |

---

## Impact on AgentFluent Feature Feasibility

### Previously Classified as "Requires Agent SDK Data" -- Now Feasible

These features were listed in the original research as requiring Agent SDK migration. They are now feasible with existing Claude Code subagent data:

1. **Prompt-to-behavior correlation** -- delegation prompt (from parent) + full internal traces (from subagent file) provide the complete picture
2. **Detailed error analysis** -- which tool failed, what the error was, how many retries -- all in subagent tool_result blocks
3. **Internal reasoning analysis** -- full assistant response content available in subagent files
4. **Error recovery patterns** -- same detection algorithms apply to subagent tool_use/tool_result sequences
5. **Retry sequence analysis** -- consecutive tool_use/tool_result pairs for the same tool are visible

### Genuinely Requires Agent SDK (production agent use case only)

These remain Agent SDK-specific because they involve programmatic agent workflows, not the data format:

- Programmatic prompt version management -- hardcoded prompts in application code vs interactive prompts
- CI/CD integration -- automated agent runs without a human in the loop
- Custom instrumentation hooks -- programmatic callbacks vs shell command hooks
- Batch analysis across hundreds/thousands of programmatic agent runs

---

## Corrected Bootstrap Strategy

The original bootstrap strategy had the AgentFluent trigger as: "when per-tool-call traces from the Agent SDK become essential." This is superseded.

**Updated trigger:** AgentFluent as a separate product is triggered by **audience divergence** -- when it needs to serve Agent SDK/production users with fundamentally different workflows (CI/CD integration, programmatic prompt versioning, batch analysis) that don't fit the interactive focus of CodeFluent.

**Updated sequence:**
1. **Done:** Deploy custom PM agent for real work in Claude Code. (April 14)
2. **Done:** Discovered full subagent traces in `<session-uuid>/subagents/agent-<id>.jsonl`. (April 15)
3. **AgentFluent MVP (v1.0):** Execution analytics + config assessment + diagnostics preview using parent session metadata. Subagent files enumerated but not parsed.
4. **AgentFluent v1.1:** Subagent trace parser + deep diagnostics with per-tool-call evidence. This is the "wow" release where recommendations come with specific tool-call evidence.
5. **AgentFluent v1.2+:** Agent SDK source parsing, CI/CD integration, prompt regression detection, LLM-powered analysis.

---

## Corrected "Implications for AgentFluent Prototype" Section

Replace the split between "testable with subagent data" and "requires Agent SDK data" with:

**Fully available from subagent JSONL files (v1.1):**
- All parent-session metadata signals (invocation frequency, delegation effectiveness, output quality, cost attribution, efficiency metrics, failure detection, continuity patterns)
- Prompt-to-behavior correlation -- delegation prompt + full internal traces
- Detailed error analysis -- which tool failed, what the error was, how many retries
- Internal reasoning analysis -- full assistant response content
- Error recovery patterns -- same detection algorithm as main session analysis
- Retry sequence analysis -- consecutive same-tool calls

**Genuinely requires Agent SDK (different user persona, different workflows):**
- Programmatic prompt version management
- CI/CD integration and automated analysis
- Custom instrumentation hooks
- Batch analysis at scale (hundreds+ of programmatic agent runs)

---

## Action Items

1. **AgentFluent `docs/AGENT_ANALYTICS_RESEARCH.md`** needs updating to correct the "no separate JSONL files" statement and the "Observable vs Hidden" table. This should happen when a developer next works on the research doc. The corrected content is in this file.
2. **AgentFluent MVP PRD** (`prd-mvp.md`) "Out of Scope" section should note that subagent trace parsing is deferred to v1.1, not because of data unavailability but to keep MVP scope bounded. See D008.
3. **CLAUDE.md** "JSONL Data Format" section should document the `<session-uuid>/subagents/agent-<agentId>.jsonl` path pattern.
