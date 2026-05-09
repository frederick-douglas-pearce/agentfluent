# PRD: AgentFluent v0.7 -- From Signals to Answers

**Status:** Draft
**Date:** 2026-05-09
**Author:** PM Agent
**Decision log:** See `decisions.md` D028-D034 for scoping and design decisions.
**Backlog:** See `backlog-v0.7.md` for the full sequenced backlog.

---

## 1. Theme

**"From signals to answers."**

v0.6 completed the analytical core: three diagnostics axes (cost, speed, quality), temporal filtering, and before/after comparison via `diff`. AgentFluent now detects what to change. But the output stays trapped in a terminal session -- there is no way to share findings in a PR comment, archive them in CI, or drill into a single session for post-mortem. And trust issues surfaced during v0.6 dogfood show that `diff` output is not yet self-documenting enough for production use.

v0.7 closes three gaps:

1. **Diff hardening for production trust.** Diff output becomes self-documenting: window context shows what time ranges were compared, duplicate model rows are eliminated, and a version-drift warning prevents silent conflation of detector-sensitivity changes with real behavior changes. CI consumers and humans reading archived diffs can trust what they see.

2. **Diagnostics presentation and config effectiveness.** Anti-recommendations (negative-savings offload candidates) are filtered from display, cluster auto-names strip noise tokens, and a new `unused_agent` signal detects custom agents that are defined but never delegated to -- the first config-effectiveness diagnostic that bridges the gap between "what is configured" and "what actually runs."

3. **Output scope and shareability.** Two long-deferred features land together: `agentfluent report` produces structured Markdown from any `analyze --json` snapshot (shareable in PRs, Slack, archives), and `--session <uuid>` auto-scopes diagnostics to that session (enabling single-session post-mortems without noise from unrelated sessions).

A design spike for Tier 3 GitHub enrichment prepares the ground for v0.8 without adding implementation risk to v0.7.

One-line pitch: **"Trust what you see. Share what you find. Drill into what matters."**

### Why this theme

The alternative was to continue adding detection capability (Tier 2 git signals, Tier 3 GitHub enrichment). That path was rejected because:

1. **Output trust gaps are blocking adoption.** The v0.6 dogfood surfaced concrete trust failures: duplicate rows, missing context, anti-recommendations displayed as recommendations. These erode confidence in the tool before any new detection capability matters.

2. **Shareability unlocks a new user loop.** Until v0.7, AgentFluent output is a terminal-only experience. `report` makes findings portable -- a developer can run `analyze`, generate a Markdown report, and attach it to a PR. This is the minimal surface for team adoption beyond a single user.

3. **Per-session scope is the #1 usability request.** The original codefluent CLI review ranked both `report` (#198) and per-session diagnostics (#201) as P1. Both have been deferred through v0.4, v0.5, and v0.6 while the output format stabilized. No remaining blockers.

4. **Detection expansion (Tier 2/3) benefits from a stable output layer.** `report` needs a settled JSON envelope. Per-session scope needs stable pipeline plumbing. Shipping both now means Tier 2/3 signals land on a surface that already handles sharing and scoping correctly.

## 2. Goals

1. **Make diff output production-ready** with self-documenting window context, deduplicated model rows, and version-drift warnings (#342, #343, #347 -- two already shipped)
2. **Clean up diagnostics presentation** by filtering anti-recommendations and stripping noise from cluster names (#344, #345 -- both already shipped)
3. **Ship the first config-effectiveness signal** detecting unused custom agents (#346)
4. **Ship `agentfluent report`** as a composable subcommand producing structured Markdown (#353, #354, #355, #356)
5. **Auto-scope diagnostics to `--session`** for single-session post-mortems (#357, #358, #359, #360)
6. **Scope Tier 3 GitHub enrichment** via a time-boxed design spike (#352)
7. **Ship docs that reflect what shipped** (#326)

## 3. Non-Goals

- LLM-powered analysis (stays rule-based)
- Auto-applying recommended fixes
- Webapp dashboard
- Cross-project aggregation
- Tier 2 git signals (FEAT_FIX_PROXIMITY) -- deferred per D028; stretch scope only
- Tier 3 GitHub enrichment implementation -- spike only, implementation deferred to v0.8
- `report` for `diff` output -- v0.7 `report` handles `analyze` snapshots; diff-report is a future extension
- `--strict` mode for diagnostics-version drift -- D034 says warn-only for now
- Negative recommendations ("remove this subagent") -- deferred per D020

## 4. In Scope (Must-Include) -- 18 issues

### Already Shipped in v0.7

Two PRs merged on 2026-05-09 before the PRD was drafted. They are part of v0.7's scope but require no further implementation work.

| # | Title | Epic | What shipped |
|---|-------|------|--------------|
| #342 | Propagate baseline/current window metadata into diff table + JSON | #349 | Window context header on diff table; `baseline_window` / `current_window` in JSON |
| #347 | Stamp + warn on diagnostics-version drift between baseline and current | #349 | `diagnostics_version` in analyze JSON envelope; yellow drift warning in diff output |
| #344 | Filter or relabel offload candidates with negative savings | #350 | Negative-savings rows hidden in CLI; `--show-negative-savings` flag; JSON unchanged |
| #345 | Cluster auto-names: stopword filter for issue numbers / version refs | #350 | Stopword filter strips pure-numeric, version-ref, SHA-prefix tokens from cluster slugs |

PR #361 closed #342 and #347. PR #363 closed #344 and #345.

### Epic 1: Diff Hardening for Production Trust -- #349

| # | Title | Effort | Status |
|---|-------|--------|--------|
| #342 | Propagate window metadata into diff table + JSON | S | SHIPPED (PR #361) |
| #343 | Token Metrics by Model: deduplicate rows when (model, origin) pairs collide | S | Open |
| #347 | Stamp + warn on diagnostics-version drift | S-M | SHIPPED (PR #361) |

**Remaining work:** #343 only. Add `Origin` column to diff Token Metrics by Model table to eliminate visually duplicate rows.

### Epic 2: Diagnostics Presentation + Config Effectiveness -- #350

| # | Title | Effort | Status |
|---|-------|--------|--------|
| #344 | Filter negative-savings offload candidates from CLI | S | SHIPPED (PR #363) |
| #345 | Cluster auto-names: stopword filter | XS-S | SHIPPED (PR #363) |
| #346 | Detect 'agent defined but never delegated to' (unused agent) | M | Open |

**Remaining work:** #346 only. New `unused_agent` diagnostic signal. Built-in agents excluded per D033.

### Epic 3: Output Scope + Shareability -- #351

#### Sub-epic: Markdown Report (#198)

| # | Title | Effort | Status |
|---|-------|--------|--------|
| #353 | `agentfluent report` command skeleton + JSON ingestion | S-M | Open |
| #354 | Section renderers: token/cost, agent table, diagnostics, offload | M | Open |
| #355 | Tests + golden Markdown fixture | S-M | Open |
| #356 | docs: GLOSSARY entry + README update + CLI help | S | Open |

Decision D031: `report` is a new subcommand (composable: `analyze --json > snap.json && report snap.json > report.md`), not `analyze --format markdown`.

#### Sub-epic: Per-Session Diagnostics Scope (#201)

| # | Title | Effort | Status |
|---|-------|--------|--------|
| #357 | Plumb `--session` through to diagnostics aggregator | M | Open |
| #358 | Update CLI to flow `--session` semantics consistently | S-M | Open |
| #359 | Tests for single-session and full-window cases | S-M | Open |
| #360 | CHANGELOG breaking-change note | XS | Open |

Decision D032: `--session <uuid>` auto-scopes diagnostics. **Behavior change from v0.6** -- in v0.6, `--session` scoped token/cost metrics but diagnostics rolled up all sessions. In v0.7, diagnostics respect the session scope.

### Research

| # | Title | Effort | Status |
|---|-------|--------|--------|
| #352 | Tier 3 GitHub enrichment scoping spike | M (2-3 days) | Open |

Design-only deliverable: `.claude/specs/prd-tier3-github-enrichment.md` covering auth model, optional dependency strategy, rate-limit/caching, signal selection, and privacy model. No implementation code. Gates v0.8.

### Docs

| # | Title | Effort | Status |
|---|-------|--------|--------|
| #326 | docs: catch up README + GLOSSARY + CHANGELOG for v0.7.0 | M | Open |

Auto-created by the docs workflow when the v0.7.0 milestone was opened.

### Stretch

| # | Title | Effort | Status |
|---|-------|--------|--------|
| #275 | Local git feat-then-fix proximity signal (Tier 2) | M (2-3 days) | Open |

Deferred from v0.6 per D028. Introduces a new data source (git subprocess). Pull in only if all must-include scope completes ahead of schedule. Gated behind `--git` flag.

**Total in-scope: 18 issues (4 shipped, 14 open), ~18-24 dev days remaining**

## 5. Open Questions / Decisions Needed

### ~~OQ1~~ — Resolved as D029: `--session` breaking change → CHANGELOG + minor bump

The `--session` behavior change (D032) is a semantics-level breaking change. **Resolved 2026-05-09:** Document under `BREAKING CHANGE:` in CHANGELOG, keep release-please's minor bump (0.7.0). Tracked by #360. Rejected: `feat!:` major bump (0.x reserves majors for 1.0); deprecation period (added complexity without benefit pre-1.0). See `decisions.md` D029.

### ~~OQ2~~ — Resolved as D030: report section ordering → metrics first

**Resolved 2026-05-09:** Section order is Summary → Token Metrics → Agent Metrics → Diagnostics → Offload → Footer. Mirrors the `analyze` table order; grounds readers in data before recommendations resolve metric references. See `decisions.md` D030.

### OQ3: Should `report` handle `diff` JSON in addition to `analyze` JSON?

The current scope (D031) explicitly limits `report` to `analyze --json` snapshots. A natural follow-up is `report diff.json` producing a Markdown diff report. This is NOT in v0.7 scope but should be flagged so the report architecture does not foreclose it.

**Recommendation:** Defer to v0.8. Ensure the report skeleton has an extensible dispatch pattern (envelope type detection) so adding diff-report is additive. No action needed now beyond noting it in the PRD.

## 6. Decisions Made (Referenced)

| ID | Summary | Date | Reference |
|----|---------|------|-----------|
| D028 | FEAT_FIX_PROXIMITY deferred from v0.6 to v0.7 stretch | 2026-05-08 | `decisions.md` |
| D029 | `--session` breaking change communicated via CHANGELOG, minor bump | 2026-05-09 | `decisions.md` |
| D030 | `report` section ordering: metrics first, then diagnostics | 2026-05-09 | `decisions.md` |
| D031 | `report` is a new subcommand, not `--format markdown` | 2026-05-09 | #351 body |
| D032 | `--session` auto-scopes diagnostics (behavior change) | 2026-05-09 | #351 body |
| D033 | Built-in agents excluded from unused-agent signal | 2026-05-09 | #350 body |
| D034 | Diagnostics-version drift is warn-only (no `--strict`) | 2026-05-09 | #349 body |

## 7. Dependencies

```
SHIPPED:
[#342 window metadata] ──> DONE (PR #361)
[#347 version drift]   ──> DONE (PR #361)
[#344 negative-savings] ─> DONE (PR #363)
[#345 stopword filter]  ─> DONE (PR #363)

REMAINING:
[#343 deduplicate model rows] -- independent (last #349 story)

[#346 unused_agent signal] -- independent (last #350 story)

[#353 report skeleton] ──> [#354 section renderers] ──> [#355 tests + golden fixture]
                                                    └──> [#356 docs]

[#357 --session pipeline] ──> [#358 CLI semantics] ──> [#359 tests]
                                                   └──> [#360 CHANGELOG note]

[#352 Tier 3 spike] -- independent

[#326 docs catch-up] -- last (reflects what shipped)

[#275 git feat-fix] -- STRETCH (no deps on must-include work)
```

### Cross-epic independence

The three epics and the research spike have zero cross-dependencies. They can be implemented in any order. The user has signaled that the report command (epic #351) should start first.

## 8. Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| `report` Markdown format is hard to get right for all GitHub/Slack renderers | Report looks broken in target environments; users lose trust in the feature | Test against GitHub Markdown preview specifically (#355 golden fixture). Use simple tables, no HTML. Iterate on format in v0.7.x patches. |
| Per-session scope breaks existing scripts that relied on project-level diagnostics with `--session` | CI pipelines produce different output after upgrade | CHANGELOG breaking-change note (#360). The 0.x version posture permits this. The old behavior was inconsistent (metrics scoped, diagnostics not), so the "breakage" is a bug fix. |
| `unused_agent` signal has high false-positive rate for new agents | Users dismiss config-effectiveness signals | Conservative approach: severity `info` not `warning`. Message acknowledges partial-window confound. Built-in agents excluded (D033). |
| Tier 3 spike scope creeps into implementation | Research consumes implementation time | Spike is explicitly time-boxed (2-3 days) with a design-document deliverable. Acceptance criteria prohibit implementation code. |
| #275 stretch scope is never pulled in | Tier 2 quality signal remains unshipped | Acceptable. Tier 1 signals (shipped in v0.6) cover the under-recommendation gap. #275 adds confirming evidence but is not needed for the product goal. Natural fit for v0.8 alongside Tier 3 implementation. |

## 9. Success Criteria

v0.7 is successful when:

1. **Diff output is self-documenting.** `agentfluent diff baseline.json current.json` shows window context, has no visually duplicate model rows, and warns when diagnostics versions differ. A CI consumer reading the diff artifact can understand what was compared without re-running commands.
2. **Diagnostics presentation is clean.** No negative-savings candidates appear as recommendations. Cluster auto-names describe behavior patterns, not issue numbers. The `unused_agent` signal fires when a custom agent is defined but never delegated to in the analyzed window.
3. **`agentfluent report snap.json` produces well-formed Markdown.** The output is readable as a standalone document, renders correctly in GitHub Markdown preview, and includes all analyze sections (summary, token metrics, agent metrics, diagnostics, offload candidates, footer with reproduction command).
4. **`--session <uuid>` auto-scopes diagnostics.** Token/cost metrics AND diagnostics are both restricted to the named session. Recommendations reflect only that session's signals. JSON output includes session metadata.
5. **The Tier 3 spike is complete.** A design document exists at `.claude/specs/prd-tier3-github-enrichment.md` covering auth, dependencies, rate limits, signal selection, and privacy. A developer starting v0.8 can begin implementation without additional design conversations.
6. **All new code has >80% test coverage.** No regressions.
7. **Docs reflect what shipped.** README, GLOSSARY, CHANGELOG all updated (#326).

## 10. Release Checklist

- [x] #342 merged: window metadata in diff table + JSON (PR #361)
- [x] #347 merged: diagnostics-version stamp + drift warning (PR #361)
- [x] #344 merged: negative-savings offload candidates hidden (PR #363)
- [x] #345 merged: cluster auto-name stopword filter (PR #363)
- [ ] #343 merged: Token Metrics by Model deduplicated with Origin column
- [ ] #346 merged: `unused_agent` diagnostic signal
- [ ] #353 merged: `agentfluent report` command skeleton + JSON ingestion
- [ ] #354 merged: report section renderers
- [ ] #355 merged: report tests + golden Markdown fixture
- [ ] #356 merged: report docs (GLOSSARY, README, CLI help)
- [ ] #357 merged: `--session` plumbed through diagnostics aggregator
- [ ] #358 merged: CLI `--session` semantics updated
- [ ] #359 merged: per-session scope tests
- [ ] #360 merged: CHANGELOG breaking-change note for `--session`
- [ ] #352 merged: Tier 3 GitHub enrichment design spike
- [ ] #326 merged: docs catch-up (README, GLOSSARY, CHANGELOG)
- [ ] Dogfood run: `agentfluent analyze --project agentfluent --json > snap.json && agentfluent report snap.json` produces clean Markdown
- [ ] Dogfood run: `agentfluent analyze --project agentfluent --session <uuid> --diagnostics` shows session-scoped results
- [ ] `uv run pytest --cov=agentfluent` passes with >80% coverage
- [ ] `uv run ruff check src/` clean
- [ ] `uv run mypy src/agentfluent/` clean
- [ ] CHANGELOG updated via release-please
- [ ] Version bump to 0.7.0
