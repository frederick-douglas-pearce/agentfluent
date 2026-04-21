# PRD: AgentFluent v0.3 -- Deep Diagnostics

**Status:** Draft
**Date:** 2026-04-20
**Author:** PM Agent
**Decision log:** See `decisions.md` D009-D012 for key decisions made during planning.
**Backlog:** See `backlog-v0.3.md` for the full epic/story breakdown.

---

## 1. Problem Statement

AgentFluent v0.2 ships execution analytics, configuration assessment, and a diagnostics preview based on parent-session metadata (total_tokens, tool_uses, duration_ms, output text patterns). The recommendations are directionally correct but lack evidence depth -- "this agent seems slow" instead of "this agent retried Read 4 times on a missing file."

Full subagent JSONL traces exist at `~/.claude/projects/<project>/<session-uuid>/subagents/agent-<agentId>.jsonl` (350+ files discovered across projects). These contain complete tool_use/tool_result sequences, `is_error` flags, per-step token usage, and internal reasoning. Parsing these traces transforms AgentFluent from "pattern-matching on summaries" to "evidence-based diagnostics with per-tool-call proof."

This is the release where AgentFluent delivers on its core differentiator: **behavior-to-improvement diagnostics backed by specific, observable evidence from the agent's own execution trace.**

## 2. Why This Release

AgentFluent's tagline is "tells you what to change." v0.2 proved the concept with metadata-level analysis. v0.3 makes it real:

- **From heuristic to evidentiary.** Instead of "tokens/tool_use is 2x average, consider more specific instructions," v0.3 says "the agent called Bash 3 times with the same failing command, each time getting `permission denied`. Add `/usr/bin/chmod` to allowed_tools or address the permissions check in the prompt."
- **From generic to prescriptive.** Delegation pattern recognition (#92) doesn't just say "you use general-purpose too much" -- it produces a copy-paste-ready agent definition with description, model, tools, and prompt.
- **From cost-blind to cost-aware.** Model-routing diagnostics (#95) tells you exactly which agents are overspec'd and how much you'd save by switching.

No competing tool offers this combination for local-first agent development. LangSmith, Braintrust, and Phoenix all focus on trace visualization -- none correlate observed behavior back to specific config file changes.

## 3. Goals and Non-Goals

### Goals

1. Parse subagent JSONL traces and link them to parent session invocations
2. Surface per-tool-call evidence (errors, retries, failure sequences) in diagnostics
3. Recommend custom subagents from clustered general-purpose invocations (#92)
4. Recommend model changes based on observed task complexity (#95)
5. Support non-default Claude config directories (#90)
6. Maintain v0.2's quality bar: all features tested, typed, linted

### Non-Goals

- LLM-powered analysis (stays rule-based + traditional ML for clustering)
- Runtime agent routing or interception
- Auto-modifying agent config files
- Webapp dashboard (deferred to v0.4+)
- Cross-project aggregation
- Prompt regression detection (`agentfluent diff`) -- requires multi-version data
- Internal reasoning quality analysis (requires LLM scoring)

## 4. Target User and Use Cases

**Primary:** Python developers building Claude Code subagents who have accumulated session data in `~/.claude/projects/` and want to understand why their agents underperform.

**Use cases:**
1. "My PM agent is slow and expensive -- what's wrong?" -> Deep diagnostics show it retries Write operations due to hook permission errors. Recommendation: add `Write` to allowed_tools in the agent definition or configure the hook to permit it.
2. "I keep delegating to general-purpose for similar tasks" -> Delegation diagnostics cluster the invocations and produce a purpose-built agent definition draft.
3. "Am I using the right model for each agent?" -> Model-routing diagnostics flag an Opus agent doing simple reads and estimate $X/month savings from switching to Haiku.
4. "My Claude config is in a non-standard location" -> `--claude-config-dir` flag resolves the path for all commands.

## 5. Scope

### In Scope (Must-Include)

| # | Feature | Epic |
|---|---------|------|
| 1 | Subagent JSONL trace parser (discover, model, parse, link to parent) | Subagent Trace Parser |
| 2 | Deep diagnostics engine (per-tool-call error/retry/failure evidence) | Deep Diagnostics Engine |
| 3 | `--claude-config-dir` flag + `CLAUDE_CONFIG_DIR` env var (#90) | Config Directory Override |
| 4 | Delegation pattern recognition with subagent draft output (#92) | Delegation Pattern Recognition |
| 5 | Model-routing diagnostics with cost-savings estimates (#95) | Model-Routing Diagnostics |

### Stretch Scope

| # | Feature | Epic |
|---|---------|------|
| 6 | MCP server config assessment (audit `mcp__<server>__*` usage vs config) | MCP Config Assessment |

### Out of Scope (Deferred)

- **#80 Historical pricing data structure** -- useful for accurate retrospective cost analysis but not required. v0.3 uses current rates with noted limitation.
- **#81 Session-timestamp cost calculation** -- depends on #80.
- **#82 Automated pricing-update service** -- depends on #80.
- **#97 Hosted documentation site** -- orthogonal to analytics features; can land independently.
- **#96 release-please Node.js migration** -- CI chore, not blocked until June 2026.
- **LLM-powered prompt quality scoring** -- requires API calls, breaks local-first constraint.
- **Prompt regression detection** (`agentfluent diff`) -- requires multiple prompt versions over time.
- **Webapp dashboard** -- v0.4+ per delivery strategy.
- **Agent SDK source parsing** -- deferred until Agent SDK test data exists.

### Stretch Scope Detail: MCP Config Assessment

**What it does:** Audit observed MCP tool usage (tool names matching `mcp__<server>__*` in JSONL and subagent traces) against configured MCP servers. Surface: unused configured servers, missing servers implied by failed `mcp__*` tool attempts, recommendations for adjusting MCP configuration.

**Why stretch (not in-scope):** The must-include scope (subagent trace parser + deep diagnostics + 3 enhancement issues) is already substantial. The subagent parser is the critical-path dependency for everything else and carries genuine complexity. MCP assessment is architecturally clean but additive -- it doesn't deepen the core diagnostic capability, it widens it to another config surface.

**Why stretch (not deferred):** MCP tool names are already visible in the data the subagent parser surfaces. The developer can build the shared "audit config surface against observed usage" framework (used by both model-routing and MCP) knowing MCP is coming. Scoping it as stretch means the architecture accommodates it even if the stories themselves slip.

**Cut criteria:** If the must-include scope fills the release timeline, MCP stories are cut cleanly. The epic label and stories remain in the backlog for v0.4.

## 6. Functional Requirements

### 6.1 Subagent Trace Parser

**Input:** `~/.claude/projects/<project>/<session-uuid>/subagents/agent-<agentId>.jsonl`

**Behavior:**
- Discover subagent directories within session directories
- Parse subagent JSONL files (same message format as parent, all messages have `isSidechain: true`)
- Link subagent files to parent session invocations via `agentId` field
- Extract per-tool-call data: tool name, input summary, result content, `is_error` flag, token usage
- Build a `SubagentTrace` model containing the ordered sequence of tool calls with results

**Key data model additions:**
- `SubagentTrace`: agent_id, agent_type, delegation_prompt, tool_calls (list), total_errors, total_retries, token_usage
- `SubagentToolCall`: tool_name, input_summary, result_summary, is_error, tokens, timestamp
- `RetrySequence`: tool_name, attempts (count), first_error, last_error, eventual_success (bool)

**Constraints:**
- Parse lazily (don't load all 350+ files at once) -- parse on demand per session analysis
- Handle missing/corrupt subagent files gracefully (warn, continue)
- Handle subagent files that reference non-existent parent invocations (orphans -- report, don't crash)

### 6.2 Deep Diagnostics Engine

**Behavior:**
- Analyze `SubagentTrace` data to detect diagnostic signals with per-tool-call evidence
- New signal types:
  - `TOOL_ERROR_SEQUENCE`: consecutive `is_error: true` results for the same tool
  - `RETRY_LOOP`: same tool called repeatedly with similar input (edit-distance threshold)
  - `PERMISSION_FAILURE`: tool_result content matching permission/access patterns
  - `STUCK_PATTERN`: agent produces same tool call > 3 times without progress
- Each signal includes evidence: specific tool calls, timestamps, error content
- Recommendations reference both the observed behavior AND the specific config change

**Recommendation examples (evidence-backed):**
- "Agent 'pm' called Write 3 times, all failing with 'blocked by hook'. Evidence: [timestamps, error text]. Fix: add Write to `tools` in `~/.claude/agents/pm.md` or adjust `.claude/hooks/` permissions."
- "Agent 'general-purpose' entered a retry loop on Bash (5 attempts, same `cd /nonexistent && ls` command). Fix: add error handling instructions to the delegation prompt -- 'If a directory does not exist, report the failure rather than retrying.'"

### 6.3 Config Directory Override (#90)

Per issue #90's spec. Key points:
- `--claude-config-dir <path>` on `list`, `analyze`, `config-check`
- `CLAUDE_CONFIG_DIR` env var fallback
- Resolution: flag > env > `~/.claude/` default
- Project-scope `.claude/agents/` stays CWD-relative (unaffected)

### 6.4 Delegation Pattern Recognition (#92)

Per issue #92's spec. Key points:
- TF-IDF + LSA + KMeans clustering of general-purpose invocations
- Structured subagent draft output (description, model, tools, prompt template)
- Dedup against existing agents via cosine similarity
- `--min-cluster-size` (default: 5) and `--min-similarity` (default: 0.6) flags
- Integrates into `agentfluent analyze --diagnostics` Recommendations table
- `scikit-learn` dependency added

**Enhancement from subagent traces:** When subagent trace data is available, the tools list in the draft recommendation is derived from *actual tool usage* in the clustered invocations' traces (not just the count from metadata). This makes the `tools` field in the draft agent definition accurate.

### 6.5 Model-Routing Diagnostics (#95)

Per issue #95's spec. Key points:
- Classify per-agent task complexity into tiers (simple/moderate/complex) based on tool patterns, token usage, error rate
- Compare observed tier against model actually used
- Emit overspec'd and underspec'd recommendations with cost-savings estimates
- Uses current pricing rates (not historical -- see D012 re: #80 dependency)

**Open questions (to resolve before implementation):**
1. **Heuristic definition:** What thresholds separate "Haiku task" from "Opus task"? Proposed starting point: read-only tools + < 5 tool calls + < 2k tokens = simple; write tools + > 10 tool calls = complex; else moderate.
2. **Agents only or interactive too?** Recommend agents-only for v0.3 (per #95's own recommendation). Interactive sessions are a v0.4 follow-up.
3. **Pricing dependency:** Use current rates for "what would it cost going forward" estimates. Do not block on #80 for historical accuracy.

### 6.6 MCP Config Assessment (Stretch)

**Behavior:**
- Extract MCP tool usage from session/subagent data (tool names matching `mcp__<server>__*`)
- Identify configured MCP servers from `.claude/settings.json` or project settings
- Surface:
  - Configured servers with zero observed usage ("unused server: consider removing")
  - `mcp__*` tool call failures suggesting missing server configuration
  - MCP tools used successfully but not from a configured server (implicit/inherited)
- Emit recommendations with `target: "mcp"` in the diagnostics output

## 7. Technical Approach

### Central Addition: Subagent Trace Parser

The subagent trace parser is the upstream dependency for deep diagnostics, enhanced delegation recognition, and (indirectly) model-routing. It adds a new subpackage:

```
agentfluent/
  traces/                   # NEW: subagent trace parsing
    __init__.py
    discovery.py            # Find subagent dirs within session dirs
    parser.py               # Parse subagent JSONL into SubagentTrace
    models.py               # SubagentTrace, SubagentToolCall, RetrySequence
    linker.py               # Link traces to parent AgentInvocation via agentId
```

The existing `diagnostics/` package gains new signal types and correlation rules:

```
diagnostics/
  signals.py                # Extended: new signal types for trace-level evidence
  correlator.py             # Extended: new rules using SubagentTrace data
  delegation.py             # NEW: clustering + draft generation (#92)
  model_routing.py          # NEW: complexity classification + model recommendations (#95)
```

### Integration Points

1. `core/parser.py` remains unchanged -- it parses parent sessions
2. `traces/parser.py` reuses the same message format but adds `isSidechain` handling
3. `agents/extractor.py` already produces `AgentInvocation` with `agent_id` -- the linker matches these to `SubagentTrace` objects
4. `diagnostics/signals.py` gains new `SignalType` enum values and extraction functions that consume `SubagentTrace`
5. `diagnostics/correlator.py` gains new `CorrelationRule` implementations
6. CLI `analyze` command orchestrates: parse session -> extract agents -> parse traces -> link -> deep diagnostics

### Dependency Graph

```
[Config Dir Override (#90)] -- independent, no dependencies

[Subagent Trace Parser] -- depends on existing parser infrastructure
    |
    v
[Deep Diagnostics Engine] -- depends on trace parser
    |
    +--> [Delegation Pattern Recognition (#92)] -- depends on traces for tool lists
    |
    +--> [Model-Routing Diagnostics (#95)] -- depends on traces for complexity signals
    |
    +--> [MCP Config Assessment (stretch)] -- depends on traces for MCP tool names
```

### New Dependency

- `scikit-learn` (for #92 clustering). Runtime dependency. Acceptable for a Python analytics CLI tool.

## 8. Success Metrics

v0.3 is successful when:

1. `agentfluent analyze --diagnostics` on a session with subagent traces produces recommendations citing specific tool calls as evidence
2. Recommendations include the exact tool name, error content, and timestamp -- not just "errors detected"
3. Delegation recognition clusters general-purpose invocations and produces a structured agent draft when >= 5 similar invocations exist
4. Model-routing diagnostics flag at least one overspec'd or underspec'd agent in test data and include a cost-savings estimate
5. `--claude-config-dir` works for all commands, tested with a non-default path
6. All new code has >80% test coverage
7. No regressions in existing v0.2 functionality

## 9. Sequencing

Implementation order:

1. **E1: Config Directory Override** (#90) -- independent, low risk, immediate usability win
2. **E2: Subagent Trace Parser** -- critical path, everything else depends on it
3. **E3: Deep Diagnostics Engine** -- builds on trace parser, unlocks evidence-based recommendations
4. **E4: Delegation Pattern Recognition** (#92) -- builds on traces + existing diagnostics infrastructure
5. **E5: Model-Routing Diagnostics** (#95) -- builds on traces + existing diagnostics infrastructure
6. **E6: MCP Config Assessment** (stretch) -- builds on traces, scoped cleanly for cut

E4 and E5 can be developed in parallel once E2+E3 are complete.

## 10. Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Subagent JSONL format varies across Claude Code versions | Parser breaks on older/newer files | Defensive parsing with `extra="ignore"` on models; test against real data from multiple time periods |
| Subagent files are large (some > 1MB) | Memory/performance issues | Lazy parsing; stream line-by-line; don't load all traces at once |
| TF-IDF clustering quality poor on short descriptions | #92 produces bad recommendations | Confidence scoring + cohesion threshold filters low-quality clusters; sentence-transformers upgrade path documented |
| scikit-learn dependency size concerns | Package bloat for a CLI tool | scikit-learn is ~30MB installed; acceptable for analytics tooling. If problematic, make it an optional extra (`agentfluent[clustering]`) |
| Model-routing heuristics too simplistic | False positive recommendations | Start conservative (high thresholds, suppress borderline cases); include confidence signaling; expose threshold flags |
| Scope creep into LLM features | Delays release | Hard boundary maintained: all analysis is rule-based or traditional ML. LLM features require explicit D0xx decision. |

## 11. Open Questions

1. **Model-routing heuristics:** What thresholds define task complexity tiers? Need empirical calibration against real session data before finalizing. Propose: implement with configurable thresholds, run against existing data, tune before release.
2. **Retry detection sensitivity:** How similar must two tool calls be to count as a "retry"? Exact match on tool name + input? Or fuzzy match on input? Start with exact tool name + input edit distance < 20%.
3. **Subagent trace depth:** Some traces have 50+ tool calls. Should deep diagnostics analyze all of them, or focus on error sequences only? Propose: extract all, report only those containing signals (errors, retries, stuck patterns).
4. **#92 tool list derivation:** When subagent traces are not available for a clustered invocation (older sessions pre-dating trace discovery), should the draft omit the `tools` field or use a generic default? Propose: omit with a note "run with newer session data for tool recommendations."
5. **Should model-routing cover custom subagents only, or also general-purpose?** The general-purpose agent's model is set by the system (Opus by default). Recommending a model change for it requires the user to create a custom agent with a different model. This overlaps with #92's output. Propose: cover both, but for general-purpose, phrase the recommendation as "create a custom agent with Haiku/Sonnet for this task pattern" (which links to #92's output).
