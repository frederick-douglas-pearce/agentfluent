# AgentFluent v0.5 Backlog

Ordered backlog for v0.5 (Trustworthy Diagnostics). Issues are sequenced by dependency chain, not by issue number.

**Theme:** Fix the inputs, fix the math, then build on the foundation.

**Milestone:** v0.5.0

---

## Triage Summary

| Disposition | Count | Issues |
|-------------|-------|--------|
| In scope | 9 | #230, #186, #187, #189, #172, #199, #184, #185, #215 |
| Stretch | 2 | #227, #205 |
| Deferred to v0.6 | 5 | #198, #201, #203, #204, #208 |

---

## Phase 1: Data Fidelity (critical path -- blocks everything downstream)

These issues fix the measurement artifacts the dogfood run exposed. Nothing else in the release produces trustworthy output until these land.

### 1. #230 -- Detect and exclude user-input wait time from duration metrics

**Priority:** critical-path
**Labels:** `enhancement`, `priority:medium` (should be upgraded to `priority:high`)
**Sizing:** M (3-4 days)
**Dependencies:** None
**Status:** IN SCOPE

**Summary:** Split `total_duration_ms` into `active_duration_ms` + `wait_duration_ms`. Use `active_duration_ms` for `duration_outlier` detection.

**Key implementation decisions:**
- AskUserQuestion-anchored detection preferred (high confidence)
- Idle-gap heuristic (gap > 60s between non-interactive tool calls) as fallback
- Both fields exposed in JSON output; `active_duration_ms` used for all downstream metrics
- Validate against dogfood data: `pm` 999s/call should drop to ~minutes

**Blocks:** #189 (parent-thread offload needs trustworthy duration for savings estimates)

---

### 2. #186 -- Recalibrate outlier detection against real distributions

**Priority:** critical-path
**Labels:** `enhancement`, `priority:medium` (should be upgraded to `priority:high`)
**Sizing:** M (3-5 days: Phase 1 notebook ~2 days, Phase 2 code change ~1-2 days)
**Dependencies:** None (independent of #230 but both must land before #189)
**Status:** IN SCOPE

**Summary:** Two-phase: (1) extend calibration notebook with distribution analysis for `tokens_per_tool_use` and `duration_per_tool_use`, (2) migrate `_extract_token_outliers` / `_extract_duration_outliers` to distribution-appropriate method (likely IQR or P95).

**Key outputs:**
- Per-agent-type distribution stats (mean, median, std, Q1, Q3, P90, P95, P99)
- Comparison of outlier methods (z-score, IQR, percentile) against current mean-ratio
- Updated `detail` dict on signals: `p95_value`, `std_value`, `iqr` alongside existing `mean_value`
- "Before/after" signal count comparison

**Blocks:** #187 (consumes new distribution stats), #189 (trustworthy token metrics)

---

### 3. #187 -- Surface distribution context in verbose output

**Priority:** high
**Labels:** `enhancement`, `priority:low` (should be upgraded to `priority:medium`)
**Sizing:** XS-S (1 day)
**Dependencies:** #186 (needs distribution stats in `detail` dict)
**Status:** IN SCOPE

**Summary:** Under `--verbose`, augment outlier signal messages with z-score, P95, and distribution shape context. Non-verbose output unchanged.

**Example:**
```
Verbose:  Agent 'Explore' has 40,288 tokens/tool_use (z=3.2, above P95=28,400) --
          8.0x the 5,064 mean, distribution right-skewed (n=89).
```

---

## Phase 2: Headline Feature (depends on Phase 1)

### 4. #189 -- Parent-thread offload candidates

**Priority:** high
**Labels:** `enhancement`, `priority:high`
**Sizing:** L (5-7 days, multi-PR)
**Dependencies:** #230 + #186 (trustworthy metrics)
**Status:** IN SCOPE

**Summary:** New diagnostics layer analyzing parent-thread tool-use sequences. Extracts repeating patterns, clusters by similarity (reuse delegation.py TF-IDF + KMeans infra), estimates parent-thread token cost per cluster, recommends delegation targets (subagent YAML draft or skill scaffold).

**Implementation approach (likely epic decomposition):**
1. Parent-thread tool-sequence extraction (sliding window)
2. Sequence clustering (reuse delegation.py vectorizer + KMeans)
3. Cost estimation per cluster (parent-thread tokens x model pricing)
4. Recommendation surface (subagent draft or skill scaffold)
5. CLI integration ("Offload Candidates" section in `analyze --diagnostics`)
6. Tests + calibration against real data

**Critical guard:** If this runs long (>7 days), defer to v0.6 and release with the data-fidelity + comparison story. The 8 other in-scope items tell a complete "trustworthy diagnostics" story without #189.

---

## Phase 3: Output Improvements (benefits from Phases 1-2 but not strictly blocked)

### 5. #172 -- Priority ranking + top-N summary on recommendations

**Priority:** high
**Labels:** `enhancement`, `priority:medium`
**Sizing:** S-M (2-3 days)
**Dependencies:** None strict, but benefits from #186 (distribution-grounded signals produce better priority scores)
**Status:** IN SCOPE

**Summary:** Composite priority score per recommendation (severity x occurrence count x cost impact x evidence strength). Top-N summary block above the full recommendations table. `--top-n` CLI flag (default 5). `priority_score` field in JSON output.

**Note:** Should land before #199 so `diff` can compare recommendations by priority.

---

### 6. #199 -- `agentfluent diff` -- compare two analyze runs

**Priority:** high
**Labels:** `enhancement`, `priority:medium`, `epic:v04-review-polish`
**Sizing:** M-L (3-5 days)
**Dependencies:** #172 (priority ranking enables meaningful "new/resolved" comparisons)
**Status:** IN SCOPE

**Summary:** New subcommand. `agentfluent diff baseline.json current.json` compares two `analyze --json` outputs.

**PM design decisions (resolved in PRD):**
- **Storage:** User manages baselines explicitly. No internal caching.
- **Scope:** Both diagnostics AND token_metrics.
- **CI exit codes:** Exit 1 if regressions detected (new critical/warning signals). `--fail-on {critical|warning|info}` flag.

**Output surfaces:**
- New, resolved, and persisting recommendations (keyed by `(agent_type, target, signal_types)`)
- Count deltas per signal type
- Cost delta and per-agent token deltas
- `--format json` for CI consumption
- Future: `--format markdown` for PR comments (v0.6 companion to #198)

---

## Phase 4: Delegation Quality (independent of Phases 1-3)

### 7. #185 -- Delegation drafts: unified model classifier

**Priority:** medium
**Labels:** `enhancement`, `priority:medium`
**Sizing:** S (1-2 days)
**Dependencies:** None
**Status:** IN SCOPE

**Summary:** Extract the complexity classifier from `model_routing.py` into a shared helper. Call it from `delegation._classify_model`. One source of truth for "what model fits this workload."

**Blocks:** #184 (the model classifier informs tool-list filtering decisions)

---

### 8. #184 -- Delegation drafts: minimal tools list

**Priority:** medium
**Labels:** `enhancement`, `priority:medium`
**Sizing:** S (1-2 days)
**Dependencies:** #185 (unified classifier helps inform whether a cluster is read-only)
**Status:** IN SCOPE

**Summary:** Replace union-of-observed-tools with frequency-filtered list. Tools used in <50% of cluster members excluded from draft. `tools_observed` retained as debug field in JSON. Fixes over-broad tool recommendations (e.g., code-review agent getting Write/Edit/Bash).

---

### 9. #215 -- Explain why delegation_suggestions is empty

**Priority:** low
**Labels:** `enhancement`, `priority:low`
**Sizing:** XS (<1 day)
**Dependencies:** None
**Status:** IN SCOPE

**Summary:** Add `delegation_suggestions_skipped_reason` field to JSON output when `delegation_suggestions` is empty. Values: `sklearn_not_installed`, `insufficient_invocations`, `no_clusters_above_min_size`.

---

## Stretch Scope

Pull these in if the must-include scope completes ahead of schedule.

### S1. #227 -- Extend Cost by Model to include subagent tokens

**Priority:** medium
**Labels:** `enhancement`, `priority:medium`
**Sizing:** S-M (2-3 days)
**Dependencies:** None (infrastructure exists in `traces/`)
**Status:** STRETCH

**Summary:** Wire existing subagent trace token data into per-model cost breakdown. Add origin attribution (parent vs subagent). Presentation task, not parsing task.

---

### S2. #205 -- `--severity` / `--min-severity` filter

**Priority:** low
**Labels:** `enhancement`, `priority:low`, `epic:v04-review-polish`
**Sizing:** XS (< 1 day)
**Dependencies:** None
**Status:** STRETCH

**Summary:** `--min-severity {info|warning|critical}` flag on `analyze`. Natural companion to #172 (priority ranking).

---

## Deferred to v0.6

| # | Title | Priority | Rationale |
|---|-------|----------|-----------|
| #198 | Markdown report export | medium | Output format. Design should follow `diff` (#199), not precede it. |
| #201 | Per-session diagnostics scope | medium | M effort, deep pipeline touch. Better after #172 and #199 settle. |
| #203 | Inline trace excerpts on critical findings | low | UX polish. Not blocking trust or comparison workflow. |
| #204 | `list` table: cost column + JSON | low | Requires wiring cost into list path. Not critical path. |
| #208 | README note on `[clustering]` extra | low | Docs-only (README part shipped in PR #210). JSON part covered by #215. |

---

## Filed During Sign-Off

### #231 — Hook-induced `permission_failure` noise filtering

**Rationale:** Dogfood analysis (2026-04-29) flagged that most `permission_failure` signals come from the secret-blocking hook (`block_secret_reads.py`), which is intended behavior. These false signals undermine the "trustworthy diagnostics" theme.

**Scope:** Recognize `permission_failure` signals originating from known hook patterns and either downgrade severity to info or annotate with `source: "hook"`.

**Sizing:** S (1-2 days)

**Status:** Filed 2026-04-30 in v0.5.0 milestone with `priority:medium`. Slotted into Wave 1 alongside #230 and #186 as data-fidelity work.

---

## Implementation Priority Order

### Wave 1 -- Data fidelity (days 1-8)
1. **#230** -- Wait-time exclusion (critical path, no deps)
2. **#186** -- Outlier recalibration (critical path, no deps, parallel with #230)
3. **#231** -- Hook-induced `permission_failure` noise filtering (S, parallel with #230/#186)
4. **#187** -- Distribution context in verbose output (depends on #186)

### Wave 2 -- Headline feature (days 6-14, overlaps with Wave 1 tail)
5. **#189** -- Parent-thread offload candidates (depends on #230 + #186)

### Wave 3 -- Output improvements (days 10-16, overlaps with Wave 2)
6. **#172** -- Priority ranking + top-N summary
7. **#199** -- `agentfluent diff` (depends on #172)

### Wave 4 -- Delegation quality (days 12-15, parallel with Wave 3)
8. **#185** -- Unified model classifier
9. **#184** -- Minimal tools list (depends on #185)
10. **#215** -- Empty delegation explanation

### Wave 5 -- Stretch (if time allows)
11. **#205** -- `--severity` filter
12. **#227** -- Subagent tokens in Cost by Model

### Validation
13. Second dogfood run (`agentfluent analyze --project agentfluent`)
14. Release prep (changelog, version bump, CI green)

**Estimated total: 10 must-include issues, ~15-20 dev days**

---

## Ordered Backlog (flat view)

| Order | # | Title | In/Out | Priority | Deps | Phase |
|-------|---|-------|--------|----------|------|-------|
| 1 | #230 | Detect/exclude user-input wait time | IN | critical-path | none | 1 |
| 2 | #186 | Recalibrate outlier detection | IN | critical-path | none | 1 |
| 3 | #231 | Hook-induced `permission_failure` noise | IN | medium | none | 1 |
| 4 | #187 | Distribution context in verbose output | IN | high | #186 | 1 |
| 5 | #189 | Parent-thread offload candidates | IN | high | #230, #186 | 2 |
| 6 | #172 | Priority ranking + top-N summary | IN | high | none* | 3 |
| 7 | #199 | `agentfluent diff` | IN | high | #172 | 3 |
| 8 | #185 | Unified model classifier | IN | medium | none | 4 |
| 9 | #184 | Minimal tools list | IN | medium | #185 | 4 |
| 10 | #215 | Empty delegation explanation | IN | low | none | 4 |
| 11 | #205 | `--severity` filter | STRETCH | low | none | 5 |
| 12 | #227 | Subagent tokens in Cost by Model | STRETCH | medium | none | 5 |
| -- | #198 | Markdown report export | DEFERRED | medium | #199 | v0.6 |
| -- | #201 | Per-session diagnostics scope | DEFERRED | medium | #172, #199 | v0.6 |
| -- | #203 | Inline trace excerpts | DEFERRED | low | -- | v0.6 |
| -- | #204 | `list` cost column + JSON | DEFERRED | low | -- | v0.6 |
| -- | #208 | README clustering note | DEFERRED | low | -- | v0.6 |

\* #172 benefits from #186 (better signals = better priority scores) but is not strictly blocked.
