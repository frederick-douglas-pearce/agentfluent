# PRD: AgentFluent v0.6 -- Quality Axis + Temporal Scoping

**Status:** Draft
**Date:** 2026-05-05
**Author:** PM Agent
**Decision log:** See `decisions.md` D026 for the headline scoping decision.
**Backlog:** See `backlog-v0.6.md` for the full sequenced backlog.

---

## 1. Theme

**"The quality axis and the tools to prove it."**

v0.5 delivered trustworthy diagnostics -- reliable signals, distribution-grounded outlier detection, priority ranking, and `agentfluent diff` for before/after comparison. But the comparison workflow had a gap: no way to scope analysis to a time window. And the diagnostics engine still evaluated agents only on cost and speed -- missing the strongest reason to delegate to review subagents: **quality**.

v0.6 closes both gaps:

1. **Quality as a third diagnostics axis.** Three new signals detect quality failures observable in existing session data -- user mid-flight corrections, file rework density, and reviewer-caught rate. Multi-axis scoring ensures quality-motivated recommendations surface even when they don't save tokens. The under-recommendation gap for review subagents is closed.

2. **Date/time-range filtering.** `--since`/`--until` flags on `analyze` and `list` enable temporal scoping -- the missing ingredient that makes the `diff` workflow retroactive. After a config change, users can generate before/after baselines without having proactively saved one.

Together these deliver on the release tagline: the quality axis tells you *what quality improvements to make*, and temporal filtering lets you *prove they worked*.

One-line pitch: **"Know what to change for quality, not just cost. Prove it worked without a time machine."**

### Why quality axis IS the v0.6 headline (not deferred to v0.7)

The alternative was shipping date-range filtering + cleanup as v0.6 and deferring quality to v0.7. That path was rejected for three reasons:

1. **Credibility gap is urgent.** AgentFluent's current recommendations systematically under-recommend review-style subagents because only cost and speed are scored. This diverges from best practice. Every release that ships without the quality axis is a release where users following AgentFluent's advice get worse agents.

2. **v0.5 built the exact scaffolding quality needs.** Priority ranking (#172), offload candidates (#189), calibration sweep (#260), and `diff` (#199) are all shipped and stable. The infrastructure is fresh. Deferring to v0.7 risks the scaffolding going stale or requiring re-familiarization.

3. **Date-range filtering alone is a feature, not a theme.** A release needs a compelling narrative. "You can now filter by date" is a nice-to-have. "AgentFluent now evaluates quality and you can prove changes worked over time" is a product moment worth announcing.

4. **Effort fits.** Tier-1 quality axis is estimated at 12-15 dev days. Date-range filtering is 5-8 days. Combined with polish/docs, the total is ~22-28 dev days -- within the 3-4 week window.

### Why date-range filtering IS v0.6 (not separate)

Date-range filtering (already scoped in `prd-date-range-filtering.md`, D024/D025) directly serves the dogfooding loop: after applying the pm.md fixes from #291/#292, the user needs `--since` to verify the improvement without historical noise. It also composes with the quality axis: once quality signals ship, `--since`/`--until` lets users measure whether adding an architect agent actually reduced `USER_CORRECTION` and `FILE_REWORK` signals in subsequent sessions.

## 2. Goals

1. **Ship three Tier-1 quality signals** -- user mid-flight corrections, file rework density, reviewer-caught rate (#269, #270, #271)
2. **Add multi-axis scoring** -- quality signals integrate into priority ranking; recommendations display which axis triggered them (#272, #273)
3. **Calibrate quality thresholds** -- sweep against real dogfood data to set conservative defaults (#274)
4. **Enable temporal scoping** -- `--since`/`--until` on `analyze` and `list` commands (#293-#299)
5. **Close the dogfooding loop** -- run `agentfluent analyze --project agentfluent --since <fix-date>` to verify pm.md fixes worked
6. **Ship docs that reflect what shipped** -- README, GLOSSARY, CHANGELOG caught up (#287)

## 3. Non-Goals

- LLM-powered analysis (stays rule-based)
- Auto-applying recommended fixes
- Webapp dashboard
- Cross-project aggregation
- Tier 2 quality signals (local git correlation) -- stretch at best, not committed
- Tier 3 quality signals (GitHub enrichment) -- deferred to v0.7
- Negative recommendations ("remove this subagent") -- deferred per D020
- Markdown report export (#198) -- deferred to v0.7; depends on quality axis output stabilizing first
- Per-session diagnostics scope (#201) -- deferred to v0.7; benefits from quality axis integration

## 4. In Scope (Must-Include) -- 17 issues

### Epic 1: Quality Axis (Tier 1) -- #268

| # | Title | Effort | Why v0.6 |
|---|-------|--------|----------|
| #268 | Epic: Quality as a third axis (Tier 1) | -- | Container epic |
| #269 | Extract user mid-flight corrections + quality_signals module skeleton | S-M | Prerequisite for all quality work; creates module, enums, pipeline wiring |
| #270 | Detect file rework density | S | Strong quality proxy; data already in tool_use blocks |
| #271 | Measure reviewer-caught rate | M | Directly closes under-recommendation gap for review subagents |
| #272 | Multi-axis scoring in aggregation layer | M | Architectural heart; makes quality signals surface in priority ranking |
| #273 | CLI and JSON output: axis labels on recommendations | S-M | User-visible axis attribution on every recommendation |

### Epic 2: Date-Range Filtering -- #293

| # | Title | Effort | Why v0.6 |
|---|-------|--------|----------|
| #293 | Epic: Date/time-range filtering | -- | Container epic |
| #294 | Extract first-message timestamp during session discovery | S | Foundation for time filtering |
| #295 | Datetime parsing utility | S | Shared parser for ISO 8601, date-only, relative formats |
| #296 | Add --since/--until to `list --project` | S | Preview which sessions a window covers |
| #297 | Add --since/--until to `analyze` | S-M | Core feature; flag interactions, error handling |
| #298 | Add `window` metadata to analyze --json output | XS | Self-documenting JSON envelopes |
| #299 | Update CLI help text and epilog examples | XS | Docs the new flags |

### Bug fix

| # | Title | Effort | Why v0.6 |
|---|-------|--------|----------|
| #292 | pm Write hook conflicts with `memory: user` -- agent-memory writes silently blocked | XS | Already fixed; milestone validates it shipped |

### Docs

| # | Title | Effort | Why v0.6 |
|---|-------|--------|----------|
| #287 | docs: catch up README + GLOSSARY + CHANGELOG for v0.6 | M | Release requires docs parity. Content scoped to what actually ships. |

### CLI ergonomics

| # | Title | Effort | Why v0.6 |
|---|-------|--------|----------|
| #285 | Redesign Top-N priority fixes summary | S | Reduces repetition; natural companion to #273 axis attribution |

**Total in-scope: 17 issues (~22-26 dev days)**

## 5. Stretch Scope -- 5 issues

Pull in only if the must-include scope completes ahead of schedule. Ordered by value.

| # | Title | Effort | Why stretch |
|---|-------|--------|-------------|
| #274 | Quality signal calibration notebook + threshold tuning | M | Important for precision, but conservative defaults from #269-#272 are shippable. Calibration refines, it doesn't gate. |
| #275 | Local git feat-then-fix proximity signal (Tier 2) | M | Structurally enabled by Tier 1 module; new data source (git) introduces complexity |
| #281 | Bound ERROR_REGEX in error rate + error signal extraction | S | Follow-up polish from #241; improves signal precision |
| #265 | Consolidate CLI formatter test helpers | S | Tech debt; reduces maintenance burden but not user-facing |
| #264 | Capture tool_result.is_error per burst for cluster error_rate | S | Enriches offload candidate quality; not blocking |

## 6. Out of Scope / Deferred

### Deferred to v0.7

| # | Title | Rationale |
|---|-------|-----------|
| #198 | Markdown report export | Output format. Should incorporate quality-axis attribution and `diff` integration. Design benefits from v0.6 quality output stabilizing first. |
| #201 | Per-session diagnostics scope (`--scope session`) | Deep pipeline touch. Benefits from quality axis and date-range filtering settling. Natural v0.7 companion to `--since`. |
| #203 | Inline trace excerpts on critical findings | UX polish. Not blocking the quality story. |
| #204 | `list` table: cost column + JSON | Wiring task. Not on the critical path for quality or temporal features. |
| #263 | Recalibrate parent-thread offload thresholds (multi-contributor data) | Needs more diverse data. Will benefit from quality-axis calibration learnings. |
| #262 | Extract bursts at parse time to bound SessionAnalysis memory | Performance optimization. No user-visible change unless memory pressure observed. |
| #170 | Always suggest concrete target model in model-routing recommendations | Good improvement but independent of v0.6 themes. |
| #171 | Verify MCP audit rules fire in analyze output | Investigative; may be a data-clean non-issue. |
| #183 | Delegation drafts: skill-aware provenance | Requires novel skill-scanner infrastructure. v0.7+ per v0.4 scope review. |
| #275 | Local git feat-then-fix proximity (if not pulled from stretch) | New data source; only if Tier 1 is stable. |

### Explicitly NOT v0.6

| # | Title | Rationale |
|---|-------|-----------|
| #291 | pm prompt addendum (Read fallback + GitHub query guidance) | Labeled `chore`, `priority:low`, explicitly says "No release milestone" in the body. Maintenance task to be applied ad hoc. Not user-facing AgentFluent functionality. |

## 7. Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Quality signals produce too many false positives | Trust erosion -- users dismiss quality recommendations | Conservative thresholds (high correction counts, high rework counts). #274 (calibration) is stretch but conservative defaults are shippable. Run dogfood before release to verify signal:noise. |
| Pattern matching for corrections is too crude | Legitimate "no" responses counted as corrections | False-positive mitigation baked into #269 AC: corrections following assistant questions are excluded. Threshold-based: only fire when rate is high. |
| Date-range filtering reveals edge cases in session discovery | Sessions without timestamps, empty files | Filter treats `first_message_timestamp = None` as excluded; warning in verbose mode. |
| Quality axis + date-range combined scope is too large for 3-4 weeks | Delayed release | Quality axis has a clean cut point: if #272 (multi-axis scoring) runs long, ship #269-#271 (signals) and #273 (output) without #272 -- signals still flow through existing priority ranking. Date-range filtering has no internal dependencies on quality axis -- they're parallel streams. |
| Recommendation overload from third axis | Users see too many recommendations | Priority ranking (#172) already gates via top-N. Quality signals boost via the additive term, they don't multiply the total count. |
| #274 calibration stretch doesn't land | Thresholds may be imprecise | Ship with documented conservative defaults. Calibration can land in v0.6.1 hotfix if precision is a problem. |

## 8. Dependencies

```
[#269 user corrections + module skeleton] ──> [#270 file rework]
                                           └─> [#271 reviewer-caught]
                                           └─> [#272 multi-axis scoring]
                                                        │
                                                        v
                                              [#273 CLI/JSON output]
                                                        │
                                                        v (stretch)
                                              [#274 calibration notebook]
                                                        │
                                                        v (stretch)
                                              [#275 git feat-fix proximity]

[#294 timestamp extraction] ──> [#295 datetime parser]
                                        │
                                        ├──> [#296 list --since/--until]
                                        └──> [#297 analyze --since/--until]
                                                        │
                                                        v
                                              [#298 window metadata]
                                                        │
                                                        v
                                              [#299 CLI help text]

[#285 Top-N redesign] -- independent (benefits from #273 landing first)

[#287 docs] -- last (reflects what shipped)

[#292 pm hook fix] -- already fixed; no deps
```

The two epic streams (quality axis and date-range filtering) are **fully parallel** -- no cross-dependencies until docs (#287) at the end.

## 9. Success Criteria

v0.6 is successful when:

1. **Quality signals fire on real data.** Re-running `agentfluent analyze --project agentfluent --diagnostics` shows `USER_CORRECTION`, `FILE_REWORK`, or `REVIEWER_CAUGHT` signals where applicable. At minimum, sessions with known corrections produce the signal.
2. **Axis attribution appears on recommendations.** Every recommendation in CLI output shows `[cost]`, `[speed]`, or `[quality]` prefix. JSON output includes `axis_scores` and `primary_axis` fields.
3. **Review-subagent recommendations surface.** For sessions where architect/tester was used and caught issues, the reviewer-caught signal fires and contributes to recommendations. For sessions without review agents but with corrections/rework, a quality-axis recommendation appears suggesting an architect or tester.
4. **Date-range filtering works end-to-end.** `agentfluent analyze --project P --since 2026-05-04` produces output scoped to post-date sessions only. `list --project P --since 7d` shows only recent sessions.
5. **The dogfooding loop is closed.** After the pm.md fixes (#291, #292), `analyze --since <fix-date>` shows reduced `retry_loop` / `tool_error_sequence` signals for pm without dilution from historical sessions.
6. **`diff` composes with temporal filtering.** Generating time-windowed baselines and comparing them via `diff` produces meaningful deltas.
7. **All new code has >80% test coverage.** No regressions.
8. **Docs reflect what shipped.** README, GLOSSARY, CHANGELOG all updated.

## 10. Release Checklist

- [ ] #269 merged: `quality_signals.py` module, `Axis` enum, `SignalType` additions, `USER_CORRECTION` detection
- [ ] #270 merged: `FILE_REWORK` detection
- [ ] #271 merged: `REVIEWER_CAUGHT` rate measurement
- [ ] #272 merged: multi-axis scoring, `axis_scores`, `primary_axis` on recommendations
- [ ] #273 merged: CLI/JSON axis attribution in output
- [ ] #294 merged: `first_message_timestamp` on `SessionInfo`
- [ ] #295 merged: datetime parsing utility
- [ ] #296 merged: `--since`/`--until` on `list`
- [ ] #297 merged: `--since`/`--until` on `analyze`
- [ ] #298 merged: `window` metadata in JSON output
- [ ] #299 merged: CLI help text + examples
- [ ] #285 merged: Top-N summary redesigned
- [ ] #292 merged: pm hook fix (already done)
- [ ] #287 merged: docs catch-up
- [ ] Dogfood run validates quality signals against real sessions
- [ ] Dogfood run validates `--since` scoping against post-fix sessions
- [ ] `uv run pytest --cov=agentfluent` passes with >80% coverage
- [ ] `uv run ruff check src/` clean
- [ ] `uv run mypy src/agentfluent/` clean
- [ ] CHANGELOG updated via release-please
- [ ] Version bump to 0.6.0
