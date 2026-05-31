# AgentFluent v0.9 Backlog

Ordered backlog for v0.9 (Count Every Turn). Issues are sequenced by dependency chain, not by issue number.

**Theme:** Count turns. Cut waste. Ship the signals that explain why.

**Milestone:** v0.9.0

---

## Triage Summary

| Disposition | Count | Issues |
|-------------|-------|--------|
| Already filed (open) | 16 | #465, #466, #467, #468, #469, #470, #405, #404, #372, #406, #407, #481, #480, #478, #477, #479 |
| Net-new (to create) | 1 | docs catch-up |
| Stub/tracking only | 1 | #469 (not implementation-ready) |
| Total in milestone | 17 | |

---

## Stream A: Model-Turn Integration -- `epic:analytics`

Six stories surfacing model-turn counts at every analytics level. Dependency-ordered. All six already filed.

### A1. #465 -- Surface parent-session model-turn count in CLI and JSON output

**Priority:** medium
**Labels:** `enhancement`, `epic:analytics`, `priority:medium`
**Sizing:** XS (~0.5 day)
**Dependencies:** None
**Status:** ALREADY FILED

**Summary:** Expose `model_turns: int` on `SessionAnalysis` (aliasing or deriving from existing `assistant_message_count`). Add to Token Usage table, Per-Session Breakdown (verbose), and JSON output. GLOSSARY entry for `model_turns`. The backing data already exists at `pipeline.py:163`.

**Acceptance criteria:**
- `SessionAnalysis.model_turns: int` field exposed
- `AnalysisResult.total_model_turns: int` aggregated across sessions
- Token Usage table shows "Model turns" row
- Per-Session Breakdown (verbose) shows "Turns" column
- JSON includes `model_turns` at session level, `total_model_turns` at aggregate
- GLOSSARY entry: "one merged assistant message (one API round-trip)"
- `agentfluent explain model_turns` resolves
- Unit tests: 5 assistant messages -> 5 turns; 0 messages -> 0; multi-session aggregation; JSON field presence

**Blocks:** #470 (diff needs `model_turns` in envelope)

---

### A2. #466 -- Add model-turn count to SubagentTrace and AgentInvocation

**Priority:** medium
**Labels:** `enhancement`, `epic:analytics`, `priority:medium`
**Sizing:** S (~1 day)
**Dependencies:** None (independent of #465; can be implemented in any order)
**Status:** ALREADY FILED

**Summary:** New `model_turns: int` field on `SubagentTrace` (computed from trace's assistant messages). New `model_turns: int | None` computed field on `AgentInvocation` (returns `trace.model_turns` when trace exists, `None` otherwise). Per-Invocation Detail table gains "Turns" column.

**Acceptance criteria:**
- `SubagentTrace.model_turns: int` populated by counting `type == "assistant"` messages
- `AgentInvocation.model_turns: int | None` via `@computed_field` (Pydantic) or stored field (dataclass)
- Per-Invocation Detail table (verbose) shows "Turns" column; `None` displays as "-"
- JSON includes `model_turns` per invocation (null when no trace)
- Unit tests: 3 assistant messages -> 3; 1 message with 5 tool_use blocks -> 1; 0 messages -> 0; invocation with/without trace

**Blocks:** #467 (rollup consumes `AgentInvocation.model_turns`)

---

### A3. #467 -- Per-agent-type model-turn rollup and efficiency ratios

**Priority:** medium
**Labels:** `enhancement`, `epic:analytics`, `priority:medium`
**Sizing:** S (~1 day)
**Dependencies:** #466
**Status:** ALREADY FILED

**Summary:** Aggregate model-turn counts by agent type in `AgentTypeMetrics` and compute derived efficiency ratios: `avg_turns_per_invocation`, `avg_tool_calls_per_turn`, `avg_tokens_per_turn`, `estimated_avg_cost_per_turn_usd`. All as stored fields (not `@property`) following the existing pattern. `_merge_agent_metrics` carries turn fields through multi-session aggregation.

**Acceptance criteria:**
- `AgentTypeMetrics` gains `total_model_turns: int`, `invocations_with_turns: int`, and four stored ratio fields
- `AgentMetrics.total_model_turns: int` summed across types
- `compute_agent_metrics()` accumulates turns from invocations where `model_turns is not None`
- `_merge_agent_metrics` carries + recomputes turn fields
- Agent Invocations table shows "Avg Turns" column
- JSON envelope includes all new fields per agent type and at aggregate level
- Unit tests: 3 invocations [4, 6, None] -> totals correct, avg correct; all-None -> None ratios; 0-turns -> None ratios; merge test

**Blocks:** #470 (diff needs per-agent-type turn totals), #469 (stub depends on turn infrastructure)

---

### A4. #468 -- Research: investigate trace-missing subagent invocations (~20%)

**Priority:** medium
**Labels:** `epic:analytics`, `priority:medium`, `research`
**Sizing:** XS (~0.5 day)
**Dependencies:** None
**Status:** ALREADY FILED

**Summary:** Investigate why ~20% of subagent invocations (6/31 pm invocations, all from session `08b03d83-...`) have no subagent trace file. Five hypotheses in the issue body. Deliverable is a written finding (comment on the issue), not a code change.

**Acceptance criteria:**
- Per-invocation checks: shared agent_type? `toolUseResult` metadata values? invocation-order pattern?
- Session-level checks: CC version header? `subagents/` directory exists but files missing, or directory absent?
- Cross-corpus checks (if time allows): isolated to this session or broader?
- Comment posted with: findings, supported hypothesis, recommendation (code change vs. `None` is correct)

**Blocks:** Nothing (informs understanding of #466/#467's `None` treatment)

---

### A5. #470 -- Integrate model-turn metrics into `agentfluent diff`

**Priority:** medium
**Labels:** `enhancement`, `epic:analytics`, `priority:medium`
**Sizing:** S-M (~1-1.5 days)
**Dependencies:** #465, #467
**Status:** ALREADY FILED

**Summary:** Add model-turn deltas to diff output. Per-agent-type: `AgentTypeDelta` gains turn fields. Parent-session: turn delta in `TokenMetricsDelta` (or sibling). Backward compatibility with pre-turn envelopes via `int(d.get("total_model_turns", 0) or 0)`. Avg turns derived in renderer, not precomputed.

**Acceptance criteria:**
- `AgentTypeDelta` gains `baseline_total_model_turns`, `current_total_model_turns`, `total_model_turns_delta`, `baseline_invocations_with_turns`, `current_invocations_with_turns`
- Parent-session model turns delta surfaced
- `_diff_agent_metrics` extracts new fields with legacy fallback
- Per-Agent diff table shows "Turns" or "Avg Turns" delta column
- Token Metrics section shows parent-session turn delta
- JSON diff envelope includes all new fields
- Unit tests: different turn counts -> correct deltas; pre-turn baseline -> 0 fallback; both zero -> 0 delta; renderer avg derivation

**Blocks:** Nothing

---

### A6. #469 -- Per-turn diagnostic ratios (stub -- requires dogfood validation)

**Priority:** low
**Labels:** `enhancement`, `epic:analytics`, `priority:low`
**Sizing:** TBD (depends on dogfood)
**Dependencies:** #467
**Status:** ALREADY FILED -- TRACKING ITEM ONLY

**Summary:** Scope placeholder for per-turn diagnostic ratios (`tool_errors_per_turn`, `retries_per_turn`, `cost_per_turn`). Explicitly NOT implementation-ready. Requires dogfood data after #465-#467 ship to determine thresholds and validate which ratios are diagnostically useful.

**v0.9 disposition:** Assess at dogfood time. If data supports implementation, ship in v0.9. If not, defer to v0.10 with findings documented on the issue.

**Blocks:** Nothing

---

## Stream B: Advanced Tool Use Diagnostics -- Epic #403

Four stories for three new diagnostic signals. All already filed with full specs. Detailed spec at `.claude/specs/prd-advanced-tool-use-diagnostics.md`.

### B1. #405 -- `PARAMETER_RETRY` signal with paste-ready `input_examples` extraction

**Priority:** high
**Labels:** `enhancement`, `epic:diagnostics`, `priority:high`
**Sizing:** M (2-3 days)
**Dependencies:** None
**Status:** ALREADY FILED

**Summary:** New diagnostic signal detecting parameter-retry patterns (same tool called 2+ times with different input shapes, first call errored). Tier A: trace-required, consecutive same-tool detection with input-shape comparison. Tier B: metadata fallback using `toolStats` 3+ calls. Paste-ready `input_examples` extraction from successful calls. SPEED axis.

**Acceptance criteria:**
- `SignalType.PARAMETER_RETRY` enum value
- Tier A: 2+ consecutive calls to same tool, first `is_error` or error text, input keys differ -> WARNING signal
- Tier B: `toolStats` 3+ calls with no trace -> INFO signal
- Paste-ready example: successful call's `input` dict formatted as JSON in recommendation
- Correlator rule with observation, reason, action referencing Anthropic 72% -> 90% accuracy benchmark
- GLOSSARY entry, `explain` command
- Aggregation per agent type
- Unit tests for all TP/FP boundaries, paste-ready extraction, correlator

**Blocks:** Nothing

---

### B2. #404 + #372 -- `TOOL_INVENTORY_OVERSIZED` (graduated from parked #371)

**Priority:** high
**Labels:** `enhancement`, `epic:diagnostics`, `priority:high`
**Sizing:** XS (#404 update) + M (#372 implementation) = M total (~2-3 days)
**Dependencies:** None
**Status:** ALREADY FILED

**Summary:** Two-part delivery. #404 updates #372's citations, priority, and recommendation text to reference the Advanced Tool Use article's `defer_loading` platform-level API feature and Opus 4.5 benchmarks (79.5% -> 88.1% accuracy). #372 implements the `TOOL_INVENTORY_OVERSIZED` signal: detect oversized tool inventories with low utilization (30+ tools, <0.5 utilization ratio), recommend `defer_loading: true`. COST axis.

**Acceptance criteria:**
- #372 recommendation text references article benchmarks and `defer_loading: true` platform syntax
- Signal fires on invocations with 30+ available tools and utilization ratio < 0.5
- GLOSSARY entry, `explain` command
- Unit tests per #372's existing ACs

**Blocks:** Nothing

---

### B3. #406 -- `TOOL_ORCHESTRATION_CHAIN` signal (Tier A metadata-only)

**Priority:** high
**Labels:** `enhancement`, `epic:diagnostics`, `priority:high`
**Sizing:** M (2-3 days)
**Dependencies:** None
**Status:** ALREADY FILED

**Summary:** New diagnostic signal detecting tool-orchestration chains: high sequential tool-call counts (>= 10) with large intermediate payloads (>= 2000 tokens/call average) across 3+ invocations of the same agent type. Recommends Programmatic Tool Calling (`allowed_callers`). INFO severity (precision uncertainty). COST axis. First LLM-call augmentation candidate (D035).

**Acceptance criteria:**
- `SignalType.TOOL_ORCHESTRATION_CHAIN` enum value
- Fires when `totalToolUseCount >= 10` AND `totalTokens / totalToolUseCount > 2000` AND 3+ invocations show pattern
- Correlator rule cites 37% token reduction benchmark
- Estimated savings annotation: `totalTokens * 0.37`
- GLOSSARY entry, `explain` command
- Unit tests per #406's ACs (above/below thresholds, min-invocation gate)

**Blocks:** #407 (calibration)

---

### B4. #407 -- `TOOL_ORCHESTRATION_CHAIN` calibration check

**Priority:** medium
**Labels:** `enhancement`, `epic:diagnostics`, `priority:medium`
**Sizing:** S (1-2 days)
**Dependencies:** #406
**Status:** ALREADY FILED

**Summary:** Ground-truth precision check. Sample >= 15 detections from dogfood corpus. Classify TP/FP. If precision >= 70%, ship. If < 70%, one round of threshold tuning. Document calibration results in code comments. Feeds D035 candidate #1 baseline.

**Acceptance criteria:**
- >= 15 detections sampled and classified
- Precision calculated and documented
- If < 70%: threshold adjustment + re-check
- Calibration results in code comments in signal implementation file

**Blocks:** Nothing (but gates release -- must complete before v0.9 ships)

---

## Stream C: Dogfood Fixes -- various epics

Five stories addressing concrete issues from the v0.8 dogfood analysis. All independent, all already filed.

### C1. #481 -- Detect/warn `cleanupPeriodDays` at default (30)

**Priority:** high
**Labels:** `enhancement`, `priority:high`
**Sizing:** S-M (1-2 days)
**Dependencies:** None
**Status:** ALREADY FILED

**Summary:** Read `~/.claude/settings.json` (and project `.claude/settings.json`) at session-discovery time. Warn when `cleanupPeriodDays <= 30` or missing. Suggest setting to 3650. Show in CLI warning and JSON envelope. No warning when >= 365.

**Acceptance criteria:**
- Reads both global and project settings.json
- Warning fires when value <= 30 or missing (quotes actual file path)
- Silent when >= 365
- Tests: missing key, default value, long value
- README "Setup" section mentions as first-run recommendation

**Blocks:** Nothing

---

### C2. #480 -- Surface `active_duration` alongside wall-clock in agent summary table

**Priority:** medium
**Labels:** `enhancement`, `priority:medium`
**Sizing:** S (1-2 days)
**Dependencies:** None
**Status:** ALREADY FILED

**Summary:** Per-agent summary shows both "Avg wall-clock/call" and "Avg active/tool_use" columns. The data exists in the JSON envelope (`trace.active_duration_ms`); only the rendering surface needs updating. Optional: highlight rows where wall-clock/active ratio > 3x.

**Acceptance criteria:**
- Both columns visible in agent summary table
- JSON envelope unchanged (no schema breaks)
- Glossary entry or link explaining the distinction
- Interactive-pattern agents (pm, etc.) clearly show divergence

**Blocks:** Nothing

---

### C3. #478 -- Document "Tier 3 can be healthily silent"

**Priority:** medium
**Labels:** `documentation`, `priority:medium`
**Sizing:** XS (<0.5 day)
**Dependencies:** None
**Status:** ALREADY FILED

**Summary:** Per-signal doc note in `docs/SIGNALS.md` explaining operator patterns that produce zero Tier 3 signals as a healthy outcome. Reference `tier3_degraded` as the "did it run" flag. Link from README.

**Acceptance criteria:**
- Per-signal healthy-silence patterns documented
- `tier3_degraded` distinguished from "signal didn't fire"
- README Tier 3 mention links to the note

**Blocks:** Nothing

---

### C4. #477 -- Remove tester subagent

**Priority:** medium
**Labels:** `chore`, `priority:medium`
**Sizing:** XS (<0.5 day)
**Dependencies:** None
**Status:** ALREADY FILED

**Summary:** Delete `.claude/agents/tester.md`. Remove references in CLAUDE.md/README. Two-strikes decision from v0.7 analysis + v0.8 confirmation (0 invocations after scope broadening in #397).

**Acceptance criteria:**
- `.claude/agents/tester.md` deleted
- No `tester` references in CLAUDE.md or README
- `chore(agents):` commit convention

**Blocks:** Nothing

---

### C5. #479 -- Architect prompt tightening

**Priority:** medium
**Labels:** `chore`, `priority:medium`
**Sizing:** XS (<0.5 day)
**Dependencies:** None
**Status:** ALREADY FILED

**Summary:** Two prompt additions to `.claude/agents/architect.md`: (1) consolidate Read line ranges in first call, (2) confirm issue number before retrying `mcp__github__get_issue`. Addresses 30 + 3 retries respectively from v0.8 dogfood.

**Acceptance criteria:**
- Both instructions added to architect.md
- `chore(agents):` commit convention
- PR body notes session restart requirement per `[[feedback-subagent-session-cache]]`

**Blocks:** Nothing

---

## Stream D: Docs

### D1. NEW -- docs: catch up README + GLOSSARY + CHANGELOG for v0.9.0

**Priority:** required-for-release
**Labels:** `documentation`, `enhancement`, `priority:medium`
**Sizing:** M (2-3 days)
**Dependencies:** All feature work complete
**Status:** TO BE CREATED

**Summary:** Update README (model-turn metrics, Advanced Tool Use signals, cleanupPeriodDays, active_duration column), GLOSSARY (new terms: `model_turns`, `parameter_retry`, `tool_orchestration_chain`, `tool_inventory_oversized`, `avg_turns_per_invocation`, `avg_tool_calls_per_turn`, `avg_tokens_per_turn`, `estimated_avg_cost_per_turn_usd`, `cleanupPeriodDays`), CHANGELOG (prose expansion for v0.9.0 features).

**Blocks:** Nothing

---

## Implementation Priority Order

### Wave 1 -- Model-turn foundation + dogfood quick wins (start immediately, parallel)

All independent. Start in parallel or interleave.

1. **#465** -- parent-session model turns (XS, no deps, quick win)
2. **#466** -- subagent-invocation model turns (S, no deps, blocks #467)
3. **#468** -- trace-missing research (XS, no deps, investigation only)
4. **#481** -- cleanupPeriodDays warning (S-M, no deps, high priority)
5. **#477** -- remove tester (XS, no deps, chore)
6. **#479** -- architect prompt (XS, no deps, chore)

### Wave 2 -- Rollup + Advanced Tool Use signals (days 4-10)

7. **#467** -- per-agent-type turn rollup + ratios (S, depends on #466)
8. **#405** -- PARAMETER_RETRY signal (M, independent)
9. **#404** + **#372** -- TOOL_INVENTORY_OVERSIZED (M, independent)
10. **#480** -- active_duration in summary table (S, independent)
11. **#478** -- Tier 3 silence docs (XS, independent)

### Wave 3 -- Diff integration + orchestration chain (days 8-14)

12. **#470** -- model turns in diff (S-M, depends on #465 + #467)
13. **#406** -- TOOL_ORCHESTRATION_CHAIN signal (M, independent)

### Wave 4 -- Calibration + assessment (days 12-18)

14. **#407** -- orchestration chain calibration (S, depends on #406, gates release)
15. **#469** -- per-turn diagnostic ratios assessment (TBD, depends on #467 dogfood data)

### Wave 5 -- Release prep (days 18-24)

16. **NEW** -- docs catch-up (M, depends on all features)
17. Dogfood validation run (model turns visible, Advanced Tool Use signals fire)
18. Release prep (changelog, version bump, CI green)

---

## Ordered Backlog (flat view)

| Order | # | Title | In/Out | Priority | Deps | Stream |
|-------|---|-------|--------|----------|------|--------|
| 1 | #465 | Parent-session model turns | IN | medium | none | A |
| 2 | #466 | Subagent-invocation model turns | IN | medium | none | A |
| 3 | #468 | Trace-missing research | IN | medium | none | A |
| 4 | #481 | cleanupPeriodDays warning | IN | high | none | C |
| 5 | #477 | Remove tester agent | IN | medium | none | C |
| 6 | #479 | Architect prompt tightening | IN | medium | none | C |
| 7 | #467 | Per-agent-type turn rollup + ratios | IN | medium | #466 | A |
| 8 | #405 | PARAMETER_RETRY signal | IN | high | none | B |
| 9 | #404 + #372 | TOOL_INVENTORY_OVERSIZED | IN | high | none | B |
| 10 | #480 | active_duration in summary table | IN | medium | none | C |
| 11 | #478 | Tier 3 silence docs | IN | medium | none | C |
| 12 | #470 | Model turns in diff | IN | medium | #465, #467 | A |
| 13 | #406 | TOOL_ORCHESTRATION_CHAIN signal | IN | high | none | B |
| 14 | #407 | Orchestration chain calibration | IN | medium | #406 | B |
| 15 | #469 | Per-turn diagnostic ratios (stub) | TRACKING | low | #467 | A |
| 16 | NEW | Docs catch-up | IN | required | all features | D |

---

## Proposed Issues for Review

### Already filed (no action needed -- assign to v0.9.0 milestone)

| # | Title | Labels | Epic |
|---|-------|--------|------|
| #465 | analytics: surface parent-session model-turn count in CLI and JSON output | `enhancement`, `epic:analytics`, `priority:medium` | `epic:analytics` |
| #466 | analytics: add model-turn count to SubagentTrace and AgentInvocation | `enhancement`, `epic:analytics`, `priority:medium` | `epic:analytics` |
| #467 | analytics: per-agent-type model-turn rollup and efficiency ratios | `enhancement`, `epic:analytics`, `priority:medium` | `epic:analytics` |
| #468 | research: investigate trace-missing subagent invocations (~20%) | `epic:analytics`, `priority:medium`, `research` | `epic:analytics` |
| #469 | analytics: per-turn diagnostic ratios (stub) | `enhancement`, `epic:analytics`, `priority:low` | `epic:analytics` |
| #470 | analytics: integrate model-turn metrics into diff | `enhancement`, `epic:analytics`, `priority:medium` | `epic:analytics` |
| #405 | diagnostics: PARAMETER_RETRY signal | `enhancement`, `epic:diagnostics`, `priority:high` | `epic:diagnostics` (via #403) |
| #404 | Graduate #372: refresh citations | `enhancement`, `epic:diagnostics`, `priority:high` | `epic:diagnostics` (via #403) |
| #372 | TOOL_INVENTORY_OVERSIZED signal | (update labels per #404) | `epic:diagnostics` (via #403) |
| #406 | diagnostics: TOOL_ORCHESTRATION_CHAIN signal | `enhancement`, `epic:diagnostics`, `priority:high` | `epic:diagnostics` (via #403) |
| #407 | diagnostics: TOOL_ORCHESTRATION_CHAIN calibration | `enhancement`, `epic:diagnostics`, `priority:medium` | `epic:diagnostics` (via #403) |
| #481 | cli/config: detect/warn cleanupPeriodDays | `enhancement`, `priority:high` | (standalone) |
| #480 | cli: surface active_duration in agent summary | `enhancement`, `priority:medium` | (standalone) |
| #478 | docs: Tier 3 healthy silence | `documentation`, `priority:medium` | (standalone) |
| #477 | chore: remove tester agent | `chore`, `priority:medium` | (standalone) |
| #479 | chore: architect prompt tightening | `chore`, `priority:medium` | (standalone) |

### Net-new issues to create

| Title | Labels | Epic | Body summary |
|-------|--------|------|-------------|
| docs: catch up README + GLOSSARY + CHANGELOG for v0.9.0 | `documentation`, `enhancement`, `priority:medium` | (standalone) | Update README (model-turn metrics, ATU signals, cleanupPeriodDays), GLOSSARY (new terms), CHANGELOG. Depends on all features. |

---

## Estimated Total

**17 issues (16 already filed + 1 net-new), ~17-26 dev days (3-4 weeks)**

Streams A, B, and C are fully independent. Within Stream A, the dependency chain is `#466 -> #467 -> #470` and `#465 -> #470`. Within Stream B, `#406 -> #407` is the only chain. A solo developer can start with Wave 1 (interleaving #465, #466, #468, #481 with the chore quick-wins), then shift to Wave 2 (rollup + ATU signals) once the foundation lands.
