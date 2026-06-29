# PRD: Migrate base pricing to genai-prices (date-aware) + formalize the overlay seam

**Status:** Draft
**Date:** 2026-06-28
**Author:** PM Agent
**Epic:** Folds into #535 (`epic:cost-coverage`) as its foundational Phase 1. See "Epic structure decision" below.
**Decision log:** Recommends a new **D045** (genai-prices as the base-rate source). Draft text in §8 — to be appended to `decisions.md` by a human/dev (append-only; do NOT full-file rewrite, see #500).
**Supersedes:** #80, #81 (DIY time-series). Re-scopes #82 (auto-update). Folds #252 (model-id consolidation).

---

## 1. Problem

AgentFluent's live pricing is a hand-maintained Python dict (`_PRICING` in `src/agentfluent/analytics/pricing.py`): a single current rate per model, "Update this dict when Anthropic changes pricing." It has **no time dependency** — re-analyzing a six-month-old session prices it at today's rate, silently rewriting history.

[pydantic/genai-prices](https://github.com/pydantic/genai-prices) (MIT) is already the **chosen** upstream pricing source — it is named as such in `pricing.py` comments, `docs/COST_MODEL.md`, and the body of epic #535. But it is **referenced only in prose**. It is not a dependency (`pyproject.toml` has no entry), and the migration has never been implemented or ticketed. Epic #535's definition of done ("every lever implemented in the pricing **overlay** or documented") silently presumes an overlay-on-genai-prices architecture that does not yet exist.

This PRD scopes that missing foundation.

## 2. What "migrate to genai-prices" concretely means

1. **Add `genai-prices` as a pinned runtime dependency** in `pyproject.toml`.
2. **Build an adapter** that reads genai-prices' Anthropic price record (it models `input_mtok`, `output_mtok`, `cache_read_mtok`, a single 5m-equivalent `cache_write_mtok`, context-length `tiers`, and dated `constraint`s) and produces our existing `ModelPricing` shape.
3. **Replace `_PRICING`** as the source of base rates. Keep the public surface — `get_pricing()`, `compute_cost()`, `get_known_models()`, `DEFAULT_MODEL`, `SYNTHETIC_MODELS`, and `_ALIASES` resolution — working unchanged for all existing callers.
4. **Gain date-aware lookup natively.** genai-prices carries effective-dated constraints, so historical-rate selection comes from upstream rather than a hand-built time-series. This is exactly what #80/#81 set out to build by hand.
5. **Preserve the local overlay** for every lever genai-prices does NOT model: 1-hour cache write (already landed, #534), fast mode (#536), batch/priority (#537), data residency (#538), server-tool surcharges (#539). The adapter must not collapse the 1h cache dimension back onto the 5m rate (see the `ModelPricing` docstring).

The architecture is **base rates (upstream) ⊕ local overlay (gap levers)**. The migration delivers the base layer and the merge seam; #536–539 fill the overlay.

## 3. Epic structure decision

**Decision: fold the migration into #535 as its foundational Phase 1 — do NOT spawn a parallel epic.**

Rationale:
- #535's own body already names genai-prices as the upstream and the overlay as the gap-filler. The migration is the unstated prerequisite of #535's DoD, not a separate initiative.
- Keeping one cost epic makes the dependency graph legible: Phase 1 (substrate) blocks Phase 2 (overlay levers). The seam built in Phase 1 is the contract #536–539 implement against.
- Reuses the existing `epic:cost-coverage` label; avoids fragmenting the cost work across two epics a reader must cross-reference.

#535 is re-framed into two phases:
- **Phase 1 — Substrate (this PRD):** Stories A, B, C below.
- **Phase 2 — Overlay levers (existing):** #536, #537, #538, #539, each `blocked by` Story C.

## 4. Stories

### Story A — Add genai-prices dependency + adapter; replace `_PRICING` base source
**Type:** enhancement · **Priority:** high · **Deps:** none — **do first**

The core swap. No user-visible cost change at the default (no-timestamp) path: genai-prices' current Anthropic rates must match the rates `_PRICING` holds today.

Acceptance criteria:
- [ ] `genai-prices` added to `pyproject.toml` runtime deps, version pinned.
- [ ] An adapter maps the genai-prices Anthropic record onto `ModelPricing` (base `input`, `output`, `cache_creation_5m`, `cache_read`).
- [ ] The `cache_creation_1h` overlay dimension (#534) is preserved — the adapter derives/retains it; it is never collapsed onto the 5m rate.
- [ ] `_ALIASES`, `SYNTHETIC_MODELS`, `DEFAULT_MODEL`, `get_known_models()` continue to behave identically; alias and `[1m]`-suffix resolution still works.
- [ ] `get_pricing(model)` (no timestamp) returns current rates equal to the pre-migration `_PRICING` values for every model currently in the dict (regression lock test).
- [ ] `_PRICING` is removed (or reduced to a local-overlay-only residual if any model genai-prices lacks must be supplied locally — documented if so).
- [ ] **Folds #252:** promote `MODEL_HAIKU/SONNET/OPUS` canonical-id constants into `pricing.py` as part of the module rewrite; keep `_complexity.py`/`delegation.py` re-export chain green.
- [ ] All existing tests pass unchanged; `ruff` + `mypy` clean.

### Story B — Native date-aware lookup via genai-prices constraints + session timestamp
**Type:** enhancement · **Priority:** medium · **Deps:** Story A · **Supersedes #80, #81**

Acceptance criteria:
- [ ] `get_pricing(model, timestamp: datetime | None = None)`; `None`/omitted → latest (preserves all existing callers). With a timestamp → the rate active on that date, resolved from genai-prices' dated constraints.
- [ ] Session first-message timestamp is plumbed through the cost-aggregation path (the #81 outcome), backed by upstream constraints rather than a DIY structure.
- [ ] Missing/malformed timestamp → fall back to latest rate, debug-level log; never error or skip the session.
- [ ] CHANGELOG documents that historical-session costs may change (now reflect the rate in effect at session time). Cross-references the `diff` caveat (#543).
- [ ] Unit tests: pre-change date, post-change date, missing-timestamp fallback; an end-to-end fixture at a known date yields the historically correct dollar amount.
- [ ] `ruff` + `mypy` clean.

> Note: there is no real "opus $15/$75 → $5/$25" historical drop to seed (Opus 4.5 launched at $5/$25 — the #80/#81 premise was false). Use a genuine Anthropic dated constraint that genai-prices actually carries for the cross-date tests; do not fabricate the opus drop.

### Story C — Formalize the base ⊕ overlay seam + document it in COST_MODEL.md
**Type:** refactor · **Priority:** medium · **Deps:** Story A (may fold into A if small) · **Blocks #536–539**

Acceptance criteria:
- [ ] A single, documented merge point where upstream base rates combine with local overlay multipliers/surcharges (1h cache, fast mode, batch/priority, data residency, server-tool). This is the extension contract #536–539 implement against.
- [ ] The seam makes "is this lever upstream or overlay?" a one-line answer per lever, so retiring an overlay when genai-prices adds the lever is a localized change.
- [ ] `docs/COST_MODEL.md` overlay table updated to point at the seam and reflect the post-migration architecture.
- [ ] `ruff` + `mypy` clean; tests cover base-only, base+single-overlay, and base+stacked-overlay cases.

## 5. Reconciliation of the existing pricing cluster

| Issue | Was | Disposition |
|---|---|---|
| **#80** Time-series pricing structure (DIY, CodeFluent shape) | Hand-built `pricing.json` time-series + historical seed | **CLOSE — superseded.** genai-prices provides dated constraints natively; the DIY structure is obviated. False "opus price drop" premise confirmed invalid. Any local-only historical need folds into the overlay seam (Story C). |
| **#81** Session-timestamp historical cost | Date-aware cost on top of #80's DIY structure | **CLOSE — superseded by Story B.** The *user outcome* (date-aware historical cost) is retained; the *mechanism* changes to genai-prices constraints. |
| **#82** Automated pricing-update via GH Actions scraper | Scrape Anthropic's page → append to our dict | **RE-SCOPE, keep open, defer.** We no longer own a dict to scrape into — genai-prices maintains the dataset upstream. New shape: keep the genai-prices pin current (Dependabot/Renovate or a periodic bump check) + detect when upstream adds a lever we currently overlay (signal to retire that overlay). Much smaller; blocked until Phase 1 lands. |
| **#252** Consolidate `MODEL_*` canonical ids | Standalone refactor | **FOLD into Story A** (the module rewrite is the natural home). Close on Story A merge. |
| **#543** Stamp pricing-model version into diff snapshots | Flag cross-version cost deltas (from #534) | **Keep; sequence after Phase 1.** The "pricing version" becomes the genai-prices pin + overlay version. Story B *increases* its relevance (cross-date deltas join cross-version deltas). Target v0.12. |
| **#536–539** Overlay levers | Phase 2 of #535 | **Keep; mark blocked-by Story C.** Schedule after the seam exists. |

## 6. Sequencing & dependency graph

```
PHASE 1 — Substrate (new)
[Story A: dep + adapter + replace _PRICING + fold #252]  -- do first
        |
        +--> [Story B: date-aware lookup + session ts]   (supersedes #80, #81)
        +--> [Story C: overlay seam + COST_MODEL.md]      (blocks Phase 2)
                        |
PHASE 2 — Overlay levers (existing, all blocked by Story C)
                        +--> [#536 fast mode]      (High if used)
                        +--> [#539 server-tool]    (Medium; web search fully priceable)
                        +--> [#537 batch/priority] (Medium)
                        +--> [#538 data residency] (Low–Med)

ADJACENT
[#543 diff pricing-version stamp]  -- after Phase 1 (Story B raises its value)
[#82 re-scoped pin-bump/coverage]  -- after Phase 1
```

## 7. Recommended milestone targets

v0.10 is locked and discovery-led; this cost work is **not** in it and must not crash the cut. Recommended:

- **v0.11 — "Cost correctness" release:** Phase 1 (Stories A, B, C) + the two highest-value overlay levers, #536 (fast mode) and #539 (server-tool, at least web search). Makes v0.11 the release where reported dollars become both date-accurate and lever-aware.
- **v0.12:** #537 (batch/priority), #538 (data residency), #543 (diff version stamp), #82 (re-scoped). Closes #535's DoD.
- **#252:** rides Story A in v0.11.

This is a recommendation; final milestone composition is the human's call. **v0.11 does not exist yet — needs to be created.**

## 8. Draft D045 (for a human/dev to append to `decisions.md`)

> Append-only. Do NOT rewrite the whole file (#500 data-loss risk). PM agent cannot safely append (Write overwrites), so this is handed off as text.

```markdown
## D045: genai-prices as the base-rate source; local overlay for unmodeled levers

**Date:** 2026-06-28
**Context:** Live pricing was a hand-maintained `_PRICING` dict (single current rate per
model, no time dependency). genai-prices (pydantic/genai-prices, MIT) had been named as the
chosen upstream in comments/docs and in epic #535's body, but was never made a dependency or
ticketed. The earlier DIY plan (#80/#81: build our own time-series pricing.json on CodeFluent's
shape) rested on a false premise — the "opus $15/$75 → $5/$25 historical drop" never happened
(Opus 4.5 launched at $5/$25).
**Decision:** Depend on genai-prices for Anthropic base rates AND native date-aware (effective-
dated constraint) lookup. Supply every cost lever genai-prices does not model — 1h cache write,
fast mode, batch/priority, data residency, server-tool surcharges — via a local overlay merged
at a single documented seam. Supersede #80 and #81; re-scope #82 from "scrape Anthropic into our
dict" to "keep the genai-prices pin current + detect upstream coverage of our overlay levers."
**Rationale:**
- genai-prices provides effective-dated pricing natively — the exact capability #80/#81 tried to
  hand-build, without the maintenance burden or the append-only discipline #80 required.
- The base ⊕ overlay split keeps AgentFluent's published cost bar (every lever priced or
  documented) honest while delegating the volatile base-rate data to a maintained MIT dataset.
- Owning a scraper (#82) over a hand dict is obviated once the dataset is upstream.
**Trade-off:** Adds a runtime dependency and couples AgentFluent to genai-prices' Anthropic
coverage/cadence; mitigated by the overlay (we can always supply a lever locally) and by tracking
upstream coverage so overlays retire as genai-prices catches up.
```
