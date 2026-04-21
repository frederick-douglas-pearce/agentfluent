# AgentFluent v0.3 Backlog

Full epic and story breakdown for v0.3 (Deep Diagnostics). Each section below maps to a GitHub issue.

**Label conventions:**
- Epic labels: `epic:config-dir-override`, `epic:subagent-traces`, `epic:deep-diagnostics`, `epic:delegation-patterns`, `epic:model-routing`, `epic:mcp-assessment`
- Type labels: `enhancement`, `testing`, `stretch`
- Priority labels: `priority:high`, `priority:medium`, `priority:low`

**Milestone:** v0.3.0

---

## E1: Config Directory Override

**Epic issue:** #90 (existing issue serves as the epic -- fully specced)
**Label:** `epic:config-dir-override`
**Dependencies:** None
**Sizing:** Small (1-2 days)

#90 is self-contained with detailed acceptance criteria. It can be implemented as a single PR with no decomposition needed. The implementation plumbing (discovery.py `base_path`, scanner.py `user_path`) is already in place.

### Stories

- [ ] #90 -- Add `--claude-config-dir` flag + `CLAUDE_CONFIG_DIR` env var (existing issue, fully specced)

---

## E2: Subagent Trace Parser

**Label:** `epic:subagent-traces`
**Dependencies:** Existing `core/parser.py` infrastructure
**Sizing:** Large (5-7 days)

### Summary

Parse subagent JSONL trace files at `<session-uuid>/subagents/agent-<agentId>.jsonl`, build typed models for per-tool-call data, and link traces to parent session invocations. This is the critical-path foundation for all deep diagnostics features.

### Success Criteria

- [ ] Subagent directories discovered within session directories
- [ ] Subagent JSONL files parsed into `SubagentTrace` models
- [ ] Traces linked to parent `AgentInvocation` via `agentId`
- [ ] Per-tool-call data extracted: tool name, is_error, result summary, tokens
- [ ] Retry sequences identified (consecutive same-tool calls)
- [ ] Performance: lazy parsing, handles large traces (50+ tool calls) without memory issues
- [ ] Unit tests with fixture subagent traces; integration tests against real data

### Stories

---

#### E2-S1: Define subagent trace data models

**Title:** Define subagent trace data models
**Labels:** `epic:subagent-traces`, `enhancement`, `priority:high`

**Summary:** Define Pydantic models for subagent trace data. These are the contract between the trace parser and downstream diagnostics consumers.

**Acceptance Criteria:**
- `SubagentTrace` captures: agent_id, agent_type, delegation_prompt (from first user message), tool_calls (list of SubagentToolCall), total_errors (count), total_retries (count), usage (aggregate Usage), duration_ms
- `SubagentToolCall` captures: tool_name, input_summary (truncated to 200 chars), result_summary (truncated to 500 chars), is_error (bool), usage (Usage), timestamp
- `RetrySequence` captures: tool_name, attempts (int), first_error_message, last_error_message, eventual_success (bool), tool_call_indices (list of int referencing SubagentTrace.tool_calls)
- Models handle missing fields gracefully (subagent format may vary)
- Models in `agentfluent.traces.models`

**Dependencies:** None (new subpackage)

---

#### E2-S2: Implement subagent directory discovery

**Title:** Implement subagent directory discovery
**Labels:** `epic:subagent-traces`, `enhancement`, `priority:high`

**Summary:** Discover `<session-uuid>/subagents/` directories and enumerate subagent JSONL files within them.

**Acceptance Criteria:**
- Given a project path, `discover_subagent_files(project_path)` returns a mapping of session_id -> list of subagent file paths
- Given a session path (directory), `discover_session_subagents(session_dir)` returns subagent file paths
- Subagent files are identified by pattern: `agent-<uuid>.jsonl` in `subagents/` subdirectory
- Returns the `agentId` extracted from the filename (the UUID portion)
- Handles missing `subagents/` directory (returns empty)
- Handles sessions that are files-only (no directory counterpart)
- Module at `agentfluent.traces.discovery`

**Dependencies:** E1 (#90) for respecting `--claude-config-dir` base path

---

#### E2-S3: Implement subagent trace parser

**Title:** Implement subagent trace parser
**Labels:** `epic:subagent-traces`, `enhancement`, `priority:high`

**Summary:** Parse subagent JSONL files into `SubagentTrace` models, extracting per-tool-call data.

**Acceptance Criteria:**
- Given a subagent JSONL file path, `parse_subagent_trace(path)` returns a `SubagentTrace`
- Delegation prompt extracted from first `user` message content
- Each assistant `tool_use` block paired with its subsequent `tool_result` to form a `SubagentToolCall`
- `is_error` detected from: explicit `is_error: true` on tool_result, OR error-pattern keywords in result content
- `input_summary` and `result_summary` truncated to configured limits (200/500 chars default)
- Per-step token usage extracted from assistant message `usage` fields
- Messages with `isSidechain: true` are expected (all subagent messages have this)
- Malformed lines skipped gracefully (same behavior as core parser)
- Large files (50+ tool calls) parsed without loading entire file into memory
- Module at `agentfluent.traces.parser`

**Dependencies:** E2-S1 (models)

---

#### E2-S4: Implement retry sequence detection

**Title:** Implement retry sequence detection in subagent traces
**Labels:** `epic:subagent-traces`, `enhancement`, `priority:high`

**Summary:** Identify retry sequences within a parsed subagent trace: consecutive calls to the same tool with similar input.

**Acceptance Criteria:**
- Given a `SubagentTrace`, `detect_retry_sequences(trace)` returns a list of `RetrySequence`
- A retry sequence is: 2+ consecutive tool calls where tool_name matches AND input is similar (exact match or edit distance < 20% of input length)
- Each `RetrySequence` records: tool_name, attempt count, first and last error messages, whether the sequence eventually succeeded, indices into the tool_calls list
- Non-consecutive similar calls (interleaved with other tools) do NOT count as retries
- Single tool calls with no repetition produce no retry sequences
- Module at `agentfluent.traces.parser` (or a separate `analysis.py` if cleaner)

**Dependencies:** E2-S3 (parsed trace with tool_calls)

---

#### E2-S5: Implement trace-to-invocation linker

**Title:** Link subagent traces to parent session invocations
**Labels:** `epic:subagent-traces`, `enhancement`, `priority:high`

**Summary:** Match parsed `SubagentTrace` objects to their parent `AgentInvocation` using the `agentId` field.

**Acceptance Criteria:**
- Given a list of `AgentInvocation` (from existing extractor) and a mapping of agentId -> SubagentTrace, `link_traces(invocations, traces)` enriches each invocation with its trace
- Matching is by `AgentInvocation.agent_id == SubagentTrace.agent_id`
- Invocations without a matching trace retain `trace = None` (older sessions, or trace file missing)
- Traces without a matching invocation are reported as orphans (debug-level log)
- The enriched `AgentInvocation` exposes `trace: SubagentTrace | None`
- Module at `agentfluent.traces.linker`

**Implementation Notes:**
- `AgentInvocation` model needs a new optional field: `trace: SubagentTrace | None = None`
- The linker is called during the analyze pipeline after both extraction and trace parsing complete

**Dependencies:** E2-S1 (trace models), E2-S3 (trace parser), existing agent extractor

---

#### E2-S6: Add subagent trace parser tests

**Title:** Add subagent trace parser unit and integration tests
**Labels:** `epic:subagent-traces`, `testing`, `priority:high`

**Summary:** Comprehensive tests for discovery, parsing, retry detection, and linking.

**Acceptance Criteria:**
- Fixture subagent JSONL files in `tests/fixtures/subagents/` covering:
  - Simple trace with 3 successful tool calls
  - Trace with `is_error: true` tool results
  - Trace with a retry sequence (3 consecutive same-tool calls)
  - Trace with a stuck pattern (5+ identical calls)
  - Empty/malformed file
  - Large trace (20+ tool calls)
- Unit tests validate:
  - Discovery finds files in correct directory structure
  - Parser extracts correct tool_name, is_error, input/result summaries
  - Retry detection identifies sequences and records metadata
  - Linker matches traces to invocations by agentId
  - Linker handles orphans and missing traces gracefully
- Integration tests: parse real subagent traces from `~/.claude/projects/`
- >80% coverage on `traces/` subpackage

**Dependencies:** E2-S1 through E2-S5

---

## E3: Deep Diagnostics Engine

**Label:** `epic:deep-diagnostics`
**Dependencies:** E2 (subagent trace parser)
**Sizing:** Medium (3-5 days)

### Summary

Extend the diagnostics pipeline to consume subagent trace data and produce evidence-backed recommendations. New signal types detect tool-level failures; correlation rules map them to specific config changes with evidence citations.

### Success Criteria

- [ ] New signal types: TOOL_ERROR_SEQUENCE, RETRY_LOOP, PERMISSION_FAILURE, STUCK_PATTERN
- [ ] Each signal includes per-tool-call evidence (tool name, error content, timestamps)
- [ ] Recommendations cite specific evidence, not just pattern names
- [ ] Signals degrade gracefully when trace data is unavailable (fall back to v0.2 metadata signals)
- [ ] Integrated into `agentfluent analyze --diagnostics` output
- [ ] Tests cover each signal type with fixture trace data

### Stories

---

#### E3-S1: Add trace-level signal types to diagnostics models

**Title:** Add trace-level diagnostic signal types
**Labels:** `epic:deep-diagnostics`, `enhancement`, `priority:high`

**Summary:** Extend `SignalType` enum and `DiagnosticSignal` model to support trace-level evidence.

**Acceptance Criteria:**
- New `SignalType` values: `TOOL_ERROR_SEQUENCE`, `RETRY_LOOP`, `PERMISSION_FAILURE`, `STUCK_PATTERN`
- `DiagnosticSignal.detail` dict extended to include:
  - `tool_calls`: list of dicts with `{tool_name, timestamp, error_content}` for evidence
  - `retry_count`: int (for RETRY_LOOP)
  - `stuck_count`: int (for STUCK_PATTERN)
- Existing signal types (ERROR_PATTERN, TOKEN_OUTLIER, DURATION_OUTLIER) unchanged
- Backward compatible: code consuming signals by existing types continues to work

**Dependencies:** E2-S1 (trace models for type references)

---

#### E3-S2: Implement trace-level signal extraction

**Title:** Implement trace-level signal extraction from SubagentTrace data
**Labels:** `epic:deep-diagnostics`, `enhancement`, `priority:high`

**Summary:** Extract deep diagnostic signals from `SubagentTrace` objects.

**Acceptance Criteria:**
- `extract_trace_signals(trace: SubagentTrace) -> list[DiagnosticSignal]`:
  - **TOOL_ERROR_SEQUENCE:** 2+ consecutive `is_error: true` results. Severity: warning (2-3 errors), critical (4+).
  - **RETRY_LOOP:** Detected `RetrySequence` with attempts >= 3. Includes first error message in evidence.
  - **PERMISSION_FAILURE:** Any tool_result containing "permission denied", "blocked", "not allowed", "access denied". Severity: critical.
  - **STUCK_PATTERN:** Same tool called 4+ times with identical input (exact match). Severity: critical.
- Each signal includes evidence in `detail.tool_calls` (limited to first 5 occurrences to avoid output bloat)
- Function handles `None` traces gracefully (returns empty list)
- Module: extend `agentfluent.diagnostics.signals` with a new `extract_trace_signals` function

**Dependencies:** E2-S4 (retry sequences), E3-S1 (signal types)

---

#### E3-S3: Add trace-aware correlation rules

**Title:** Add correlation rules that use trace-level evidence
**Labels:** `epic:deep-diagnostics`, `enhancement`, `priority:high`

**Summary:** New `CorrelationRule` implementations that consume trace-level signals and produce evidence-backed recommendations.

**Acceptance Criteria:**
- `PermissionFailureRule`: PERMISSION_FAILURE signal + agent config -> recommend specific tool additions to `tools` list, citing the blocked tool name
- `RetryLoopRule`: RETRY_LOOP signal -> recommend adding error handling guidance to prompt ("if X fails, do Y instead of retrying"), citing the specific tool and error
- `StuckPatternRule`: STUCK_PATTERN signal -> recommend adding exit conditions to prompt, citing the repeated tool call and input
- `ErrorSequenceRule`: TOOL_ERROR_SEQUENCE signal -> recommend reviewing tool access or adding fallback instructions
- All recommendations include `observation` (with tool-call evidence), `reason`, and `action` fields populated
- Recommendations reference the config file path when `AgentConfig` is available
- Rules integrate into existing correlator rule registry

**Dependencies:** E3-S2 (signal extraction), existing correlator infrastructure

---

#### E3-S4: Integrate trace diagnostics into analyze pipeline

**Title:** Integrate trace-level diagnostics into the analyze command pipeline
**Labels:** `epic:deep-diagnostics`, `enhancement`, `priority:high`

**Summary:** Wire trace parsing and deep diagnostics into the `agentfluent analyze` command.

**Acceptance Criteria:**
- `agentfluent analyze --project X --diagnostics` now:
  1. Parses parent session (existing)
  2. Extracts agent invocations (existing)
  3. Discovers and parses subagent traces (new)
  4. Links traces to invocations (new)
  5. Extracts both metadata-level AND trace-level signals (extended)
  6. Correlates all signals with config (extended)
  7. Formats output with evidence citations (extended)
- When subagent traces exist, diagnostics output includes a "Deep Diagnostics" section with evidence-backed recommendations
- When no traces exist, falls back to v0.2 metadata-level diagnostics (no regression)
- `--format json` includes trace-level signals and evidence in the `diagnostics` key
- `--verbose` shows per-tool-call evidence details; default shows summary counts + top recommendations
- Performance: analyzing a session with 5 subagent traces (each ~30 tool calls) completes in < 5 seconds

**Dependencies:** E2 (all), E3-S1 through E3-S3, existing analyze command

---

#### E3-S5: Add deep diagnostics tests

**Title:** Add deep diagnostics unit and integration tests
**Labels:** `epic:deep-diagnostics`, `testing`, `priority:high`

**Summary:** Tests for trace-level signal extraction, correlation rules, and pipeline integration.

**Acceptance Criteria:**
- Unit tests for each signal type:
  - TOOL_ERROR_SEQUENCE: fixture with consecutive errors -> signal detected with correct evidence
  - RETRY_LOOP: fixture with 3+ retries -> signal with retry count and first error
  - PERMISSION_FAILURE: fixture with "blocked" in result -> critical signal
  - STUCK_PATTERN: fixture with 4 identical calls -> critical signal
  - No errors in trace -> no signals
- Unit tests for correlation rules:
  - PERMISSION_FAILURE + config with tools list -> recommend adding the blocked tool
  - RETRY_LOOP + config -> recommend error handling in prompt
  - STUCK_PATTERN -> recommend exit conditions
- Integration test: full pipeline on a real session with subagent traces produces non-empty diagnostics
- Regression test: session without traces produces same output as v0.2

**Dependencies:** E3-S1 through E3-S4

---

## E4: Delegation Pattern Recognition

**Epic issue:** #92 (existing issue serves as the epic -- fully specced)
**Label:** `epic:delegation-patterns`
**Dependencies:** E2 (subagent traces for tool list derivation), existing diagnostics infrastructure
**Sizing:** Medium-Large (4-6 days)

### Stories

---

#### E4-S1: Implement TF-IDF + KMeans clustering pipeline

**Title:** Implement delegation clustering pipeline (TF-IDF + LSA + KMeans)
**Labels:** `epic:delegation-patterns`, `enhancement`, `priority:high`

**Summary:** Cluster `general-purpose` agent invocations by task shape using description + prompt text.

**Acceptance Criteria:**
- Given a list of `AgentInvocation` objects where `agent_type == "general-purpose"`, `cluster_delegations(invocations)` returns clusters
- Pipeline: TF-IDF vectorization of `description + prompt` -> LSA dimensionality reduction (50 components) -> KMeans clustering
- K auto-selected via silhouette score (range: 2 to min(10, n_samples/5))
- Each cluster includes: member invocations, centroid terms (top 10 TF-IDF features), cohesion score (mean intra-cluster cosine similarity)
- Clusters below `min_cluster_size` (configurable, default 5) are filtered out
- Handles edge cases: < 5 total invocations (return empty), all invocations identical (single cluster), n_samples < k_range start (skip)
- Module at `agentfluent.diagnostics.delegation`
- `scikit-learn` added to `pyproject.toml` dependencies

**Dependencies:** Existing `agents/extractor.py`

---

#### E4-S2: Implement subagent draft generation

**Title:** Generate structured subagent draft recommendations from clusters
**Labels:** `epic:delegation-patterns`, `enhancement`, `priority:high`

**Summary:** For each qualifying cluster, synthesize a copy-paste-ready agent definition with description, model, tools, and prompt.

**Acceptance Criteria:**
- Given a cluster of invocations (with optional SubagentTrace data), `generate_draft(cluster)` returns a structured draft
- **Description:** synthesized from cluster's top TF-IDF terms + most common phrases in description text
- **Model:** derived from tool usage pattern:
  - Read-only tools only (Read, Grep, Glob, WebFetch) -> `claude-haiku-4-5`
  - Write/Edit/Bash with high tokens -> `claude-opus-4-6`
  - Default -> `claude-sonnet-4-5`
- **Tools:** union of tools actually used (from SubagentTrace.tool_calls if available; otherwise omit with note)
- **Prompt:** template with common task pattern filled in (rule-based, not LLM-generated)
- Confidence tier: high (>= 10 invocations, >= 0.8 cohesion), medium (>= 5, >= 0.6), low (>= 5, < 0.6)
- Draft output as structured dict AND renderable YAML string

**Dependencies:** E4-S1 (clusters), E2-S5 (trace linker for tool list derivation)

---

#### E4-S3: Implement existing-agent deduplication

**Title:** Dedup delegation recommendations against existing custom agents
**Labels:** `epic:delegation-patterns`, `enhancement`, `priority:medium`

**Summary:** Before emitting a recommendation, check if a similar custom agent already exists.

**Acceptance Criteria:**
- Given a draft's synthesized description and existing `AgentConfig` objects (from `config/scanner.py`), compute cosine similarity between descriptions (TF-IDF vectors)
- If similarity > 0.7, suppress the recommendation; emit an INFO note instead ("Cluster matches existing agent 'X', similarity: 0.XX")
- Dedup checks both user-scope and project-scope agents
- When no agent configs are found (scanner returns empty), skip dedup and emit recommendations
- Similarity threshold configurable via `--min-similarity` flag

**Dependencies:** E4-S2 (drafts), existing `config/scanner.py`

---

#### E4-S4: Integrate delegation diagnostics into analyze command

**Title:** Integrate delegation pattern recommendations into analyze --diagnostics
**Labels:** `epic:delegation-patterns`, `enhancement`, `priority:high`

**Summary:** Wire the clustering pipeline into the analyze command output.

**Acceptance Criteria:**
- `agentfluent analyze --diagnostics` runs delegation clustering on `general-purpose` invocations
- Recommendations appear in the Recommendations table with `target: "agent"`
- Each recommendation shows: suggested agent name, confidence tier, cluster size, invocation count
- `--verbose` prints the full YAML draft block below the table
- `--format json` includes the full structured draft in the recommendation object
- `--min-cluster-size` and `--min-similarity` flags accepted by the analyze command
- When < 5 general-purpose invocations exist, delegation analysis is skipped silently
- Performance: clustering 50 invocations completes in < 2 seconds

**Dependencies:** E4-S1 through E4-S3, existing analyze command

---

#### E4-S5: Add delegation pattern recognition tests

**Title:** Add delegation pattern recognition tests
**Labels:** `epic:delegation-patterns`, `testing`, `priority:high`

**Summary:** Unit tests for clustering, draft generation, dedup, and integration.

**Acceptance Criteria:**
- Fixture data: 15+ `AgentInvocation` objects with `agent_type="general-purpose"` and varied descriptions/prompts forming 2-3 natural clusters
- Unit tests:
  - Clustering produces expected number of groups for fixture data
  - Clusters below min_cluster_size are filtered
  - Draft generation produces valid YAML with all required fields
  - Model selection logic: read-only -> Haiku, mixed -> Sonnet, heavy -> Opus
  - Dedup suppresses when similarity > threshold
  - Dedup allows when similarity < threshold
  - Edge case: < 5 invocations produces no recommendations
- Integration test: full pipeline on real session data (if sufficient general-purpose invocations exist)

**Dependencies:** E4-S1 through E4-S4

---

## E5: Model-Routing Diagnostics

**Epic issue:** #95 (existing issue serves as the epic -- specced with open questions)
**Label:** `epic:model-routing`
**Dependencies:** E2 (traces for complexity signals), existing analytics infrastructure
**Sizing:** Medium (3-4 days)

### Stories

---

#### E5-S1: Implement task complexity classification

**Title:** Implement per-agent task complexity tier classification
**Labels:** `epic:model-routing`, `enhancement`, `priority:high`

**Summary:** Classify each agent's observed task pattern into simple/moderate/complex based on aggregated metrics.

**Acceptance Criteria:**
- Given per-agent invocation statistics (aggregated from `AgentInvocation` and optionally `SubagentTrace`), `classify_complexity(agent_stats)` returns a tier
- **Simple tier:** predominantly read-only tools (Read, Grep, Glob, WebFetch), < 5 average tool calls, < 2000 average tokens, 0 write tools
- **Complex tier:** write tools (Write, Edit, Bash) present, > 10 average tool calls, OR > 5000 average tokens, OR error rate > 20%
- **Moderate tier:** everything else
- Thresholds are configurable (dict/config, not hardcoded inline)
- When SubagentTrace is available, tool classification uses actual tool names from traces (more accurate)
- When only metadata is available, uses heuristic from tool_uses count + token count
- Module at `agentfluent.diagnostics.model_routing`

**Dependencies:** E2-S5 (linked traces for tool names), existing agent metrics

---

#### E5-S2: Implement model-routing mismatch detection

**Title:** Detect model-routing mismatches (overspec'd and underspec'd)
**Labels:** `epic:model-routing`, `enhancement`, `priority:high`

**Summary:** Compare observed complexity tier against the model actually used; emit signals when they don't match.

**Acceptance Criteria:**
- Model tier mapping: Haiku = simple, Sonnet = moderate, Opus = complex (configurable)
- **Overspec'd:** agent uses Opus/Sonnet but observed tier is simple. Signal with severity: warning (one tier gap), info (marginal).
- **Underspec'd:** agent uses Haiku but observed tier is complex AND error rate is high or retry rate is high. Signal with severity: warning.
- **Correctly spec'd:** no signal emitted.
- Cost-savings estimate included for overspec'd: `(current_cost - alternative_cost) * invocation_count` using current pricing rates
- Handles agents without explicit model (default to Opus for general-purpose)
- Handles agents with < 3 invocations: skip (insufficient data for confident classification)
- New signal type: `MODEL_MISMATCH` added to `SignalType` enum

**Dependencies:** E5-S1 (complexity classification), existing pricing module

---

#### E5-S3: Add model-routing correlation rule and recommendations

**Title:** Add ModelRoutingRule to correlator with cost-savings recommendations
**Labels:** `epic:model-routing`, `enhancement`, `priority:high`

**Summary:** Correlation rule that produces actionable model-change recommendations.

**Acceptance Criteria:**
- `ModelRoutingRule` in correlator consumes MODEL_MISMATCH signals
- Overspec'd recommendation: "Agent 'X' runs on [model] but its task pattern is [tier]. Consider [alternative]. Estimated savings: $Y.YY per [N] invocations at current rates."
- Underspec'd recommendation: "Agent 'X' runs on [model] but shows [high retry rate / high error rate]. If output quality is insufficient, consider [alternative]."
- `target: "model"` on all model-routing recommendations
- When `AgentConfig` is available, reference the config file: "Update `model` in `~/.claude/agents/X.md`"
- Recommendations include the specific metrics that drove the classification in `detail`

**Dependencies:** E5-S2 (mismatch detection), existing correlator

---

#### E5-S4: Add model-routing diagnostics tests

**Title:** Add model-routing diagnostics tests
**Labels:** `epic:model-routing`, `testing`, `priority:high`

**Summary:** Unit tests for complexity classification, mismatch detection, and recommendations.

**Acceptance Criteria:**
- Unit tests:
  - Read-only agent with low tokens -> classified as simple
  - Write-heavy agent with many tool calls -> classified as complex
  - Mixed agent -> classified as moderate
  - Opus agent classified as simple -> overspec'd signal with correct cost estimate
  - Haiku agent classified as complex with high errors -> underspec'd signal
  - Sonnet agent classified as moderate -> no signal
  - Agent with < 3 invocations -> skipped
  - Cost-savings calculation is correct given known pricing + invocation count
- Integration test: run model-routing on real sessions, validate output structure

**Dependencies:** E5-S1 through E5-S3

---

## E6: MCP Config Assessment (STRETCH)

**Label:** `epic:mcp-assessment`, `stretch`
**Dependencies:** E2 (subagent traces for MCP tool names)
**Sizing:** Medium (3-4 days)

### Summary

Audit observed MCP tool usage (`mcp__<server>__*` tool names in JSONL and subagent traces) against configured MCP servers. Surface unused servers, missing servers, and recommendations for MCP config changes.

**CUT CRITERIA:** This epic is stretch scope. If the must-include epics (E1-E5) fill the release timeline, all E6 stories are deferred to v0.4 with no impact on the core release.

### Stories

---

#### E6-S1: Extract MCP tool usage from session and trace data (STRETCH)

**Title:** Extract MCP tool usage from sessions and subagent traces
**Labels:** `epic:mcp-assessment`, `enhancement`, `stretch`, `priority:medium`

**Summary:** Identify all tool calls matching the `mcp__<server>__<tool>` naming convention and aggregate usage per server.

**Acceptance Criteria:**
- Given parsed session messages and SubagentTrace data, extract all tool names matching `mcp__*`
- Parse tool name into server name and tool name components (`mcp__github__create_issue` -> server: `github`, tool: `create_issue`)
- Aggregate per server: total calls, unique tools used, error count, success count
- Handle sessions with no MCP tools (return empty)
- Module at `agentfluent.diagnostics.mcp_assessment` (or `config/mcp.py`)

**Dependencies:** E2 (trace parser for subagent tool names)

---

#### E6-S2: Discover configured MCP servers (STRETCH)

**Title:** Discover configured MCP servers from settings files
**Labels:** `epic:mcp-assessment`, `enhancement`, `stretch`, `priority:medium`

**Summary:** Read Claude Code settings to determine which MCP servers are configured.

**Acceptance Criteria:**
- Scan `~/.claude/settings.json` (user scope) and `.claude/settings.json` (project scope) for MCP server configuration
- Extract server names, their configured tools, and enabled/disabled status
- Handle missing settings files gracefully
- Handle settings files without MCP configuration (return empty)
- Respect `--claude-config-dir` for user-scope settings path

**Dependencies:** E1 (#90 for config dir override)

---

#### E6-S3: Implement MCP config audit and recommendations (STRETCH)

**Title:** Implement MCP config audit with recommendations
**Labels:** `epic:mcp-assessment`, `enhancement`, `stretch`, `priority:medium`

**Summary:** Compare observed MCP usage against configured servers; emit recommendations.

**Acceptance Criteria:**
- **Unused server:** configured server with 0 observed tool calls -> INFO recommendation: "MCP server 'X' is configured but never used in analyzed sessions. Consider removing if unneeded."
- **Missing server:** `mcp__X__*` tool calls that failed + server 'X' not in config -> WARNING recommendation: "Failed MCP tool calls reference server 'X' which is not configured. Add server 'X' to MCP settings."
- **Underused server:** configured server with < 2 tool calls across all sessions -> INFO note
- Recommendations use `target: "mcp"` in the diagnostics output
- `--format json` includes MCP audit results
- Integrated into `agentfluent analyze --diagnostics` when MCP usage is detected

**Dependencies:** E6-S1 (usage extraction), E6-S2 (configured servers)

---

#### E6-S4: Add MCP assessment tests (STRETCH)

**Title:** Add MCP config assessment tests
**Labels:** `epic:mcp-assessment`, `testing`, `stretch`, `priority:medium`

**Summary:** Unit tests for MCP extraction, discovery, and audit logic.

**Acceptance Criteria:**
- Fixture data with MCP tool calls in session/trace data
- Fixture settings.json with configured servers
- Tests: unused server detected, missing server detected, all servers used (no recommendations), no MCP tools (no output)

**Dependencies:** E6-S1 through E6-S3

---

## Implementation Priority Order

### Phase 1: Foundation (can start immediately)
1. E1 / #90 -- Config directory override (independent, 1-2 days)

### Phase 2: Core Data Layer (critical path)
2. E2-S1 -- Subagent trace data models
3. E2-S2 -- Subagent directory discovery
4. E2-S3 -- Subagent trace parser
5. E2-S4 -- Retry sequence detection
6. E2-S5 -- Trace-to-invocation linker
7. E2-S6 -- Trace parser tests

### Phase 3: Deep Diagnostics (depends on Phase 2)
8. E3-S1 -- Trace-level signal types
9. E3-S2 -- Trace-level signal extraction
10. E3-S3 -- Trace-aware correlation rules
11. E3-S4 -- Integrate into analyze pipeline
12. E3-S5 -- Deep diagnostics tests

### Phase 4: Advanced Diagnostics (depends on Phase 2+3, can be parallel)
13. E4-S1 -- Delegation clustering pipeline
14. E4-S2 -- Subagent draft generation
15. E4-S3 -- Existing-agent deduplication
16. E4-S4 -- Integrate delegation into analyze
17. E4-S5 -- Delegation tests
18. E5-S1 -- Task complexity classification
19. E5-S2 -- Model-routing mismatch detection
20. E5-S3 -- Model-routing correlation rule
21. E5-S4 -- Model-routing tests

### Phase 5: Stretch (only if Phase 1-4 complete within timeline)
22. E6-S1 -- MCP tool usage extraction (STRETCH)
23. E6-S2 -- Configured MCP server discovery (STRETCH)
24. E6-S3 -- MCP config audit + recommendations (STRETCH)
25. E6-S4 -- MCP assessment tests (STRETCH)

**Total: 6 epics (1 stretch), 25 stories (4 stretch)**
