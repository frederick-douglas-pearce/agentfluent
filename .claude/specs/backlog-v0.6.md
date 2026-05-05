# AgentFluent v0.6 Backlog

Ordered backlog for v0.6 (Quality Axis + Temporal Scoping). Issues are sequenced by dependency chain, not by issue number.

**Theme:** Add the quality dimension, enable temporal comparison, close the dogfooding loop.

**Milestone:** v0.6.0

---

## Triage Summary

| Disposition | Count | Issues |
|-------------|-------|--------|
| In scope | 17 | #268, #269, #270, #271, #272, #273, #292, #293, #294, #295, #296, #297, #298, #299, #285, #287 |
| Stretch | 5 | #274, #275, #281, #265, #264 |
| Deferred to v0.7 | 10 | #198, #201, #203, #204, #263, #262, #170, #171, #183, #275 (if not stretch) |
| Already closed in milestone | 1 | #235 |

---

## Stream A: Quality Axis (Tier 1) -- Epic #268

These issues add the third diagnostics dimension. The stream has a strict dependency chain: #269 must land first, then #270/#271/#272 can parallelize, then #273 depends on #272.

### A1. #269 -- Extract user mid-flight corrections + create quality_signals module skeleton

**Priority:** critical-path (prerequisite)
**Labels:** `enhancement`, `priority:high`, `epic:quality-axis`
**Sizing:** S-M (2-3 days)
**Dependencies:** None
**Status:** IN SCOPE

**Summary:** Create `diagnostics/quality_signals.py`, add `Axis(StrEnum)` and new `SignalType` values to models, add `SIGNAL_AXIS_MAP` to `aggregation.py`, wire into `pipeline.run_diagnostics()`, implement `USER_CORRECTION` detection.

**Key deliverables:**
- Module skeleton (`quality_signals.py`, `Axis` enum, signal types, pipeline wiring)
- Pattern-matching detection for user corrections (negation, interruption, redirection, undo)
- False-positive mitigation (corrections following questions excluded)
- Unit tests with 3 fixture scenarios

**Blocks:** #270, #271, #272

---

### A2. #270 -- Detect file rework density as a quality signal

**Priority:** high
**Labels:** `enhancement`, `priority:high`, `epic:quality-axis`
**Sizing:** S (1-2 days)
**Dependencies:** #269 (module skeleton, `SignalType.FILE_REWORK`)
**Status:** IN SCOPE

**Summary:** Count per-file edits from `Edit`/`Write`/`MultiEdit` tool_use blocks. Fire `FILE_REWORK` signal when threshold exceeded (default: 4 edits/file/session). Bonus: detect post-completion rework.

**Blocks:** Nothing (can parallelize with #271 and #272 after #269)

---

### A3. #271 -- Measure reviewer-caught rate for existing review subagents

**Priority:** high
**Labels:** `enhancement`, `priority:high`, `epic:quality-axis`
**Sizing:** M (2-3 days)
**Dependencies:** #269 (module skeleton, `REVIEW_AGENT_TYPES` constant)
**Status:** IN SCOPE

**Summary:** When review subagents (architect, security-review, tester, code-reviewer) run, measure whether they produced substantive findings and whether the parent acted on them. Emit `REVIEWER_CAUGHT` signal. This directly validates the quality-axis recommendation: review subagents demonstrably catch issues.

**Key considerations:**
- Must fire for both built-in and custom review agents (architect review concern #4)
- Substantive findings detected via length, presence of "blocker"/"issue"/"concern"/"must" language
- Parent action detected by checking for subsequent edits after the review result

**Blocks:** Nothing (parallel with #270 and #272)

---

### A4. #272 -- Multi-axis scoring in aggregation layer + axis attribution

**Priority:** high
**Labels:** `enhancement`, `priority:high`, `epic:quality-axis`
**Sizing:** M (2-3 days)
**Dependencies:** #269 (`SIGNAL_AXIS_MAP`, `Axis` enum, new signal types)
**Status:** IN SCOPE

**Summary:** Extend `_compute_priority_score` with `quality_evidence_factor * W_QUALITY` term (D021). Compute `axis_scores` and `primary_axis` as post-hoc annotations on `AggregatedRecommendation`. Backward compatible: existing non-quality recommendations score identically.

**Key constraint:** First post-upgrade `diff` between pre-quality and post-quality baselines must show zero `priority_score_delta` for persisting non-quality recommendations.

**Blocks:** #273

---

### A5. #273 -- CLI and JSON output: axis labels on recommendations

**Priority:** high
**Labels:** `enhancement`, `priority:high`, `epic:quality-axis`
**Sizing:** S-M (1-2 days)
**Dependencies:** #272 (`axis_scores`, `primary_axis` on model)
**Status:** IN SCOPE

**Summary:** Display `[cost]`, `[speed]`, or `[quality]` prefix on each recommendation in CLI output. Expose `axis_scores` and `primary_axis` in JSON. Follow D020 recommendation copy: concise with axis label.

**Blocks:** Nothing (stretch #274 and #275 follow, but aren't gated)

---

## Stream B: Date-Range Filtering -- Epic #293

Fully parallel with Stream A. No cross-dependencies. Internal chain: #294 -> #295 -> #296/#297 -> #298 -> #299.

### B1. #294 -- Extract first-message timestamp during session discovery

**Priority:** critical-path (prerequisite)
**Labels:** `enhancement`, `epic:date-range-filtering`
**Sizing:** S (1-2 days)
**Dependencies:** None
**Status:** IN SCOPE

**Summary:** Extend `SessionInfo` with `first_message_timestamp: datetime | None`. Populate during `discover_sessions()` by reading the first analytical message's timestamp. Performance bounded: typically lines 1-5 of each file.

**Blocks:** #295, #296, #297

---

### B2. #295 -- Datetime parsing utility for --since/--until input

**Priority:** high
**Labels:** `enhancement`, `epic:date-range-filtering`
**Sizing:** S (1-2 days)
**Dependencies:** None (can start parallel with #294)
**Status:** IN SCOPE

**Summary:** Create `core/timeutil.py` (or similar). Parse ISO 8601 with/without timezone, date-only, relative (`7d`, `12h`, `30m`). Return timezone-aware UTC `datetime`. Clear error on unparseable input.

**Blocks:** #296, #297

---

### B3. #296 -- Add --since/--until time filtering to `list --project`

**Priority:** medium
**Labels:** `enhancement`, `epic:date-range-filtering`
**Sizing:** S (1 day)
**Dependencies:** #294, #295
**Status:** IN SCOPE

**Summary:** Add flags to `list` command. Filter sessions by `first_message_timestamp`. Allows users to preview which sessions a time window covers before running expensive analysis.

---

### B4. #297 -- Add --since/--until time filtering to `analyze`

**Priority:** high
**Labels:** `enhancement`, `epic:date-range-filtering`
**Sizing:** S-M (2-3 days)
**Dependencies:** #294, #295
**Status:** IN SCOPE

**Summary:** Core feature. Add flags, wire session filtering, handle flag interactions (`--since` + `--latest N`, `--since` + `--session` error, `--since` + `--agent` orthogonal). Per D024/D025: session-level filtering, whole-session semantics.

**Blocks:** #298

---

### B5. #298 -- Add `window` metadata field to analyze --json output

**Priority:** medium
**Labels:** `enhancement`, `epic:date-range-filtering`
**Sizing:** XS (<1 day)
**Dependencies:** #297
**Status:** IN SCOPE

**Summary:** Add `window` field to JSON envelope with `since`, `until`, `session_count_before_filter`, `session_count_after_filter`. `null` when no filter applied. No schema version bump (additive).

**Blocks:** #299

---

### B6. #299 -- Update CLI help text and epilog examples for --since/--until

**Priority:** low
**Labels:** `documentation`, `epic:date-range-filtering`
**Sizing:** XS (<1 day)
**Dependencies:** #297, #298
**Status:** IN SCOPE

**Summary:** Update `--help` text, add epilog examples showing retroactive baseline workflow. Document session-granularity filtering.

---

## Stream C: CLI Ergonomics

### C1. #285 -- Redesign Top-N priority fixes summary to reduce repetition

**Priority:** medium
**Labels:** `enhancement`, `priority:medium`
**Sizing:** S (1-2 days)
**Dependencies:** None strict; benefits from #273 (axis labels on recommendations) landing first so the redesign incorporates axis info
**Status:** IN SCOPE

**Summary:** Replace duplicate `representative_message` in the Top-N summary with a dense pointer list that references the Recommendations table by row number. Design questions resolved during implementation (see issue body).

---

## Stream D: Bug Fix (already resolved)

### D1. #292 -- pm Write hook conflicts with `memory: user`

**Priority:** critical (was blocking)
**Labels:** `bug`
**Sizing:** XS (already fixed)
**Dependencies:** None
**Status:** IN SCOPE (merged)

**Summary:** Extended pm.md Write hook regex to allow `~/.claude/agent-memory/pm/`. Decision D023.

---

## Stream E: Docs

### E1. #287 -- docs: catch up README + GLOSSARY + CHANGELOG for v0.6

**Priority:** required-for-release
**Labels:** `documentation`, `priority:medium`
**Sizing:** M (2-3 days)
**Dependencies:** All feature work complete (docs reflect what shipped)
**Status:** IN SCOPE

**Summary:** Update README (roadmap, features, analyze section, config table, JSON example, screenshots), GLOSSARY (new terms), CHANGELOG (prose expansion). Scoped to what actually ships in v0.6 -- if stretch items don't land, docs exclude them.

---

## Stretch Scope

Pull in only if must-include scope completes ahead of schedule. Priority order:

### S1. #274 -- Quality signal calibration notebook + threshold tuning

**Priority:** high (within stretch)
**Labels:** `enhancement`, `priority:high`, `epic:quality-axis`
**Sizing:** M (2-3 days)
**Dependencies:** #269, #270, #271, #272 all merged
**Status:** STRETCH

**Summary:** Calibration sweep against real dogfood data. Same pattern as #260 (offload calibration). Tunes thresholds for `USER_CORRECTION`, `FILE_REWORK`, `REVIEWER_CAUGHT` signals and `W_QUALITY` weight.

**Why stretch not must-include:** Conservative defaults from #269-#272 are shippable. Calibration improves precision but doesn't gate functionality. Can land in v0.6.1 if precision issues surface post-release.

---

### S2. #275 -- Local git feat-then-fix proximity signal (Tier 2)

**Priority:** medium (within stretch)
**Labels:** `enhancement`, `priority:low`, `epic:quality-axis`
**Sizing:** M (2-3 days)
**Dependencies:** Tier 1 (#269-#273) stable; introduces `git log` subprocess
**Status:** STRETCH

**Summary:** New data source (local git). Detect `feat:` followed by `fix:` on same files within N days. Gated behind `--git` flag.

**Why stretch:** New data source, new subprocess dependency. Only if Tier 1 is solid.

---

### S3. #281 -- Bound ERROR_REGEX in error rate + error signal extraction

**Priority:** low (within stretch)
**Labels:** `enhancement`
**Sizing:** S (1-2 days)
**Dependencies:** None
**Status:** STRETCH

**Summary:** Follow-up from #241. Bounds the error regex to reduce false-positive error signals. Improves signal precision.

---

### S4. #265 -- Consolidate CLI formatter test helpers

**Priority:** low (within stretch)
**Labels:** `testing`
**Sizing:** S (1-2 days)
**Dependencies:** None
**Status:** STRETCH

**Summary:** Tech debt. Consolidate `_render` console helper and `_draft` builder across CLI test files. Reduces maintenance burden for future CLI changes (including #285).

---

### S5. #264 -- Capture tool_result.is_error per burst for cluster error_rate

**Priority:** low (within stretch)
**Labels:** `enhancement`
**Sizing:** S (1-2 days)
**Dependencies:** None
**Status:** STRETCH

**Summary:** Enriches parent-thread offload candidates with per-burst error rate. Useful for quality-axis composition but not blocking.

---

## Deferred to v0.7

| # | Title | Priority | Rationale |
|---|-------|----------|-----------|
| #198 | Markdown report export | medium | Output format. Should incorporate quality-axis attribution. Design benefits from v0.6 output stabilizing. |
| #201 | Per-session diagnostics scope | medium | Deep pipeline touch. Benefits from quality axis and date-range settling. |
| #203 | Inline trace excerpts on critical findings | low | UX polish. Not blocking quality or temporal features. |
| #204 | `list` table: cost column + JSON | low | Wiring task. Not on critical path. |
| #263 | Recalibrate parent-thread offload thresholds | medium | Needs more diverse contributor data. |
| #262 | Extract bursts at parse time | low | Performance optimization. No user-visible change. |
| #170 | Concrete target model in all model-routing recs | medium | Good improvement but independent of v0.6 themes. |
| #171 | Verify MCP audit rules fire | medium | Investigative. May be data-clean (no-op). |
| #183 | Delegation drafts: skill-aware provenance | medium | Requires skill scanner. Novel infrastructure. v0.7+. |

---

## Implementation Priority Order

### Wave 1 -- Foundations (days 1-5, parallel streams)

**Stream A (quality):**
1. **#269** -- User corrections + module skeleton (S-M, no deps, critical-path)

**Stream B (temporal):**
2. **#294** -- Timestamp extraction in discovery (S, no deps, critical-path)
3. **#295** -- Datetime parsing utility (S, no deps, parallel with #294)

### Wave 2 -- Core features (days 4-12, parallel within and across streams)

**Stream A (quality):**
4. **#270** -- File rework density (S, depends on #269)
5. **#271** -- Reviewer-caught rate (M, depends on #269)
6. **#272** -- Multi-axis scoring (M, depends on #269)

**Stream B (temporal):**
7. **#296** -- `list --since/--until` (S, depends on #294, #295)
8. **#297** -- `analyze --since/--until` (S-M, depends on #294, #295)

### Wave 3 -- Output and integration (days 10-16)

**Stream A (quality):**
9. **#273** -- CLI/JSON axis labels (S-M, depends on #272)

**Stream B (temporal):**
10. **#298** -- JSON `window` metadata (XS, depends on #297)
11. **#299** -- CLI help text (XS, depends on #298)

**Stream C (CLI):**
12. **#285** -- Top-N redesign (S, benefits from #273 but not blocked)

### Wave 4 -- Already done

13. **#292** -- pm hook fix (XS, already merged)

### Wave 5 -- Stretch (if time allows, days 14-20)

14. **#274** -- Quality signal calibration notebook (M, depends on A1-A4)
15. **#281** -- Bound ERROR_REGEX (S)
16. **#265** -- Test helper consolidation (S)
17. **#264** -- is_error per burst (S)
18. **#275** -- Git feat-fix proximity (M, depends on Tier 1 stable)

### Wave 6 -- Release prep (days 18-22)

19. **#287** -- Docs catch-up (M, depends on all features being final)
20. Dogfood validation run (`agentfluent analyze --project agentfluent --since <v0.5.1-date> --diagnostics`)
21. Release prep (changelog, version bump, CI green)

---

## Ordered Backlog (flat view)

| Order | # | Title | In/Out | Priority | Deps | Stream |
|-------|---|-------|--------|----------|------|--------|
| 1 | #269 | User corrections + module skeleton | IN | critical-path | none | A |
| 2 | #294 | Timestamp extraction | IN | critical-path | none | B |
| 3 | #295 | Datetime parsing utility | IN | high | none | B |
| 4 | #270 | File rework density | IN | high | #269 | A |
| 5 | #271 | Reviewer-caught rate | IN | high | #269 | A |
| 6 | #272 | Multi-axis scoring | IN | high | #269 | A |
| 7 | #296 | `list --since/--until` | IN | medium | #294, #295 | B |
| 8 | #297 | `analyze --since/--until` | IN | high | #294, #295 | B |
| 9 | #273 | CLI/JSON axis labels | IN | high | #272 | A |
| 10 | #298 | JSON `window` metadata | IN | medium | #297 | B |
| 11 | #299 | CLI help text | IN | low | #298 | B |
| 12 | #285 | Top-N redesign | IN | medium | none* | C |
| 13 | #292 | pm hook fix | IN | done | none | D |
| 14 | #287 | docs catch-up | IN | required | all features | E |
| 15 | #274 | Calibration notebook | STRETCH | high | #269-#272 | A |
| 16 | #281 | Bound ERROR_REGEX | STRETCH | low | none | -- |
| 17 | #265 | Test helper consolidation | STRETCH | low | none | -- |
| 18 | #264 | is_error per burst | STRETCH | low | none | -- |
| 19 | #275 | Git feat-fix proximity | STRETCH | medium | Tier 1 | A |
| -- | #198 | Markdown report export | DEFERRED | medium | v0.6 output stable | v0.7 |
| -- | #201 | Per-session diagnostics scope | DEFERRED | medium | quality + temporal | v0.7 |
| -- | #203 | Inline trace excerpts | DEFERRED | low | -- | v0.7 |
| -- | #204 | `list` cost column + JSON | DEFERRED | low | -- | v0.7 |
| -- | #263 | Offload threshold recalibration | DEFERRED | medium | more data | v0.7 |
| -- | #262 | Parse-time burst extraction | DEFERRED | low | -- | v0.7 |
| -- | #170 | Concrete model targets | DEFERRED | medium | -- | v0.7 |
| -- | #171 | Verify MCP audit rules | DEFERRED | medium | -- | v0.7 |
| -- | #183 | Skill-aware provenance | DEFERRED | medium | -- | v0.7+ |

\* #285 benefits from #273 (axis labels in output) landing first so the redesign can incorporate them, but is not strictly blocked.

---

## Estimated Total

**Must-include: 17 issues, ~22-26 dev days (3-4 weeks)**
**With stretch: +5 issues, ~8-12 additional dev days**

The two epic streams parallelize well. A solo developer can interleave: e.g., start #269 (quality), then #294/#295 (temporal) while #269 is in review, then #270/#271 (quality) + #296/#297 (temporal). The parallelism reduces calendar time relative to sequential execution.
