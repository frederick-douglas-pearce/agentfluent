# AgentFluent v0.7 Backlog

Ordered backlog for v0.7 (From Signals to Answers). Issues are sequenced by dependency chain, not by issue number.

**Theme:** Trust what you see. Share what you find. Drill into what matters.

**Milestone:** v0.7.0

---

## Triage Summary

| Disposition | Count | Issues |
|-------------|-------|--------|
| Already shipped | 4 | #342, #344, #345, #347 |
| In scope (open) | 13 | #343, #346, #353, #354, #355, #356, #357, #358, #359, #360, #352, #275, #326 |
| Stretch | 1 | #275 |
| Total in milestone | 18 | (including parent issues #198, #201, #349, #350, #351) |

---

## Already Shipped

Four stories merged on 2026-05-09, before this backlog was drafted. No further work needed.

### S1. #342 -- Propagate baseline/current window metadata into diff table + JSON

**Status:** SHIPPED (PR #361, merged 2026-05-09)
**Epic:** #349 (Diff hardening)
**Summary:** Diff table shows `Baseline: <date range> (N sessions) | Current: ...` header. JSON carries `baseline_window` / `current_window`. Legacy envelopes render `(window not recorded, N sessions)`.

---

### S2. #347 -- Stamp + warn on diagnostics-version drift between baseline and current

**Status:** SHIPPED (PR #361, merged 2026-05-09)
**Epic:** #349 (Diff hardening)
**Summary:** `analyze --json` envelopes carry `diagnostics_version`. Diff emits yellow warning when versions differ; dim "version unknown" when one side predates the field; silent when both unknown. Decision D034.

---

### S3. #344 -- Filter or relabel offload candidates with negative savings

**Status:** SHIPPED (PR #363, merged 2026-05-09)
**Epic:** #350 (Diagnostics presentation)
**Summary:** Negative-savings rows hidden in CLI by default. `--show-negative-savings` flag opts back in. JSON unchanged. Empty-positive case renders informational footnote.

---

### S4. #345 -- Cluster auto-names: stopword filter for issue numbers / version refs

**Status:** SHIPPED (PR #363, merged 2026-05-09)
**Epic:** #350 (Diagnostics presentation)
**Summary:** Stopword filter strips pure-numeric, version-ref (`v0`, `v06`), and 7+ hex-char SHA-prefix tokens from cluster slug names. `top_terms` in JSON kept raw.

---

## Stream A: Diff Hardening -- Epic #349

One story remaining after PR #361 shipped #342 and #347.

### A1. #343 -- Token Metrics by Model: deduplicate rows when (model, origin) pairs collide

**Priority:** medium
**Labels:** `enhancement`, `priority:medium`, `epic:v07-diff-hardening`
**Sizing:** S (1-2 days)
**Dependencies:** None
**Status:** IN SCOPE

**Summary:** Diff table shows visually identical model rows when `(model, origin)` pairs exist for the same model (e.g., `claude-opus-4-7` appearing twice: once for parent, once for subagent). Add `Origin` column between Model and Baseline cost. Table key matches `analyze` JSON's `(model, origin)` natural key.

**Acceptance criteria:**
- Diff table no longer shows visually-duplicate model rows
- `Origin` column visible in table output
- Table key matches `(model, origin)` from analyze JSON

**Blocks:** Nothing

---

## Stream B: Diagnostics Presentation -- Epic #350

One story remaining after PR #363 shipped #344 and #345.

### B1. #346 -- Detect 'agent defined but never delegated to' (unused agent)

**Priority:** medium
**Labels:** `enhancement`, `priority:medium`, `epic:v07-diagnostics-presentation`
**Sizing:** M (2-3 days)
**Dependencies:** None
**Status:** IN SCOPE

**Summary:** New `unused_agent` diagnostic signal. Fires when a custom agent is present in config-scanner output but has zero invocations in `agent_metrics.by_agent_type` over the analyzed window. Severity: `info`. Recommendation includes the agent's current `description` and remediation suggestions (rewrite description, check delegation triggers, accept it's unused). Built-in agents (Explore, Plan, general-purpose) silently excluded per D033.

**Key considerations:**
- Cross-references config scanner output with agent metrics -- needs both data sources available
- Partial-window confound acknowledged in message for recently added agents
- `agentfluent explain unused_agent` output required
- Unit test with fixture where one custom agent has invocations and another doesn't

**Blocks:** Nothing

---

## Stream C: Output Scope + Shareability -- Epic #351

The largest stream. Two independent sub-epics: Markdown report (#198) and per-session diagnostics (#201). The user wants the report command implemented first.

### Sub-epic: Markdown Report (#198)

Dependency chain: #353 -> #354 -> #355 + #356 (parallel).

### C1. #353 -- `agentfluent report` command skeleton + JSON ingestion

**Priority:** high (user wants this first)
**Labels:** `enhancement`, `priority:medium`, `epic:v07-output-shareability`
**Sizing:** S-M (2-3 days)
**Dependencies:** None
**Status:** IN SCOPE

**Summary:** Create the `agentfluent report` subcommand. New `cli/report_cmd.py` module with Typer command. Accepts positional argument (path to `analyze --json` output file). Optional `--output` flag for file output (default: stdout). Loads and validates the JSON envelope (required fields: `data`, `metadata`). Section renderers are stubs in this story. Decision D031.

**Acceptance criteria:**
- `agentfluent report snap.json` produces Markdown output to stdout
- `agentfluent report snap.json --output report.md` writes to file
- Invalid JSON / missing file / wrong envelope format produce clear error messages
- `agentfluent report --help` shows usage with examples
- Composable: `analyze --json > snap.json && report snap.json` works end-to-end
- Unit tests: valid ingestion, missing file, invalid JSON, wrong envelope

**Blocks:** #354

---

### C2. #354 -- Section renderers: token/cost, agent table, diagnostics, offload

**Priority:** high
**Labels:** `enhancement`, `priority:medium`, `epic:v07-output-shareability`
**Sizing:** M (3-4 days)
**Dependencies:** #353 (command skeleton)
**Status:** IN SCOPE

**Summary:** Implement Markdown section renderers. Each renderer is a standalone function: `render_summary(data) -> str`, `render_token_metrics(data) -> str`, etc. Sections: header/summary, token/cost metrics (table), agent metrics (table), diagnostics/recommendations (severity-grouped), offload candidates (if present), footer (reproduction command + timestamp).

**Key considerations:**
- Tables must render correctly in GitHub Markdown preview
- Empty sections produce "No findings" note, not empty output
- Axis labels appear on recommendations (`[cost]`, `[speed]`, `[quality]`)
- Use string formatting, not a template engine (minimal dependencies)
- Each renderer is independently testable

**Blocks:** #355, #356

---

### C3. #355 -- Tests + golden Markdown fixture

**Priority:** medium
**Labels:** `testing`, `epic:v07-output-shareability`
**Sizing:** S-M (1-2 days)
**Dependencies:** #354 (renderers must exist)
**Status:** IN SCOPE

**Summary:** Comprehensive test coverage for `agentfluent report`. Golden Markdown fixture in `tests/fixtures/` for snapshot testing. Unit tests per renderer with edge cases (empty diagnostics, zero agents, single session). Integration test: end-to-end `analyze --json | report` pipeline. Backward compatibility test: report handles analyze JSON from v0.6 gracefully.

**Blocks:** Nothing

---

### C4. #356 -- docs: report command GLOSSARY entry + README update + CLI help

**Priority:** low
**Labels:** `documentation`, `epic:v07-output-shareability`
**Sizing:** S (1 day)
**Dependencies:** #354 (docs describe what shipped)
**Status:** IN SCOPE

**Summary:** GLOSSARY entry for `report` subcommand. README: command overview table + workflow example (`analyze --json > snap.json && report snap.json > report.md`). CLI help epilog with common invocations. No references to the rejected `analyze --format markdown` approach.

**Blocks:** Nothing

---

### Sub-epic: Per-Session Diagnostics Scope (#201)

Dependency chain: #357 -> #358 -> #359 + #360 (parallel).

### C5. #357 -- Plumb `--session` through to diagnostics aggregator

**Priority:** medium
**Labels:** `enhancement`, `priority:medium`, `epic:v07-output-shareability`
**Sizing:** M (2-3 days)
**Dependencies:** None
**Status:** IN SCOPE

**Summary:** When `--session <uuid>` is provided, restrict the diagnostics aggregation to that session's invocations only. The change is in how the session list is constructed for the aggregation pass, not in per-session signal extraction. The simplest approach: filter the session list before it reaches `run_diagnostics()`. Decision D032.

**Acceptance criteria:**
- Token/cost metrics scoped to named session (existing, verify preserved)
- Diagnostics signals extracted only from named session
- Aggregated recommendations reflect only that session's signals
- Offload candidates scoped to named session
- Quality signals (USER_CORRECTION, FILE_REWORK, REVIEWER_CAUGHT) scoped
- JSON `data.diagnostics_result` reflects single-session scope
- Session UUID appears in output metadata
- Without `--session`, behavior unchanged (all sessions aggregated)

**Blocks:** #358

---

### C6. #358 -- Update CLI to flow `--session` semantics consistently

**Priority:** medium
**Labels:** `enhancement`, `priority:medium`, `epic:v07-output-shareability`
**Sizing:** S-M (1-2 days)
**Dependencies:** #357 (pipeline plumbing)
**Status:** IN SCOPE

**Summary:** CLI output consistently reflects per-session scope. Table header shows "Session: <uuid>" not "Project: P (N sessions)". JSON envelope `metadata` includes `session` field. Flag interactions: `--session` + `--since`/`--until` errors (mutually exclusive per D024); `--session` + `--latest N` errors.

**Blocks:** #359, #360

---

### C7. #359 -- Tests for single-session and full-window cases

**Priority:** medium
**Labels:** `testing`, `epic:v07-output-shareability`
**Sizing:** S-M (1-2 days)
**Dependencies:** #357, #358
**Status:** IN SCOPE

**Summary:** Test coverage for per-session diagnostics scope. Key assertion: run diagnostics on all sessions, run on single session, verify single-session output is a subset. Multi-session fixture where sessions have different diagnostics profiles. Edge case: single session with no findings produces empty result, not error.

**Blocks:** Nothing

---

### C8. #360 -- CHANGELOG breaking-change note for --session diagnostics auto-scope

**Priority:** low
**Labels:** `documentation`, `epic:v07-output-shareability`
**Sizing:** XS (<1 day)
**Dependencies:** #357 or #358 (document alongside implementation)
**Status:** IN SCOPE

**Summary:** CHANGELOG entry with `BREAKING CHANGE:` notation. Explains: old behavior (diagnostics rolled up all sessions even with `--session`), new behavior (diagnostics auto-scoped), rationale (consistency with metrics scope). References D032.

**Blocks:** Nothing

---

## Stream D: Research

### D1. #352 -- Tier 3 GitHub enrichment scoping spike

**Priority:** medium
**Labels:** `enhancement`
**Sizing:** M (2-3 days)
**Dependencies:** None
**Status:** IN SCOPE

**Summary:** Time-boxed design spike producing `.claude/specs/prd-tier3-github-enrichment.md`. Covers: auth model (`gh` CLI vs MCP vs PAT), optional dependency strategy, rate-limit/caching, signal selection and prioritization, privacy model. No implementation code. Acceptance criteria prohibit code artifacts.

**Key constraint:** Bounded to 2-3 dev days. The deliverable is a design document actionable enough that a developer starting v0.8 can begin implementation without additional design conversations.

**Blocks:** v0.8 Tier 3 implementation (not v0.7 work)

---

## Stream E: Docs

### E1. #326 -- docs: catch up README + GLOSSARY + CHANGELOG for v0.7.0

**Priority:** required-for-release
**Labels:** `documentation`, `enhancement`, `priority:medium`
**Sizing:** M (2-3 days)
**Dependencies:** All feature work complete (docs reflect what shipped)
**Status:** IN SCOPE

**Summary:** Update README (roadmap, command table, sample output, JSON example, screenshots), GLOSSARY (new terms: `report`, `unused_agent`, per-session scope), CHANGELOG (prose expansion beyond release-please auto-entries). Verify `.claude/specs/` PRDs reflect what shipped. Append decision log entries for scope changes during implementation.

---

## Stretch Scope

### S1. #275 -- Local git feat-then-fix proximity signal (Tier 2)

**Priority:** low (within stretch)
**Labels:** `enhancement`, `priority:low`
**Sizing:** M (2-3 days)
**Dependencies:** None for implementation; conceptually builds on Tier 1 quality signals (shipped in v0.6)
**Status:** STRETCH

**Summary:** Detect `feat:` commit followed by `fix:` on same files within N days using `git log` subprocess. Correlate to session review-agent usage. New `SignalType.FEAT_FIX_PROXIMITY`. Gated behind `--git` flag. Deferred from v0.6 per D028.

**Why stretch:** Introduces a new data source (git subprocess), a new CLI flag, and heuristic timestamp linkage. Risk surface unlike anything else in v0.7. Natural companion to v0.8 Tier 3 work.

---

## Implementation Priority Order

### Wave 1 -- Report command skeleton (start immediately)

The user has signaled the report command is the next implementation target.

1. **#353** -- Report command skeleton + JSON ingestion (S-M, no deps, entry point)

### Wave 2 -- Report renderers + parallel independents (days 3-8)

2. **#354** -- Section renderers (M, depends on #353)
3. **#343** -- Diff model row deduplication (S, independent, can parallel)
4. **#346** -- Unused agent signal (M, independent, can parallel)

### Wave 3 -- Report validation + per-session scope (days 7-14)

5. **#355** -- Report tests + golden fixture (S-M, depends on #354)
6. **#356** -- Report docs (S, depends on #354)
7. **#357** -- `--session` diagnostics plumbing (M, independent of report)
8. **#358** -- CLI `--session` semantics (S-M, depends on #357)

### Wave 4 -- Per-session validation + research (days 12-18)

9. **#359** -- Per-session tests (S-M, depends on #357, #358)
10. **#360** -- CHANGELOG breaking-change note (XS, depends on #357 or #358)
11. **#352** -- Tier 3 design spike (M, independent, time-boxed)

### Wave 5 -- Stretch (if time allows)

12. **#275** -- Git feat-fix proximity (M, independent)

### Wave 6 -- Release prep (days 18-24)

13. **#326** -- Docs catch-up (M, depends on all features being final)
14. Dogfood validation runs (report + per-session scope)
15. Release prep (changelog, version bump, CI green)

---

## Ordered Backlog (flat view)

| Order | # | Title | In/Out | Priority | Deps | Stream |
|-------|---|-------|--------|----------|------|--------|
| -- | #342 | Window metadata in diff | SHIPPED | -- | -- | A |
| -- | #347 | Diagnostics-version drift | SHIPPED | -- | -- | A |
| -- | #344 | Negative-savings filter | SHIPPED | -- | -- | B |
| -- | #345 | Cluster-name stopwords | SHIPPED | -- | -- | B |
| 1 | #353 | Report command skeleton | IN | high | none | C |
| 2 | #354 | Report section renderers | IN | high | #353 | C |
| 3 | #343 | Diff model deduplication | IN | medium | none | A |
| 4 | #346 | Unused agent signal | IN | medium | none | B |
| 5 | #355 | Report tests + golden fixture | IN | medium | #354 | C |
| 6 | #356 | Report docs | IN | low | #354 | C |
| 7 | #357 | --session diagnostics plumbing | IN | medium | none | C |
| 8 | #358 | CLI --session semantics | IN | medium | #357 | C |
| 9 | #359 | Per-session tests | IN | medium | #357, #358 | C |
| 10 | #360 | CHANGELOG breaking-change note | IN | low | #357 | C |
| 11 | #352 | Tier 3 design spike | IN | medium | none | D |
| 12 | #275 | Git feat-fix proximity | STRETCH | low | none | -- |
| 13 | #326 | Docs catch-up | IN | required | all features | E |

---

## Estimated Total

**Must-include (remaining): 13 open issues, ~18-24 dev days (3-4 weeks)**
**Already shipped: 4 issues (via PRs #361, #363)**
**With stretch: +1 issue, ~2-3 additional dev days**

The three epic streams (diff hardening, diagnostics presentation, output shareability) are fully independent. The report sub-epic and per-session sub-epic within Stream C are also independent. A solo developer can interleave: start with report skeleton (#353), then parallelize #354 with #343/#346, then shift to per-session scope while report tests/docs settle.
