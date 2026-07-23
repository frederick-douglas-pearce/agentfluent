# PRD: AgentFluent v0.12 -- Cost Correctness, the Quantity Half

**Status:** Proposed 2026-07-22 (scope recommendation; human disposes)
**Date:** 2026-07-22
**Author:** PM Agent
**Governing decision:** [D057](decisions.md) (the `totalTokens` defect is a deterministic 100% rule, ~15x silent understatement; `#646` becomes the v0.12 headline; `#648` split out; v0.12 corrects the *quantity* half while v0.11 corrected the *rate* half). [D056](decisions.md) (aggregation convention (A) -- `totalTokens` exclusive of children -- stands). [D058](decisions.md) (dogfood milestoning: shippable-artifact test; re-measurement floats on the label -- excludes #513/#469/#558/#600/#601/#637 from this milestone). [D045](decisions.md) (genai-prices base + local overlay). [D054](decisions.md) (`COST_MODEL.md` caveat routing; tier gap split to `#639`). [D042](decisions.md) (hold scope at the epic). [D029](decisions.md) (released field -> no rename). [D059](decisions.md) (this release's scoping call).
**Upstream context:** v0.11 shipped the pricing *foundation* (genai-prices base⊕overlay seam #547, date-aware plumbing #546, #545) and, per D057(11), an explicit **cost caveat** (`docs/COST_MODEL.md` + README/GLOSSARY + CHANGELOG "Known limitations", via #549) stating the composite `estimated_total_cost_usd` is no more accurate than v0.10. This PRD is the release that *retires* that caveat.

---

## 1. Theme

**"Cost correctness -- the quantity half."**

v0.11 got the **price per token** right (the genai-prices base⊕overlay seam) but had to ship a caveat admitting the composite cost number was no better than v0.10, because the **token count** feeding it is wrong. D057 established why: `toolUseResult.totalTokens` is a deterministic single-turn context-size proxy, not cumulative spend, so per-agent cost is understated **~15x** (six heaviest sessions: $6.94 published vs $105.63 true), and a *separate* link-coverage defect silently drops **30.7%** of all subagent processed tokens before they ever reach `session total_cost`.

v0.12 closes that gap. It corrects the quantity (`#646` field semantics + the cache-diluted `blended_rate`), recovers the dropped tokens (`#648` link coverage + explicit disclosure of what stays unrecoverable), completes the multi-level linker that makes nested spend attributable (`#595` PR B), and -- on the *rate* side -- **completes cost-lever coverage** by landing all four remaining Phase 2 overlay levers (`#536`/`#537`/`#538`/`#539`) on the seam v0.11 built, closing epic `#535`.

One-line pitch: **"v0.11 priced each token right; v0.12 counts the tokens right -- so `estimated_total_cost_usd` finally means what it says, and every Claude cost lever is either priced or explicitly disclosed."**

### Why this theme, why now

D057(10) named `#646` the headline of v0.12.0 explicitly: v0.11 deliberately shipped the rate half alone so the ~15x quantity movement could be validated independently against the Usage & Cost Admin API rather than tangled into a composite delta. The enabling linker (`#595`) landed only PR A in v0.11 (PR B open); the orphan cohort's root cause was not yet decomposed; the fix was undesigned. Those blockers are now the v0.12 work. Shipping the rate-lever completion (`#535` Phase 2) in the same release is coherent, not a second marquee: it is the *rate* half of the same "cost correctness" story, sitting on the already-shipped #547 seam, and it retires an epic rather than opening one.

## 2. Goals

1. **Correct the quantity (`#646`, headline).** Replace the `totalTokens` single-turn proxy with real per-agent spend computed from per-turn `usage`, and correct the cache-diluted `blended_rate` (D057(7): the cost error 15.2x exceeds the token error 5.8x precisely because both compound -- any fix must correct the rate as well as the quantity). Re-document `total_tokens` as a context-size proxy (no rename -- D029).
2. **Recover the dropped tokens (`#648`).** Decompose the 30.7% into depth-≥2 (attributable once `#595` PR B lands) vs orphaned/rotated parents (a disclosure problem); attribute the fixable cohort; surface an explicit coverage count for the residual instead of a silent debug log.
3. **Complete the multi-level linker (`#595` PR B).** `parent_invocation_id` + `depth` on `SubagentTrace`, depth-≥2 probe, cycle guard -- the join that makes `#648`'s depth-≥2 cohort attributable.
4. **Complete cost-lever coverage (`#535` Phase 2).** Land all four remaining overlay levers on the #547 seam: fast mode (`#536`), server-side tool surcharges (`#539`), batch/priority service tier (`#537`), data residency (`#538`). Close epic `#535`.
5. **Land the pricing-adjacent items unblocked by Phase 1** (`#543` diff pricing-version stamp, `#82` genai-prices pin currency + upstream-coverage detection).
6. **Hold the line on hygiene/infra** (`#649` streaming-snapshot dedup regression lock, `#652` demo-svg path scrub, `#653` uv.lock self-version sync).
7. **Ship catch-up docs (`#548`) LAST**, updating the v0.11 caveat once #646/#648 land and adding the v0.12.0 runtime cost caveat (D057(11): the CLI/JSON runtime caveat was deliberately deferred from v0.11 to v0.12).

## 3. Non-Goals

- **The >200K long-context tier fix (`#639`).** A distinct, real present-day mis-pricing (D054), but not on this milestone -- milestone assignment is a maintainer call and it is not part of the quantity/lever-completion story. Do not pull it in silently.
- **Dogfood re-measurement / validation issues (D058).** `#513`, `#469`, `#558`, `#600`, `#601`, `#637` float on the `dogfood` label with no milestone -- their gate (corpus-window availability) is orthogonal to shipping code and they cannot be a version's blocker. `#646`/`#648` are dogfood-*surfaced bugs* (they carry `bug`, ship a fix a `pip install` user sees) and correctly stay.
- **Admin API reconciliation as an automated feature (D057(15)).** The one-time manual validation of the corrected spend against the Usage & Cost Admin API is recorded in the `#646` issue and `docs/TOKEN_ACCOUNTING.md`; it is **not** a shipped feature and **not** an AC -- org-scoped credentials + outbound egress conflict with the local-first posture.
- **A `total_tokens` rename (D029).** The field has shipped; corrected spend lands *beside* it and it is re-documented, not renamed.
- **CodeFluent's independent copy of the `#648` defect.** Measured at 30.2% on the same corpus; filed and fixed in that repo, not here.
- LLM-powered analysis (D035). Auto-applying fixes (D002). Webapp dashboard, cross-project aggregation.

## 4. In Scope

Four workstreams. WS1 (quantity) is the headline and carries the analytical weight and risk; WS2 (rate levers) is a batch of small, parallel overlay multipliers on the already-shipped #547 seam; WS3--WS5 are adjacent, hygiene, and docs.

### Workstream 1 -- Cost correctness: the quantity half (HEADLINE)

| # | Title | Type | Commit scope | Deps |
|---|-------|------|--------------|------|
| #595 | Multi-level trace-to-invocation linker, PR B (`parent_invocation_id` + `depth` on `SubagentTrace`; depth-≥2 probe; cycle guard) | feat | `feat:` (src/) | PR A (shipped v0.11); **enables #648 AC2** |
| #646 | `totalTokens` is a final-turn proxy -> compute real per-agent spend from per-turn `usage`; correct the cache-diluted `blended_rate`; re-document `total_tokens` | fix | `fix:` (src/) | linked traces (PR A shipped); coordinate on `analytics/pipeline.py` with #595/#648 |
| #648 | Orphan + depth-≥2 traces silently dropped (30.7% of subagent tokens): decompose, attribute the depth-≥2 cohort, disclose the residual coverage | fix | `fix:` (src/) | **AC2 depends on #595 PR B**; AC1 (decompose) can start independently |

**Notes.**
- **`#646` is the headline correction and must fix both quantity and rate** (D057(7)). Computing spend from per-turn `usage` alone, while leaving `blended_rate` diluting cache reads at full token weight, would leave the composite still wrong.
- **`#648` AC1 gates the rest** (D057(12)): decompose the 30.7% into depth-≥2 (fixable by #595 PR B) vs orphaned/rotated parents (a coverage-*disclosure* deliverable, likely not fixable). Until the split exists the fix cannot be sized and the residual to disclose is unknown. AC3/AC4: report coverage explicitly; a debug-log line is not adequate for a 30% loss.
- **Merge-ordering, not hard blocks:** #595 PR B, #646, and #648 all touch the cost-aggregation path (`analytics/pipeline.py`). The only *hard* dependency is #648 AC2 -> #595 PR B. Sequence #595 PR B first to avoid three-way churn on the same file.
- **`#646` ↔ `#648` joint decision (architect review, 2026-07-23):** the two must *jointly* decide where depth-≥2 subagent spend lands in `by_agent_type` — architect leans **session-total-only + a coverage note** for v0.12, deferring per-type depth-≥2 attribution. Both must preserve the reconciliation invariant **`Σ subagent by_agent_type cost == subagent-origin token_metrics cost`**, asserted as a hard test — this same invariant is the internal cross-check that substitutes for the Admin-API reconciliation blocked per D059. Scope note: #646 is a per-agent-*attribution* fix confined to `agent_metrics.py` (session `total_cost` is already correct for depth-1 via `fold_subagent_metrics_in`); `blended_rate` is *replaced* by per-model trace pricing for linked invocations, not "corrected." #648 is the top-line fix (widens what reaches the fold gate, drop site `pipeline.py:257`).
- **`#595` PR B plumbing gap (architect review):** the depth-≥2 probe must read `.meta.json` sidecars to preserve lazy-parse; `discovery.py` globs `agent-*.jsonl` only today, so the sidecar read is the main plumbing risk. #595 PR B plugs in at `_link_subagent_traces` (`pipeline.py:485`) and exposes a reusable sidecar emitter index #648 AC2 consumes. New `parent_invocation_id`/`depth` on `SubagentTrace` are additive public JSON API (D055) — name deliberately; envelope stays "2" (D029).

### Workstream 2 -- Complete cost-lever coverage: the rate half (epic `#535` Phase 2)

Phase 1 (`#545`/`#546`/`#547`) shipped in v0.11, so the base⊕overlay seam #547 exists and **all four levers are unblocked and mutually independent** -- each is a small overlay multiplier keyed off a `usage.*` field, parallelizable. **ALL FOUR RIDE v0.12** (see §7).

| # | Title | Live impact (per epic) | Commit scope | Deps |
|---|-------|------------------------|--------------|------|
| #536 | price fast mode (`usage.speed`) premium rates | High if used | `feat(pricing):` | #547 seam (shipped) |
| #539 | server-side tool surcharges (web search $10/1k, code exec $/hr; `usage.server_tool_use`) | Medium if used | `feat(pricing):` | #547 seam (shipped) |
| #537 | batch / priority `service_tier` pricing | Medium | `feat(pricing):` | #547 seam (shipped) |
| #538 | data-residency (`inference_geo=us`) 1.1x multiplier | Low--Med | `feat(pricing):` | #547 seam (shipped) |
| #535 | epic: complete Claude cost-lever coverage (tracking) | -- | -- | closes when the four rows + DoD land |

**Epic `#535` DoD reminder:** every Phase 2 gap row is *either implemented in the overlay or explicitly documented as a limitation*, and `docs/COST_MODEL.md` stays in sync as levers are covered. Landing all four satisfies the "implemented" branch and closes the epic. The consolidated upstream genai-prices request (epic DoD) is the maintainer-facing loose end; file/link it as part of closing #535.

### Workstream 3 -- Pricing-adjacent (unblocked by Phase 1)

| # | Title | Type | Commit scope | Deps |
|---|-------|------|--------------|------|
| #543 | `diff`: stamp pricing-model version into snapshots to flag cross-version cost deltas | feat | `feat:` (src/) | Phase 1 shipped; independent of WS1/WS2 |
| #82 | keep the genai-prices pin current + detect upstream coverage of overlay levers (GH Actions scheduled) | chore | `chore(ci):` (pin bump to `pyproject.toml` runtime dep is the one `feat`/`fix` nuance) | Phase 1 shipped; independent |

**Note on `#543`:** its value rises once the overlay levers land (a snapshot taken under different lever coverage is a genuine cross-version cost delta), so sequence it *after* WS2 lands if contention forces a choice -- but it has no hard dependency on WS2.

### Workstream 4 -- Hygiene / infra

| # | Title | Type | Commit scope | Deps |
|---|-------|------|--------------|------|
| #649 | lock streaming-snapshot `message.id` dedup against regression + document the 1.99x trap in CLAUDE.md | test | `test:` (+ `docs:` CLAUDE.md) | independent; relates D057(9) |
| #652 | scrub real home-dir path from demo-diff.svg screenshot generation | chore | `chore(docs):` | independent |
| #653 | keep uv.lock self-version in sync inside the release PR (follow-up to manual #654 sync) | chore | `chore(ci):` | independent; feeds the release cut |

### Workstream 5 -- Docs (LAST, feeds the release cut)

| # | Title | Type | Commit scope | Deps |
|---|-------|------|--------------|------|
| #548 | catch up README + GLOSSARY + CHANGELOG for v0.12.0 | docs | `docs:` | **#646 + #648 landed** (updates the v0.11 caveat #549 shipped); reflects WS2 lever coverage |

**Critical docs delta:** v0.11 shipped a cost caveat (D057(11)) stating the composite cost is no more accurate than v0.10. Once #646/#648 land, #548 must **update** that caveat (`COST_MODEL.md`, README, GLOSSARY, CHANGELOG) to reflect the corrected quantity, and add the **v0.12.0 CLI/JSON runtime cost caveat** D057(11) deferred from v0.11 (state the corrected value and its coverage). #548 rides the loop's **`docs` graduated route** (D047).

## 5. Sizing Sanity Check

| Release | Issues |
|---------|--------|
| v0.9 | 18 |
| v0.10 | ~15 |
| v0.11 | ~20 |
| **v0.12** | **14** (3 quantity + 4 levers + 1 epic-tracking + 2 pricing-adjacent + 3 hygiene + 1 docs) |

Mid-band and comfortably shippable. The quantity workstream (WS1) is where the difficulty concentrates -- analytically hard, cross-cutting the cost path, and the release's whole reason to exist. WS2's four levers are small, independent, and additive; they *reduce* release risk relative to their count. Deferring the low-impact levers would shrink the release below the band **and** leave epic `#535` dangling across a release boundary for a trivial multiplier -- so the sizing argument actively favors keeping all four (see §7).

## 6. Dependency graph (run-init enumerates this against the live milestone)

Grouped by workstream. The loop's run-init should enumerate these rows against milestone v0.12.0. **Only two hard blocks exist; everything else is parallel.**

```
WS1 -- QUANTITY (headline)
  1. #595 (PR B: parent_invocation_id + depth; depth-≥2 probe; cycle guard)  -- BUILD FIRST in WS1
  2. #646 (spend semantics + blended_rate correction)                        -- uses PR A (shipped); merge-coordinate with #595/#648 on analytics/pipeline.py
  3. #648 (decompose 30.7% -> attribute depth-≥2 + disclose residual)        -- AC1 independent; AC2 HARD-BLOCKED by #595 PR B

WS2 -- RATE LEVERS (epic #535 Phase 2; all on the shipped #547 seam)
  4. #536  }
  5. #539  }  independent, parallel, no WS1 dependency
  6. #537  }
  7. #538  }
  (#535 epic-tracking closes when 4-7 land + DoD)

WS3 -- PRICING-ADJACENT (Phase 1 shipped -> unblocked)
  8. #543 (diff pricing-version stamp)   -- value rises after WS2; no hard block
  9. #82  (genai-prices pin currency + coverage detection)

WS4 -- HYGIENE / INFRA (independent, parallel)
  10. #649 (streaming-snapshot dedup regression + CLAUDE.md trap doc)
  11. #652 (demo-diff.svg path scrub)
  12. #653 (uv.lock self-version sync in release PR)

WS5 -- DOCS (LAST)
  13. #548 (README + GLOSSARY + CHANGELOG catch-up)  -- HARD-BLOCKED by #646 + #648; docs graduated route (D047)
```

**Hard blocks (the only two the loop must respect):**
- `#648` (AC2) <- `#595` PR B
- `#548` <- `#646` **and** `#648`

**Soft/merge-ordering (advisory, not blocks):** within WS1 sequence `#595` PR B -> `#646` -> `#648` to avoid three-way churn on `analytics/pipeline.py`; sequence `#543` after WS2 if contention forces a choice.

**Everything else parallelizable.** WS2/WS3/WS4 have no cross-workstream dependency and can run alongside WS1 from day one.

## 7. Scope call: do all four Phase 2 levers ride v0.12? -- YES, all four (recommended)

**Recommendation: ship all four -- `#536`, `#537`, `#538`, `#539` -- in v0.12.** Decisive, not a menu.

**Reasoning:**
1. **They are small and unblocked.** Phase 1 shipped the #547 base⊕overlay seam. Each lever is a single overlay multiplier keyed off a `usage.*` field -- no cross-module change, independently testable, mutually independent. The marginal cost of the fourth lever is low.
2. **The epic's own bar is completeness, and the release theme *is* cost correctness.** `#535`'s stated bar: "for every input that changes a Claude request's dollar cost, we either price it correctly or document explicitly why we can't." The DoD is satisfiable two ways -- *implement* or *document a limitation*. For a 1.1x residency multiplier or a `service_tier` switch, **implementing is cheaper than writing the "why we didn't" limitation prose** and then carrying the epic open. Deferring the low-impact levers buys nothing and costs the epic's closure.
3. **"$0 in our corpus" is explicitly rejected as the scheduling signal.** The epic bars corpus-impact as the criterion: AgentFluent is a published PyPI tool; fast mode / web search / batch / US residency are real dollars for *some* user even at $0 in Fred's dogfood corpus. (This is the same principle as D058: provenance/impact-in-our-corpus is not a scheduling signal.)
4. **No marquee dilution.** The concern behind "keep it focused" is protecting the headline -- but the headline is unambiguously the *quantity* correction (WS1), and D057(10) already named it. Four small rate-overlay stories riding alongside do not compete with it; they complete the *other* half of the same cost-correctness story.
5. **The sizing argument runs the *same* direction.** At 14 issues the release is mid-band. Deferring `#538`/`#537` would drop it below band **and** leave epic `#535` open into v0.13 for a trivial multiplier -- pure churn across a release boundary. Closing the epic is worth more than a marginally smaller release.

**Rejected alternatives:**
- *Defer `#538` (residency) and/or `#537` (batch) to v0.13.* Rejected: leaves `#535` open for a 1.1x multiplier and a tier switch; the deferral prose costs more than the implementation; no focus benefit because the headline is elsewhere.
- *Defer the whole Phase 2 batch, ship a quantity-only v0.12.* Rejected: the rate levers sit idle on a seam already built and paid for in v0.11; shipping them now is the cheapest they will ever be, and it retires the epic.

## 8. Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| WS1 three-way churn on `analytics/pipeline.py` (#595/#646/#648 all touch the cost path) | Merge conflicts, rework | Sequence #595 PR B -> #646 -> #648 (§6 soft-ordering); #648 AC1 (decompose) is analysis-only and can start in parallel |
| The ~15x quantity movement is unattributable if bundled with a rate change | Cannot validate against Admin API | D057(10) already isolated the rate half in v0.11; v0.12's WS2 levers are $0/near-$0 in our corpus, so the *dogfood* composite delta is dominated by the #646/#648 quantity fix and stays attributable. Keep the Admin-API reconciliation (D057(15)) a one-time manual check recorded on #646 |
| `#648` orphan cohort never fully resolves | A residual % stays unattributable | By design a *disclosure* deliverable (AC3/AC4): report coverage explicitly rather than silently drop; do not blend attributed + unattributed |
| v0.11 cost caveat left stale after #646/#648 land | Docs contradict shipped behavior | #548 hard-blocked on #646+#648; explicit AC to update the caveat + add the deferred runtime caveat (§4 WS5) |
| A lever multiplier double-applies a factor already in the token counts | Over-statement | `COST_MODEL.md` Sections E--G already flag which effects are in counts (thinking/tool tokens) and must NOT be applied as multipliers; each lever PR checks against that catalog |
| `#82` pin bump crosses the chore/feat boundary | Mis-scoped commit / spurious version bump | Pin currency is `chore(ci)`; only a genai-prices bump that changes *shipped* rates is `fix`/`feat` -- decide per bump against the CLAUDE.md scope convention |

## 9. Success Criteria

1. `#646`: per-agent spend computed from per-turn `usage`; `blended_rate` cache-dilution corrected; `total_tokens` re-documented as a context-size proxy (no rename); corrected composite validated once against the Admin API (manual, recorded).
2. `#648`: 30.7% decomposed into depth-≥2 vs orphan; depth-≥2 attributed via #595 PR B; residual coverage surfaced as an explicit count in the analysis result (not a debug log).
3. `#595` PR B: `parent_invocation_id` + `depth` on `SubagentTrace`; depth-≥2 probe; cycle guard; fixture-locked against `nested_session/`.
4. `#535` Phase 2: all four overlay levers (#536/#537/#538/#539) implemented and tested; `docs/COST_MODEL.md` updated; consolidated upstream request filed/linked; epic closed.
5. `#543` diff pricing-version stamp; `#82` pin-currency + coverage detection land.
6. `#649` dedup regression test + CLAUDE.md trap doc; `#652` svg path scrubbed; `#653` uv.lock self-version sync land.
7. `#548`: docs reflect corrected quantity; v0.11 caveat updated; v0.12.0 runtime cost caveat added.
8. All new production code >80% coverage; no regressions; `ruff` clean; `mypy` clean; version bump to 0.12.0.

## 10. Release Checklist

- [ ] #595 PR B merged: multi-level linker (`parent_invocation_id` + `depth`, depth-≥2 probe, cycle guard)
- [ ] #646 merged: real per-agent spend + `blended_rate` correction + `total_tokens` re-documented
- [ ] #648 merged: 30.7% decomposed, depth-≥2 attributed, residual coverage disclosed
- [ ] #536/#537/#538/#539 merged: all four Phase 2 overlay levers; `COST_MODEL.md` in sync
- [ ] #535 epic closed: DoD met; consolidated upstream genai-prices request filed/linked
- [ ] #543 merged: diff pricing-version stamp
- [ ] #82 merged: genai-prices pin currency + coverage detection
- [ ] #649 merged: dedup regression lock + CLAUDE.md 1.99x trap doc
- [ ] #652 merged: demo-diff.svg path scrub
- [ ] #653 merged: uv.lock self-version sync in release PR
- [ ] #548 merged LAST: docs catch-up; v0.11 caveat updated + v0.12.0 runtime caveat added
- [ ] Confirmed NOT pulled in: #639 (tier gap), and dogfood re-measurement #513/#469/#558/#600/#601/#637 (D058)
- [ ] D059 appended to `decisions.md`
- [ ] `pytest --cov` >80%; `ruff` clean; `mypy` clean; version bump to 0.12.0
```
