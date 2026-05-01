# PRD: AgentFluent v0.5 -- Trustworthy Diagnostics

**Status:** Draft
**Date:** 2026-04-30
**Author:** PM Agent
**Decision log:** See `decisions.md` for key decisions referenced below.
**Backlog:** See `backlog-v0.5.md` for the full sequenced backlog.

---

## 1. Theme

**"Trustworthy Diagnostics"** -- make agentfluent's signals reliable enough that a user who doesn't know the internals can act on them without second-guessing.

v0.3 built deep diagnostics. v0.4 polished the CLI surface. v0.5 addresses the gap the dogfood run exposed: **the diagnostics engine produces signals that mislead when fed unclean data.** The `pm` agent's 999s/call duration outlier -- a measurement artifact from user-input wait time, not a performance problem -- is the poster child. A user who saw that recommendation and downgraded their pm model would have been worse off. That is a trust failure, and trust failures compound.

This release fixes the inputs (wait-time exclusion), fixes the math (outlier recalibration), and adds the output surface that lets users verify improvements over time (priority ranking, comparison workflow). It also includes the largest-scope diagnostics feature yet -- parent-thread offload candidates -- which depends on trustworthy duration and token metrics to produce credible savings estimates.

One-line pitch: **"The diagnostics you can trust, and the comparison workflow that proves it."**

### Why this theme over "diagnostics + comparison workflow"

`diff` (#199) and `report` (#198) are valuable, but they're output formats. If the underlying signals are unreliable (#230, #186), comparing two unreliable snapshots produces unreliable deltas. The v0.4 scope review explicitly noted that #199 "needs design: storage model, CI semantics, schema stability." Fixing signal reliability first means `diff` launches on a stable foundation rather than shipping and immediately needing recalibration.

`diff` is included in v0.5 scope, but it ships *after* the data-fidelity work, not as the headline.

## 2. Goals

1. **Eliminate false-positive duration outliers** by detecting and excluding user-input wait time from duration metrics (#230)
2. **Recalibrate outlier detection** against real distributions -- replace naive mean-ratio with a distribution-appropriate method (#186)
3. **Surface parent-thread offload candidates** -- the dominant cost lever for agent portfolio optimization (#189)
4. **Add priority ranking** to recommendations so users know what to fix first (#172)
5. **Ship `agentfluent diff`** to compare analyze runs over time (#199)
6. **Improve delegation draft quality** -- minimal tools list and unified model classifier (#184, #185)

## 3. Non-Goals

- Webapp dashboard (deferred per delivery strategy)
- LLM-powered analysis (stays rule-based)
- Auto-applying recommended fixes
- Cross-project aggregation
- Markdown report export (#198) -- deferred to v0.6; design should follow `diff` rather than precede it
- Per-session diagnostics scope (#201) -- deferred; depends on `diff` and priority ranking settling first

## 4. Scope

### In Scope (Must-Include) -- 9 issues

| # | Title | Why v0.5 |
|---|-------|----------|
| #230 | Detect/exclude user-input wait time from duration metrics | Data-quality gate. Blocks trustworthy duration_outlier signals. Dogfood-validated. |
| #186 | Recalibrate outlier detection against real distributions | Data-quality gate. Composes with #230: #230 fixes the input, #186 fixes the math. |
| #187 | Surface distribution context in verbose output | Display half of #186. Lands naturally alongside it. |
| #189 | Parent-thread offload candidates | Only `priority:high` issue. Headline diagnostics feature. Depends on trustworthy metrics from #230+#186. |
| #172 | Priority ranking + top-N summary | Transforms the recommendations table from "list of warnings" to "action plan." Required for `diff` to produce meaningful "new/resolved" comparisons. |
| #199 | `agentfluent diff` | Comparison workflow. The feature that makes agentfluent habit-forming. Benefits from all upstream fixes. |
| #184 | Delegation drafts: minimal tools list | Fixes over-broad tool recommendations. XS-S effort. |
| #185 | Delegation drafts: unified model classifier | Fixes inconsistent model advice between delegation and model-routing. S effort. Depends on #184. |
| #215 | Explain why delegation_suggestions is empty | XS fix. Improves JSON output self-documentation. |

### Stretch Scope -- 2 issues

| # | Title | Why stretch |
|---|-------|-------------|
| #227 | Extend Cost by Model to include subagent tokens | Wiring + presentation; infrastructure exists. Enhances the cost story but not diagnostics trust. |
| #205 | `--severity` / `--min-severity` filter | Natural companion to #172 (priority ranking). XS effort. Pull in if time allows. |

### Deferred to v0.6 -- 5 issues

| # | Title | Why deferred |
|---|-------|--------------|
| #198 | Markdown report export | Output format, not data quality. Design should follow `diff`. |
| #201 | Per-session diagnostics scope | M effort, deep pipeline touch. Better after #172 and #199 settle. |
| #203 | Inline trace excerpts on critical findings | Nice UX polish. Not blocking trust or comparison workflow. |
| #204 | `list` table: cost column + JSON | Requires wiring cost into the list path. Not on the critical path. |
| #208 | README note on `[clustering]` extra | Docs-only. Can land anytime. The JSON side (#215) is in scope. |

## 5. Decisions and Tradeoffs

### The milestone is internally inconsistent -- this plan resolves it

The v0.5.0 milestone contained 16 issues from five distinct origins: dogfood-surfaced data-quality fixes (#230, #186), a v0.4-deferred `priority:high` diagnostics feature (#189), v0.4 review-polish leftovers (#198, #199, #201, #203, #204, #205, #208), delegation-quality improvements (#184, #185), and miscellaneous (#172, #187, #215, #227).

The inconsistency: #189 is labeled `priority:high` and is epic-scale (multi-PR). Meanwhile, 7 of the remaining issues are leftover v0.4 polish items that were explicitly deferred because v0.4 was overstuffed. Keeping all 16 would recreate the exact v0.4 over-scope problem the v0.4 scope review diagnosed.

Resolution: 10 in-scope (coherent "trustworthy diagnostics" theme; includes #231 filed during sign-off), 2 stretch, 5 deferred. The deferred items are all output-format or polish work that does not affect diagnostic correctness.

### #230 and #186 must land before #189

The parent-thread offload feature (#189) produces dollar-value savings estimates from duration and token metrics. If those metrics include user-wait-time artifacts, the savings estimates are inflated and misleading. #230 (wait-time exclusion) and #186 (outlier recalibration) are prerequisites, not peers, of #189. This is the critical path.

### #189 should not gate the release but should ship mid-cycle

#189 is the largest item (~5-7 days). If it encounters unexpected complexity, it should not delay the data-fidelity fixes or `diff`. Sequencing: ship #230 + #186 + #187 first (data fidelity), then #189 (new diagnostics layer), then #172 + #199 (output improvements), then #184 + #185 + #215 (delegation quality). If #189 runs long, cut it to stretch and release with the data-fidelity + comparison story.

### `diff` (#199) needs PM design decisions before implementation

The issue body itself lists three open questions: storage model, scope (diagnostics only or also token_metrics), CI exit codes. These need answers:

- **Storage:** User manages baselines. `agentfluent analyze --json > baseline.json`, then `agentfluent diff baseline.json current.json`. No internal caching for v0.5 -- explicit is better than magic.
- **Scope:** Both diagnostics AND token_metrics. The reviewer's "general-purpose retry_loop count went from 34 to 12" example spans both.
- **CI exit codes:** Yes. Exit 1 if regressions detected (new critical/warning signals). Exit 0 if no regressions. `--fail-on` flag to control threshold.

### `report` (#198) deferred despite reviewer ranking it P1

The reviewer called both `diff` and `report` P1. They serve different use cases: `diff` enables the iterative improvement loop (habit-forming); `report` enables sharing (one-shot export). The iterative loop is more strategically important for product stickiness. `report` should follow `diff` in design so the Markdown format can include delta information. Shipping `report` in v0.5 without `diff` integration would produce a format that needs redesigning in v0.6.

### #208 partially resolved, remainder deferred

#208 had two parts: README docs (shipped in PR #210) and JSON output explanation (#215 split out). #215 (the JSON part) is in scope; the original #208 can be closed if the README part already shipped. Verify at implementation time.

## 6. What's Missing (Not Yet Tracked)

### Hook-induced `permission_failure` noise filtering — #231

The dogfood analysis explicitly flagged this: "Most 'blocked' hits come from the secret-blocking PreToolUse hook. That's intended behavior, not a real failure. Worth filtering or annotating in a future agentfluent release."

This was called out as a "possible v0.5 issue" but was never filed at the time. It is a data-quality issue in the same category as #230 (measurement artifact producing false signals). The scope is narrow: recognize `permission_failure` signals that originate from known hook patterns (e.g., `block_secret_reads.py` blocking Read/Grep on credential-shaped files) and either:
- Downgrade severity from critical to info, OR
- Annotate with `source: "hook"` so users can filter

**Status:** Filed as **#231** during v0.5 planning sign-off (2026-04-30); included in Wave 1 alongside #230 and #186. S effort, directly serves the "trustworthy diagnostics" theme.

### `tester` agent observation window

The dogfood analysis planned: ship `tester` first, observe for ~1 week, then assess. `tester` shipped in #228 (2026-04-29). The observation window runs through ~2026-05-06. A second dogfood run at that point would:
- Validate whether `general-purpose` invocation counts dropped
- Check `tester` retry/error rates
- Potentially trigger `model_mismatch: overspec` signal for Haiku downgrade

This is not a product issue -- it is a process input. Flag it here so the v0.5 development cadence accounts for it: the second dogfood run should happen before v0.5 ships to validate the data-fidelity fixes.

### Model-routing calibration follow-up

The dogfood analysis noted: "The complexity classifier currently classifies every observed agent as 'complex' on v0.3 single-dataset calibration." #186's recalibration work should include a check on whether the model-routing thresholds also need adjustment. Not a separate issue -- it is in #186's scope (the notebook analysis covers all threshold-dependent signals).

## 7. Dependencies

```
[#230 wait-time exclusion] ──┐
                              ├──> [#189 parent-thread offload]
[#186 outlier recalibration] ─┘         │
        │                               │
        v                               v
[#187 distribution context]    [#172 priority ranking]
                                        │
                                        v
                               [#199 agentfluent diff]

[#185 unified model classifier] ──> [#184 minimal tools list]
                                        │
                                        v
                               [#215 empty delegation reason]
```

- #230 and #186 are independent of each other but both must land before #189
- #187 depends on #186 (consumes the distribution stats #186 produces)
- #189 depends on #230 + #186 for trustworthy metrics
- #172 is independent but should land before #199 (priority scores enable meaningful diff comparisons)
- #199 depends on #172 (for "new/resolved" recommendation comparison by priority)
- #185 should land before #184 (unified classifier feeds the tools derivation)
- #215 is independent

## 8. Success Criteria

v0.5 is successful when:

1. **The pm 999s/call artifact is resolved.** Re-running `agentfluent analyze --project agentfluent` shows `pm` with `active_duration` in the expected range (minutes, not 16 minutes). The `duration_outlier` signal either disappears or fires on genuinely slow invocations only.
2. **Outlier signals cite distribution context.** Under `--verbose`, signals report z-score/percentile alongside ratio-to-mean. The method (IQR, percentile, or whatever #186 settles on) is grounded in observed distributions, not arbitrary thresholds.
3. **Parent-thread offload candidates appear.** `agentfluent analyze --diagnostics` includes an "Offload Candidates" section when repeated parent-thread patterns are detected. At least one cluster appears in the agentfluent project's own data.
4. **Recommendations are ranked.** The top-N summary block appears above the full recommendations table. Priority scores reflect severity, occurrence count, cost impact, and evidence strength.
5. **`agentfluent diff` works.** `agentfluent diff baseline.json current.json` produces new/resolved/persisting recommendation comparisons, count deltas per signal type, and cost deltas. Exit code 1 on regressions.
6. **Delegation drafts are tighter.** YAML drafts use frequency-filtered tools (not union) and the unified complexity classifier for model selection.
7. **All new code has >80% test coverage.** No regressions in existing tests.

## 9. Release Checklist

- [ ] #230 merged: `active_duration_ms` and `wait_duration_ms` on invocation metadata; `duration_outlier` uses `active_duration_ms`
- [ ] #186 merged: outlier detection uses distribution-appropriate method; `detail` dict carries distribution stats
- [ ] #187 merged: verbose output surfaces distribution context
- [ ] #189 merged: parent-thread offload candidates in `analyze --diagnostics` output
- [ ] #172 merged: priority ranking + top-N summary on recommendations
- [ ] #199 merged: `agentfluent diff` subcommand functional
- [ ] #184 merged: frequency-based tool filtering on delegation drafts
- [ ] #185 merged: unified complexity classifier for delegation model selection
- [ ] #215 merged: `delegation_suggestions_skipped_reason` in JSON output
- [ ] Second dogfood run validates data-fidelity fixes against real sessions
- [ ] `uv run pytest --cov=agentfluent` passes with >80% coverage
- [ ] `uv run ruff check src/` clean
- [ ] `uv run mypy src/agentfluent/` clean
- [ ] CHANGELOG updated via release-please
- [ ] Version bump to 0.5.0

## 10. Risks

| Risk | Impact | Mitigation |
|------|--------|------------|
| #230 wait-time heuristic is unreliable | Duration metrics remain untrustworthy; #189 savings estimates mislead | Start with AskUserQuestion-anchored detection (high confidence); idle-gap heuristic as fallback. Validate against dogfood data before shipping. |
| #186 recalibration changes signal volume dramatically | Users see far fewer or far more signals than before | Run calibration notebook against real data; compare "before/after" signal counts; include signal-count comparison in the PR description. |
| #189 is larger than estimated | Delays the release | Sequence it after data-fidelity work. If it runs long, defer to v0.6 and release with the data-fidelity + comparison story. |
| `diff` design decisions are wrong | Users find the comparison UX confusing | Start with explicit file-pair comparison (no magic caching). Iterate in v0.6 based on usage. |
| Stretch items don't fit | Release feels incomplete | The 10 must-include items tell a complete story without stretch. #227 and #205 are genuine nice-to-haves. |
